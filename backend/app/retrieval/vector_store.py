"""
Vector store: chunks -> Chroma, with the metadata that makes ranking possible.

WHAT A VECTOR STORE IS
----------------------
A database whose primary operation is "find the k vectors closest to this one".
Comparing a query against every stored vector works fine at our scale (538
chunks is nothing), but at a million vectors a linear scan is too slow, so real
vector stores build an approximate index -- Chroma uses HNSW, a navigable graph
that trades a little recall for a very large speedup. You get the same API
either way, which is why starting small costs nothing.

THE DECISION THAT MATTERS: METADATA
-----------------------------------
Chroma can filter by metadata BEFORE searching. That single feature is what
makes this project's ranking design work.

Recall the problem: HR uploads 100 resumes and wants the top 45. Ordinary RAG
runs one query over everything and returns the 20 best chunks -- which might
come from only 18 candidates. The other 82 get no score at all, and you cannot
rank people the retriever never looked at.

So instead we search ONCE PER CANDIDATE, filtered to that candidate's chunks:

    where={"resume_id": "resume_047"}

The vector store stops being a filter over people and becomes a way to find the
best evidence INSIDE one person's resume. Everyone gets scored. That is why
every chunk carries resume_id, and why the chunker was made to attach it.

WHY WE PASS OUR OWN VECTORS
---------------------------
Chroma will happily embed text for you with its own default model. We never let
it, because then the embedding model would be chosen in two places -- ours and
Chroma's -- and step 13 could not compare models by changing one string. The
embedder stays the single source of truth; Chroma only stores what it is given.

WHAT WE EMBED vs WHAT WE STORE
------------------------------
`embedding_text` (with the "[Name | SECTION]" prefix) is what gets vectorised.
`text` (clean) is what gets stored and shown to the recruiter. They are
deliberately different: the prefix helps the vector find the right chunk, but a
recruiter should never see system labels in their evidence panel.

THE COLLECTION NAME CONTAINS THE MODEL NAME
-------------------------------------------
Vectors from different embedding models are not comparable -- they live in
different spaces. Querying a bge-small index with an OpenAI query vector does
not error; it silently returns nonsense ranked by meaningless numbers. Putting
the model in the collection name makes that mistake impossible: switch models
and you get a different, empty collection rather than corrupt results.

PRODUCTION vs LEARNING SHORTCUT
------------------------------
Chroma running in-process against a local directory. Production would use a
server-mode vector database -- Qdrant is the planned upgrade -- for real
concurrency, richer metadata filtering, and an index that survives a redeploy.
The API surface we use here is small and deliberately generic, so that swap is
mechanical.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Optional

import chromadb
from chromadb.config import Settings

from app.retrieval.embeddings import Embedder, get_embedder

BASE = Path(__file__).resolve().parents[2]
CHROMA_DIR = BASE / "data" / "chroma"

# Metadata keys we copy from the chunk onto the vector. Only fields that will
# actually be FILTERED or DISPLAYED belong here -- metadata is duplicated per
# vector, so storing the whole chunk record would bloat the index for nothing.
METADATA_FIELDS = [
    "resume_id", "candidate_name", "section",
    "source_type", "item_index", "char_count",
]


def collection_name_for(embedder: Embedder) -> str:
    """One collection per embedding model. See the module docstring."""
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", embedder.name)
    # Chroma requires 3-63 chars, starting and ending alphanumeric.
    return f"chunks_{safe}"[:63].rstrip("_-")


class VectorStore:
    """Thin wrapper over a Chroma collection.

    Thin on purpose. Every method here maps to something Qdrant also does, so
    the eventual swap touches this file and nothing else.
    """

    def __init__(self, embedder: Optional[Embedder] = None):
        self.embedder = embedder or get_embedder()
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=str(CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name_for(self.embedder),
            # Cosine, to match the normalised vectors the embedder produces.
            # Chroma's default is squared L2, which ranks differently and would
            # quietly disagree with our own cosine_similarity() helper.
            metadata={"hnsw:space": "cosine", "embedding_model": self.embedder.name},
        )

    # ---------- writing ----------

    def index_chunks(self, chunks: list[dict], batch_size: int = 200) -> int:
        """Embeds and stores chunks. Safe to re-run.

        chunk_id is used as the vector id, so upsert replaces rather than
        duplicates. Without that, re-running after a chunker change would leave
        the OLD chunks in the index alongside the new ones, and retrieval would
        quietly return stale evidence.
        """
        if not chunks:
            return 0

        vectors = self.embedder.embed_documents(
            [c["embedding_text"] for c in chunks]
        )

        total = 0
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            batch_vectors = vectors[start:start + batch_size]

            self.collection.upsert(
                ids=[c["chunk_id"] for c in batch],
                embeddings=batch_vectors,
                documents=[c["text"] for c in batch],
                metadatas=[self._clean_metadata(c["metadata"]) for c in batch],
            )
            total += len(batch)
        return total

    @staticmethod
    def _clean_metadata(meta: dict) -> dict:
        """Chroma metadata values must be str, int, float or bool.

        Lists and None are rejected, and a None slipping through raises at
        insert time on one record in the middle of a batch -- so they are
        filtered here rather than debugged there.
        """
        out: dict[str, Any] = {}
        for key in METADATA_FIELDS:
            value = meta.get(key)
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                out[key] = value
            else:
                out[key] = str(value)
        return out

    def clear(self) -> None:
        """Deletes the collection. Needed when the chunker changes shape --
        upsert only replaces ids it sees, so chunks that no longer exist would
        otherwise linger forever."""
        self.client.delete_collection(collection_name_for(self.embedder))
        self.collection = self.client.get_or_create_collection(
            name=collection_name_for(self.embedder),
            metadata={"hnsw:space": "cosine", "embedding_model": self.embedder.name},
        )

    # ---------- reading ----------

    def search(
        self,
        query: str,
        n_results: int = 5,
        where: Optional[dict] = None,
    ) -> list[dict]:
        """Semantic search, optionally filtered by metadata.

        Note embed_QUERY, not embed_documents: BGE wants an instruction prefix
        on the query side only. Using the wrong one here degrades every search
        with no error to tell you.
        """
        query_vector = self.embedder.embed_query(query)
        return self._query(query_vector, n_results, where)

    def search_within_candidate(
        self, query: str, resume_id: str, n_results: int = 3
    ) -> list[dict]:
        """The call the ranker makes, once per candidate per requirement.

        This is the fan-out: rather than one search over everyone, we ask each
        candidate individually "what is your best evidence for this?". Everyone
        gets a score, and the chunk that comes back is the citation.
        """
        return self.search(query, n_results=n_results, where={"resume_id": resume_id})

    def _query(self, vector: list[float], n_results: int, where: Optional[dict]) -> list[dict]:
        raw = self.collection.query(
            query_embeddings=[vector],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        if not raw["ids"] or not raw["ids"][0]:
            return []

        results = []
        for chunk_id, doc, meta, distance in zip(
            raw["ids"][0], raw["documents"][0],
            raw["metadatas"][0], raw["distances"][0],
        ):
            results.append({
                "chunk_id": chunk_id,
                "text": doc,
                "metadata": meta,
                # Chroma returns DISTANCE; with cosine space that is 1 - similarity.
                # Converting here means every score in this project means the same
                # thing: higher is better. Mixed conventions are how a reranker
                # ends up sorted backwards.
                "score": 1.0 - distance,
            })
        return results

    # ---------- inspection ----------

    def count(self) -> int:
        return self.collection.count()

    def candidate_ids(self) -> set[str]:
        """Every resume_id present in the index.

        Used to check the index covers every candidate in SQL. A candidate
        missing from the index would silently score zero on every soft
        requirement and vanish from the shortlist for no visible reason.
        """
        got = self.collection.get(include=["metadatas"])
        return {m.get("resume_id") for m in got["metadatas"] if m.get("resume_id")}

    def peek(self, n: int = 3) -> Iterable[dict]:
        got = self.collection.get(limit=n, include=["documents", "metadatas"])
        for chunk_id, doc, meta in zip(got["ids"], got["documents"], got["metadatas"]):
            yield {"chunk_id": chunk_id, "text": doc, "metadata": meta}