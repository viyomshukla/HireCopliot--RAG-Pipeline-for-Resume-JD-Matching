"""
Retrieval evaluation: does hybrid search and reranking actually help?

WHAT THIS ANSWERS
-----------------
Steps 8-12 built four components, each with a textbook claim attached:

    vector search   finds meaning, not just words
    BM25            catches exact terms vectors miss
    RRF fusion      combining beats either alone
    cross-encoder   reranking improves the order

None of those are currently tested on THIS data. They are what the literature
says, and they are probably true here -- but "probably" is not a number that can
go in a README, and it is not a number you can tune a threshold against.

This file runs every JD through five configurations and scores each against the
graded labels in jds.json:

    1. dense only        vectors, no keyword search
    2. keyword only      BM25, no vectors
    3. hybrid            both, fused with RRF
    4. hybrid + rerank   plus the cross-encoder
    5. + SQL prefilter   plus hard filters, if the database exists

THE METRICS, AND WHY EACH ONE
------------------------------
RECALL@k    of the candidates who SHOULD be in the top k, how many are? This is
            the shortlisting question: HR asked for 45 names, did the right
            people make the list at all.

nDCG@k      is the list in the right ORDER? Rewards putting a grade-3 candidate
            above a grade-2 one, and rewards position -- the same candidate at
            rank 2 counts far more than at rank 40. This is the metric that
            reflects ranking quality; recall only asks whether someone is
            somewhere in the list.

MRR         how far down is the first genuinely good match? A recruiter reads
            from the top, so a system whose first three picks are wrong is worse
            than its recall score suggests.

Reporting all three is deliberate. A configuration can win on recall and lose on
nDCG, which means it finds the right people but orders them badly -- a real and
common failure that a single number would hide.

TURNING CHUNK SCORES INTO CANDIDATE SCORES
-------------------------------------------
Retrieval returns CHUNKS. Ranking needs one number per CANDIDATE. This file uses
the MAXIMUM chunk score, i.e. the strength of their single best piece of
evidence.

That is a real modelling choice, not an obvious one:
    max   rewards the best evidence. A candidate with one perfect matching job
          scores full marks, which is usually what a recruiter means.
    mean  penalises long resumes -- someone with ten jobs is diluted by the
          eight that are irrelevant, which is clearly wrong.
    sum   rewards volume, so the longest resume wins regardless of fit.

max is the right default and is what step 15 should start from, but per-
requirement aggregation there will be more nuanced. Noting it here because the
choice affects every number below, and an evaluation whose modelling
assumptions are invisible is easy to over-trust.

A WARNING ABOUT READING THESE NUMBERS
--------------------------------------
The labels come from stated, checkable attributes -- skills, years, degree. They
measure whether retrieval finds the people who match on paper. They do NOT
measure whether the ranking is one a human recruiter would agree with. Good
scores here mean the machinery works, not that the hiring is good.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Callable, Optional

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

JDS_FILE = BASE / "data" / "ground_truth" / "jds.json"
CHUNKS_FILE = BASE / "data" / "chunks.json"
DB_FILE = BASE / "data" / "candidates.db"

RELEVANT_GRADE = 2      # grade >= this counts as "should be shortlisted"
CUTOFFS = [10, 20, 45]  # 45 because that is the shortlist size HR asked for


# ============================================================
# METRICS
# ============================================================

def recall_at_k(ranked: list[str], labels: dict, k: int) -> float:
    """Fraction of relevant candidates that made the top k."""
    relevant = {rid for rid, v in labels.items() if v["grade"] >= RELEVANT_GRADE}
    if not relevant:
        return float("nan")     # undefined, not zero -- see the JD calibration
    found = sum(1 for rid in ranked[:k] if rid in relevant)
    return found / len(relevant)


def ndcg_at_k(ranked: list[str], labels: dict, k: int) -> float:
    """Normalised discounted cumulative gain with graded relevance.

    gain = 2^grade - 1, so grade 3 (gain 7) is worth more than twice grade 2
    (gain 3). Ranking is not linear in usefulness: the best candidate at the top
    matters much more than a mediocre one slightly higher up.

    discount = 1/log2(rank+1), because a recruiter reads from the top and
    attention falls off steeply.

    Normalised by the IDEAL ordering, so 1.0 means "could not have been ranked
    better" and the number is comparable across JDs with different numbers of
    relevant candidates.
    """
    def dcg(grades: list[int]) -> float:
        return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades))

    got = [labels.get(rid, {}).get("grade", 0) for rid in ranked[:k]]
    ideal = sorted((v["grade"] for v in labels.values()), reverse=True)[:k]

    ideal_dcg = dcg(ideal)
    return dcg(got) / ideal_dcg if ideal_dcg > 0 else float("nan")


def mrr(ranked: list[str], labels: dict) -> float:
    """Reciprocal rank of the first relevant candidate."""
    for i, rid in enumerate(ranked, start=1):
        if labels.get(rid, {}).get("grade", 0) >= RELEVANT_GRADE:
            return 1.0 / i
    return 0.0


# ============================================================
# QUERY CONSTRUCTION
# ============================================================

def jd_to_query(spec: dict) -> str:
    """One search string per JD.

    Every configuration gets the SAME query, so the comparison isolates the
    retrieval method rather than accidentally measuring prompt engineering.

    Deliberately includes both the canonical skill names (which BM25 can match
    exactly) and the soft requirements (which are phrased unlike any resume, so
    only dense retrieval can bridge them). A query containing only one kind
    would rig the comparison in that retriever's favour.
    """
    parts = list(spec["must_have"]) + list(spec["nice_to_have"])
    parts += spec["soft_requirements"]
    return " ".join(parts)


# ============================================================
# RUNNING ONE CONFIGURATION
# ============================================================

def rank_candidates(
    query: str,
    resume_ids: list[str],
    search_fn: Callable[[str, str], list[dict]],
    score_key: str = "score",
) -> list[str]:
    """Scores every candidate and returns them best-first.

    Note this loops over EVERY candidate rather than taking a global top-k. That
    is the fan-out design: a candidate the retriever never looked at cannot be
    ranked, and a shortlist built from a global top-k silently omits people.
    """
    scored: list[tuple[float, str]] = []
    for resume_id in resume_ids:
        hits = search_fn(query, resume_id)
        # max over chunks: the strength of their best single piece of evidence.
        # See the module docstring for why not mean or sum.
        # score_key is passed explicitly per configuration rather than guessed.
        # A fused result carries BOTH "rrf_score" and an inherited "score" from
        # whichever retriever saw the chunk first, so falling back through keys
        # silently ranked the hybrid configuration by its dense scores -- and
        # produced numbers identical to dense-only, which is what gave it away.
        best = max(h.get(score_key, 0.0) or 0.0 for h in hits) if hits else 0.0
        scored.append((best, resume_id))

    scored.sort(reverse=True)
    return [rid for _, rid in scored]


def sql_prefilter(spec: dict) -> Optional[set[str]]:
    """Candidates passing the JD's hard requirements, from the database.

    Uses the EXTRACTED data, not the ground truth -- using candidates.json here
    would leak the answer key into the system under test and make the numbers
    meaningless.

    Returns None when the database is absent, so this configuration can be
    skipped rather than silently scoring zero.
    """
    if not DB_FILE.exists():
        return None

    import sqlite3
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row

    sql = """
        SELECT c.resume_id FROM candidates c
        WHERE c.total_experience_months >= ?
    """
    params: list = [spec["min_years"] * 12]

    for skill in spec["must_have"]:
        sql += """
          AND EXISTS (
            SELECT 1 FROM candidate_skills cs
            JOIN skills s ON s.id = cs.skill_id
            WHERE cs.candidate_id = c.id AND LOWER(s.canonical) = LOWER(?)
          )
        """
        params.append(skill)

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return {r["resume_id"] for r in rows}


# ============================================================
# EVALUATION
# ============================================================

def evaluate_config(
    name: str,
    search_fn: Callable[[str, str], list[dict]],
    jds: list[dict],
    resume_ids: list[str],
    use_prefilter: bool = False,
    score_key: str = "score",
) -> dict:
    totals = {f"recall@{k}": [] for k in CUTOFFS}
    totals.update({f"ndcg@{k}": [] for k in CUTOFFS})
    totals["mrr"] = []
    skipped = 0

    t0 = time.time()
    for spec in jds:
        labels = spec["labels"]
        if not any(v["grade"] >= RELEVANT_GRADE for v in labels.values()):
            skipped += 1
            continue

        pool = resume_ids
        if use_prefilter:
            allowed = sql_prefilter(spec)
            if allowed is None:
                return {"name": name, "unavailable": True}
            # Filtered-out candidates are APPENDED rather than deleted, so recall
            # is still measured over everyone. Deleting them would make the
            # prefilter look perfect by shrinking the denominator -- an easy way
            # to accidentally prove that filtering always helps.
            pool = [r for r in resume_ids if r in allowed]
            excluded = [r for r in resume_ids if r not in allowed]
        else:
            excluded = []

        ranked = rank_candidates(jd_to_query(spec), pool, search_fn, score_key) + excluded

        for k in CUTOFFS:
            totals[f"recall@{k}"].append(recall_at_k(ranked, labels, k))
            totals[f"ndcg@{k}"].append(ndcg_at_k(ranked, labels, k))
        totals["mrr"].append(mrr(ranked, labels))

    elapsed = time.time() - t0

    def mean(values: list[float]) -> float:
        clean = [v for v in values if not math.isnan(v)]
        return sum(clean) / len(clean) if clean else float("nan")

    return {
        "name": name,
        "seconds": elapsed,
        "n_jds": len(jds) - skipped,
        **{key: mean(values) for key, values in totals.items()},
    }


def print_table(results: list[dict]) -> None:
    usable = [r for r in results if not r.get("unavailable")]
    if not usable:
        print("no configurations ran")
        return

    columns = ["recall@10", "recall@20", "recall@45",
               "ndcg@10", "ndcg@45", "mrr"]

    header = f"{'configuration':22}" + "".join(f"{c:>11}" for c in columns) + f"{'secs':>8}"
    print("\n" + header)
    print("-" * len(header))

    best = {c: max(r[c] for r in usable if not math.isnan(r[c])) for c in columns}
    for r in usable:
        row = f"{r['name']:22}"
        for c in columns:
            value = r[c]
            # Mark the winner per column. With five configurations and six
            # metrics, the eye needs help finding the pattern.
            marker = "*" if abs(value - best[c]) < 1e-9 else " "
            row += f"{value:>10.3f}{marker}"
        row += f"{r['seconds']:>8.1f}"
        print(row)

    for r in results:
        if r.get("unavailable"):
            print(f"{r['name']:22} skipped (no candidates.db - "
                  f"run scripts/load_database.py)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default=None, help="embedding backend")
    parser.add_argument("--reranker", default=None, help="reranker backend")
    parser.add_argument("--depth", type=int, default=10,
                        help="chunks retrieved per candidate before reranking")
    args = parser.parse_args()

    for path in (JDS_FILE, CHUNKS_FILE):
        if not path.exists():
            sys.exit(f"missing {path}")

    from app.retrieval.embeddings import get_embedder
    from app.retrieval.vector_store import VectorStore
    from app.retrieval.keyword import BM25Index
    from app.retrieval.fusion import HybridRetriever
    from app.retrieval.reranker import RerankedRetriever, get_reranker

    jds = json.loads(JDS_FILE.read_text(encoding="utf-8"))
    chunks = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    resume_ids = sorted({c["metadata"]["resume_id"] for c in chunks})

    embedder = get_embedder(args.backend)
    store = VectorStore(embedder)
    if store.count() == 0:
        sys.exit("vector index is empty - run scripts/build_index.py first")

    bm25 = BM25Index(chunks)
    hybrid = HybridRetriever(store, bm25)
    reranker = get_reranker(args.reranker)
    stack = RerankedRetriever(hybrid, reranker, retrieve_depth=args.depth)

    print(f"embedder  : {embedder.name}")
    print(f"reranker  : {reranker.name}")
    print(f"corpus    : {len(chunks)} chunks, {len(resume_ids)} candidates, {len(jds)} JDs")

    configs = [
        ("dense only",
         lambda q, r: store.search_within_candidate(q, r, n_results=args.depth),
         False, "score"),
        ("keyword only (BM25)",
         lambda q, r: bm25.search_within_candidate(q, r, n_results=args.depth),
         False, "score"),
        # RRF ranks by POSITION, and inside one candidate every best chunk is
        # position 1 -- so 50 candidates collapse to about 10 distinct scores
        # and most of them tie. Kept in the table as the measured evidence for
        # why fusion selects chunks but does not score candidates.
        ("hybrid (RRF only)",
         lambda q, r: hybrid.search_within_candidate(q, r, n_results=args.depth),
         False, "rrf_score"),
        ("hybrid + rerank",
         lambda q, r: stack.search_within_candidate(q, r, n_results=3),
         False, "score"),
        ("+ SQL prefilter",
         lambda q, r: stack.search_within_candidate(q, r, n_results=3),
         True, "score"),
    ]

    results = [
        evaluate_config(name, fn, jds, resume_ids, use_prefilter=pre, score_key=key)
        for name, fn, pre, key in configs
    ]
    print_table(results)

    print("\nrecall@45 = of the candidates who should be shortlisted, how many were")
    print("ndcg@k    = were the BEST candidates put near the top, not just found")
    print("mrr       = how far down is the first genuinely good match")
    print("* marks the best configuration for that column")


if __name__ == "__main__":
    main()