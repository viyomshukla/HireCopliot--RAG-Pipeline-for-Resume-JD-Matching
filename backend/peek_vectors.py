import sys
sys.path.insert(0, ".")
from app.retrieval.vector_store import VectorStore
from app.retrieval.embeddings import cosine_similarity

store = VectorStore()
print(f"collection : {store.collection.name}")
print(f"vectors    : {store.count()}")
print(f"candidates : {len(store.candidate_ids())}\n")

got = store.collection.get(limit=2, include=["embeddings", "documents", "metadatas"])
for cid, doc, meta, vec in zip(got["ids"], got["documents"],
                              got["metadatas"], got["embeddings"]):
    print(f"{cid}")
    print(f"  section : {meta.get('section')}   job_id: {meta.get('job_id')}")
    print(f"  text    : {doc.split(chr(10))[0][:60]}")
    print(f"  vector  : {len(vec)} dims  first 5: {[round(float(v), 4) for v in vec[:5]]}")
    print(f"  length  : {sum(float(v)**2 for v in vec) ** 0.5:.4f}   (1.0 = normalised)\n")

print("does the space make sense?\n")
for q in ["containerised deployment with kubernetes",
          "quarterly financial reporting and audit",
          "led a team of junior engineers"]:
    hit = store.search(q, n_results=1)[0]
    print(f'  "{q}"')
    print(f"     {hit['score']:.3f}  [{hit['metadata'].get('section')}]  "
          f"{hit['text'].split(chr(10))[0][:56]}\n")