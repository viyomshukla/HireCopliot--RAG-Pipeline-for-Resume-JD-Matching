"""
Runs the disparate-impact audit over one or all job descriptions.

    python scripts/run_bias_audit.py --jd jd_001 --shortlist 10
    python scripts/run_bias_audit.py --all --shortlist 10

Needs candidates.db, the Chroma index, and parsed_jds.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.fairness.audit import audit_across_jds, audit_shortlist, format_audit  # noqa: E402
from app.ranking.jd_parser import ParsedJD                                       # noqa: E402
from app.ranking.scorer import CandidateScorer                                   # noqa: E402
from app.retrieval.embeddings import get_embedder                                # noqa: E402
from app.retrieval.fusion import HybridRetriever                                 # noqa: E402
from app.retrieval.keyword import BM25Index                                      # noqa: E402
from app.retrieval.reranker import RerankedRetriever, get_reranker               # noqa: E402
from app.retrieval.vector_store import VectorStore                               # noqa: E402

PARSED_JDS = BASE / "data" / "parsed_jds.json"
CHUNKS = BASE / "data" / "chunks.json"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--jd", default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--shortlist", type=int, default=10)
    p.add_argument("--backend", default=None)
    p.add_argument("--reranker", default=None)
    args = p.parse_args()

    if not PARSED_JDS.exists():
        sys.exit(f"missing {PARSED_JDS} - run scripts/run_jd_parsing.py")
    parsed = json.loads(PARSED_JDS.read_text(encoding="utf-8"))
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))

    store = VectorStore(get_embedder(args.backend))
    if store.count() == 0:
        sys.exit("vector index empty - run scripts/build_index.py")
    scorer = CandidateScorer(RerankedRetriever(
        HybridRetriever(store, BM25Index(chunks)),
        get_reranker(args.reranker), retrieve_depth=10,
    ))

    jd_ids = sorted(parsed) if args.all else [args.jd or sorted(parsed)[0]]
    results = {}
    for jd_id in jd_ids:
        if jd_id not in parsed:
            sys.exit(f"unknown jd '{jd_id}'. have: {', '.join(sorted(parsed))}")
        results[jd_id] = scorer.rank(ParsedJD.model_validate(parsed[jd_id]))

    if len(results) == 1:
        jd_id, ranked = next(iter(results.items()))
        print(f"=== {jd_id} ===\n")
        print(format_audit(audit_shortlist(ranked, args.shortlist),
                           args.shortlist, len(ranked)))
    else:
        print(audit_across_jds(results, args.shortlist))


if __name__ == "__main__":
    main()