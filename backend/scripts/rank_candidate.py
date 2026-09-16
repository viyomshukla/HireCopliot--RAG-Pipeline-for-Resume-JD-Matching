"""
Ranks candidates against a parsed job description.

    python scripts/rank_candidates.py --jd jd_001
    python scripts/rank_candidates.py --jd jd_001 --top 20
    python scripts/rank_candidates.py --jd jd_001 --backend hash --reranker noop

Needs: candidates.db (load_database.py), the Chroma index (build_index.py),
and parsed_jds.json (run_jd_parsing.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.ranking.jd_parser import ParsedJD                       # noqa: E402
from app.ranking.scorer import CandidateScorer, format_shortlist  # noqa: E402
from app.retrieval.embeddings import get_embedder                 # noqa: E402
from app.retrieval.fusion import HybridRetriever                  # noqa: E402
from app.retrieval.keyword import BM25Index                       # noqa: E402
from app.retrieval.reranker import RerankedRetriever, get_reranker  # noqa: E402
from app.retrieval.vector_store import VectorStore                # noqa: E402

PARSED_JDS = BASE / "data" / "parsed_jds.json"
CHUNKS = BASE / "data" / "chunks.json"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--jd", required=True, help="e.g. jd_001")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--backend", default=None)
    p.add_argument("--reranker", default=None)
    args = p.parse_args()

    if not PARSED_JDS.exists():
        sys.exit(f"missing {PARSED_JDS} - run scripts/run_jd_parsing.py first")
    parsed = json.loads(PARSED_JDS.read_text(encoding="utf-8"))
    if args.jd not in parsed:
        sys.exit(f"unknown jd '{args.jd}'. have: {', '.join(sorted(parsed))}")

    jd = ParsedJD.model_validate(parsed[args.jd])
    chunks = json.loads(CHUNKS.read_text(encoding="utf-8"))

    store = VectorStore(get_embedder(args.backend))
    if store.count() == 0:
        sys.exit("vector index empty - run scripts/build_index.py")
    retriever = RerankedRetriever(
        HybridRetriever(store, BM25Index(chunks)),
        get_reranker(args.reranker),
        retrieve_depth=10,
    )

    t0 = time.time()
    ranked = CandidateScorer(retriever).rank(jd)
    elapsed = time.time() - t0

    print(format_shortlist(ranked, jd, top_n=args.top))
    print(f"\nranked {len(ranked)} candidates in {elapsed:.1f}s")

    qualified = [c for c in ranked if c.passed_filter]
    if not qualified:
        print("\nWARNING: no candidate passed the hard filter. Usually the JD "
              "parser marked something hard that should be soft. Check "
              "required_skills above against your skills table.")
if __name__ == "__main__":
    main()