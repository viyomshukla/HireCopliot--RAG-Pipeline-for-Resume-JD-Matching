import sys, json
sys.path.insert(0, ".")
from app.retrieval.embeddings import get_embedder
from app.retrieval.vector_store import VectorStore
from app.retrieval.keyword import BM25Index
from app.retrieval.fusion import HybridRetriever
from app.retrieval.reranker import RerankedRetriever, get_reranker
from pathlib import Path

chunks = []
for p in sorted(Path("data").glob("chunks*.json")):
    loaded = json.loads(p.read_text(encoding="utf-8"))
    print(f"{p.name}: {len(loaded)} chunks, "
          f"job_ids={ {c['metadata'].get('job_id') for c in loaded} }")
    chunks.extend(loaded)

store = VectorStore()
print(f"\nvector index: {store.count()} vectors, {len(store.candidate_ids())} candidates")

stack = RerankedRetriever(HybridRetriever(store, BM25Index(chunks)), get_reranker())
rid = sorted(store.candidate_ids())[0]
hits = stack.search_within_candidate("has taken a service to production", rid, 3)
print(f"\nretrieval for {rid}: {len(hits)} hits")
for h in hits:
    print(f"  {h['score']:.3f} above_floor={h.get('above_floor')} "
          f"{h['text'].split(chr(10))[0][:50]}")