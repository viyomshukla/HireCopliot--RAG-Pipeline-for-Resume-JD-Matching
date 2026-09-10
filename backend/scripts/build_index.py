"""
Embeds chunks.json into the Chroma vector index.

    python scripts/build_index.py                 # build / refresh
    python scripts/build_index.py --reset         # wipe the collection first
    python scripts/build_index.py --backend hash  # offline, no model download
    python scripts/build_index.py --search "led a team through an ambiguous project"

Run --reset after any change to the chunker. Upsert only replaces chunk ids it
sees, so chunks that no longer exist would otherwise stay in the index forever
and keep turning up as evidence for text that is no longer in any resume.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.retrieval.embeddings import get_embedder  # noqa: E402
from app.retrieval.vector_store import VectorStore  # noqa: E402

CHUNKS_FILE = BASE / "data" / "chunks.json"


def show_search(store: VectorStore, query: str) -> None:
    print(f'\nsearch: "{query}"\n')
    for i, hit in enumerate(store.search(query, n_results=5), start=1):
        meta = hit["metadata"]
        print(f"  {i}. {hit['score']:.3f}  {meta.get('candidate_name', '?')}  "
              f"[{meta.get('section')}]")
        first_line = hit["text"].split("\n")[0][:88]
        print(f"     {first_line}")


def demo(store: VectorStore) -> None:
    """Shows the two search modes side by side, because the difference between
    them is the core idea of this project's ranking design."""
    print("\n" + "=" * 68)
    print("MODE 1: ONE SEARCH OVER EVERYONE  (what ordinary RAG does)")
    print("=" * 68)

    query = "built and deployed machine learning models in production"
    hits = store.search(query, n_results=10)
    print(f'\n"{query}"\n')
    people = []
    for hit in hits:
        name = hit["metadata"].get("candidate_name", "?")
        people.append(name)
        print(f"  {hit['score']:.3f}  {name[:24]:24} [{hit['metadata'].get('section')}]")

    distinct = len(set(people))
    total = len(store.candidate_ids())
    print(f"\n  10 chunks came from {distinct} distinct candidates.")
    print(f"  The other {total - distinct} of {total} got NO score at all -- and you")
    print(f"  cannot rank people the retriever never looked at.")

    print("\n" + "=" * 68)
    print("MODE 2: ONE SEARCH PER CANDIDATE  (what the ranker will do)")
    print("=" * 68)
    print(f'\n"{query}"\n')

    scored = []
    t0 = time.time()
    for resume_id in sorted(store.candidate_ids()):
        hits = store.search_within_candidate(query, resume_id, n_results=1)
        if hits:
            scored.append((hits[0]["score"], resume_id, hits[0]))
    elapsed = time.time() - t0

    scored.sort(reverse=True, key=lambda x: x[0])
    print(f"  every one of {len(scored)} candidates scored in {elapsed:.2f}s\n")
    for score, resume_id, hit in scored[:5]:
        name = hit["metadata"].get("candidate_name", "?")
        print(f"  {score:.3f}  {name[:24]:24} ({resume_id})")
        print(f"          evidence: {hit['text'].split(chr(10))[0][:74]}")

    worst = scored[-1]
    print(f"\n  lowest scoring candidate still gets a number: "
          f"{worst[0]:.3f} ({worst[1]})")
    print("  Note it is not near zero. Unrelated professional English scores")
    print("  0.3-0.5 on any modern embedding model, which is why retrieval uses")
    print("  RANKING and never a fixed similarity threshold.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="wipe the collection first")
    parser.add_argument("--backend", default=None, help="embedding backend to use")
    parser.add_argument("--search", default=None, help="run one search and exit")
    parser.add_argument("--no-demo", action="store_true")
    args = parser.parse_args()

    embedder = get_embedder(args.backend)
    store = VectorStore(embedder)
    print(f"embedder   : {embedder.name} ({embedder.dimensions} dims)")
    print(f"collection : {store.collection.name}")

    if args.search:
        if store.count() == 0:
            sys.exit("index is empty - build it first")
        show_search(store, args.search)
        return

    if not CHUNKS_FILE.exists():
        sys.exit(f"missing {CHUNKS_FILE} - run the chunker first")
    chunks = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))

    if args.reset:
        store.clear()
        print("collection cleared")

    t0 = time.time()
    n = store.index_chunks(chunks)
    elapsed = time.time() - t0

    print(f"\nindexed {n} chunks in {elapsed:.1f}s")
    print(f"vectors in collection : {store.count()}")
    print(f"candidates covered    : {len(store.candidate_ids())}")

    sections = Counter(c["metadata"]["section"] for c in chunks)
    print("sections              : " +
          ", ".join(f"{k} {v}" for k, v in sorted(sections.items())))

    if not args.no_demo:
        demo(store)


if __name__ == "__main__":
    main()