"""
Embeddings: text -> vector, behind one swappable interface.

WHAT AN EMBEDDING IS
--------------------
A model that maps text to a fixed-length list of floats, positioned so that
text with similar MEANING lands nearby. "Trained a PyTorch model" and
"Fine-tuned a Torch network" end up close together despite sharing no words;
"Built quarterly balance sheets" ends up far away despite sharing "built".

That is the whole reason this project can match a JD asking for "PyTorch"
against a resume that only ever writes "Torch".

WHY THE INTERFACE EXISTS
------------------------
Choosing an embedding model is not a matter of taste, it is a measurable
question -- and the answer depends on YOUR data, not on a leaderboard. Resume-
to-JD matching is a slightly unusual task and the best general model is not
automatically the best one here.

So no other file in this project may import an embedding library directly.
Everything goes through `get_embedder()`. Swapping models then means changing
one string, and step 13 can compare two models on the same evaluation set and
pick with evidence instead of vibes.

VECTORS FROM DIFFERENT MODELS ARE NOT COMPARABLE
------------------------------------------------
This catches people out. A vector from bge-small is meaningless to
text-embedding-3-small; they are different spaces entirely. Switching models
means RE-EMBEDDING EVERYTHING and rebuilding the index. That is why every
embedder here exposes `.name` and why that name gets stored next to the
vectors -- so a mismatch is detectable rather than silently producing nonsense
similarity scores.

WHY EVERY VECTOR IS NORMALISED
------------------------------
Cosine similarity measures the ANGLE between two vectors, not the distance,
because a vector's length mostly encodes how long the text was. "Python" and
"I have extensive experience with Python" point the same way but have different
magnitudes, and you want the direction.

If every vector is scaled to length 1 up front, cosine similarity becomes a
plain dot product -- one multiply-and-add instead of a division per comparison.
Every vector database does this internally. Doing it here means our scores mean
the same thing whichever store we use.

QUERY AND DOCUMENT EMBEDDINGS ARE NOT ALWAYS THE SAME
-----------------------------------------------------
BGE and E5 models are trained asymmetrically: a short question and a long
passage are different kinds of text, so queries get a prefix instruction and
documents do not. Forgetting the prefix costs real retrieval accuracy and is
invisible -- nothing errors, results just get worse. Hence separate
embed_query() and embed_documents() methods rather than one embed().

PRODUCTION vs LEARNING SHORTCUT
------------------------------
Production would run embeddings on a GPU or a dedicated inference service,
batch across requests, and version the index so a model change can be rolled
out without downtime. We run a small model on CPU and cache to disk, which is
enough for a few thousand chunks and teaches every concept that matters.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

BASE = Path(__file__).resolve().parents[2]
CACHE_DIR = BASE / "data" / "cache" / "embeddings"


# ============================================================
# HELPERS
# ============================================================

def normalise(vector: list[float]) -> list[float]:
    """Scales a vector to length 1 so cosine similarity becomes a dot product.

    Also casts to plain Python floats. Local backends return numpy arrays, and
    numpy's float32 is not JSON serialisable -- which surfaces later as a cache
    write failure rather than here, where the type actually comes from.
    """
    values = [float(v) for v in vector]
    length = math.sqrt(sum(v * v for v in values))
    if length == 0:
        return values
    return [v / length for v in values]

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Assumes both inputs are already normalised, which everything here is.

    If they were not, this would need dividing by both magnitudes. Normalising
    once at write time instead of twice per comparison is why that is worth
    doing up front.
    """
    return sum(x * y for x, y in zip(a, b))


# ============================================================
# THE INTERFACE
# ============================================================

class Embedder(ABC):
    """Every backend implements this and nothing else.

    `name` is not decoration: it is stored alongside the vectors so that an
    index built with one model is never queried with another.
    """

    name: str = "unset"
    dimensions: int = 0
    # Some models want an instruction prefixed to QUERIES only. See the module
    # docstring. Empty string for symmetric models.
    query_prefix: str = ""

    @abstractmethod
    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        """Backend-specific. Batching and caching are handled by the base class
        so no backend has to reimplement them."""

    def embed_documents(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        """Embeds chunks for storage. Cached, batched, normalised.

        Batching matters for API backends (one request instead of 500) and for
        local ones (the model runs faster on a batch than on single strings).
        """
        results: list[Optional[list[float]]] = [None] * len(texts)
        pending: list[tuple[int, str]] = []

        for i, text in enumerate(texts):
            cached = self._cache_read(text)
            if cached is not None:
                results[i] = cached
            else:
                pending.append((i, text))

        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            vectors = self._embed_raw([t for _, t in batch])
            for (i, text), vector in zip(batch, vectors):
                vector = normalise(list(vector))
                self._cache_write(text, vector)
                results[i] = vector

        return [r for r in results if r is not None]

    def embed_query(self, text: str) -> list[float]:
        """Embeds a search query. Applies the model's query prefix if it has one.

        Kept separate from embed_documents precisely so the prefix cannot be
        applied to documents by accident -- doing that silently degrades every
        result in the index.
        """
        return self.embed_documents([self.query_prefix + text])[0]

    # ---------- cache ----------
    # Embedding the same text twice is pure waste: the model is deterministic,
    # so the answer cannot change. This matters most while iterating on
    # chunking, where most chunks are unchanged between runs.

    def _cache_path(self, text: str) -> Path:
        key = hashlib.sha256(f"{self.name}|{text}".encode()).hexdigest()[:24]
        # Shard by the first two characters. One flat directory with 50,000
        # files is slow to list on every filesystem and painful on Windows.
        return CACHE_DIR / self.name.replace("/", "_") / key[:2] / f"{key}.json"

    def _cache_read(self, text: str) -> Optional[list[float]]:
        path = self._cache_path(text)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    def _cache_write(self, text: str, vector: list[float]) -> None:
        path = self._cache_path(text)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(vector))


# ============================================================
# LOCAL: fastembed  (the default)
# ============================================================

class FastEmbedEmbedder(Embedder):
    """Runs BGE locally through ONNX. No API key, no quota, no network.

    Chosen as the default for three reasons:
      * free and unlimited, which matters when free-tier LLM quotas are already
        the bottleneck in this project
      * resume text never leaves the machine, which is the right default for a
        hiring tool and a good line in the README
      * fastembed uses onnxruntime rather than PyTorch, so installing it is
        ~50MB instead of ~2GB

    bge-small-en-v1.5 is 384 dimensions. Smaller vectors mean less storage and
    faster search; the quality gap to a 768-dimension model on short technical
    text is small, and step 13 measures whether it matters here.
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", dimensions: int = 384):
        from fastembed import TextEmbedding

        self.name = model_name
        self.dimensions = dimensions
        # BGE was trained with this instruction on the query side only.
        self.query_prefix = "Represent this sentence for searching relevant passages: "
        self._model = TextEmbedding(model_name=model_name)

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        return [list(v) for v in self._model.embed(texts)]


# ============================================================
# LOCAL: sentence-transformers
# ============================================================

class SentenceTransformerEmbedder(Embedder):
    """The better-known local option. Needs PyTorch, so a much larger install.

    Worth having because it exposes far more models than fastembed, including
    ones fine-tuned for specific domains.
    """

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model = SentenceTransformer(model_name)
        self.dimensions = self._model.get_sentence_embedding_dimension()
        self.query_prefix = (
            "Represent this sentence for searching relevant passages: "
            if "bge" in model_name.lower() else ""
        )

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, show_progress_bar=False).tolist()


# ============================================================
# API: Gemini
# ============================================================

class GeminiEmbedder(Embedder):
    """Gemini's embedding endpoint, through the OpenAI-compatible URL.

    Free tier, but rate limited -- and this project already has one component
    fighting a quota. Kept as a comparison point for step 13 rather than a
    default.
    """
    def __init__(self, model_name: str = "text-embedding-004", dimensions: int = 768):
        from openai import OpenAI

        self.name = f"gemini/{model_name}"
        self.model_name = model_name
        self.dimensions = dimensions
        self._client = OpenAI(
            api_key=os.environ["GOOGLE_API_KEY"],
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model_name, input=texts)
        # Sort by index: the API does not guarantee response order matches input
        # order, and a silent reordering would attach every vector to the wrong
        # chunk -- a bug that produces plausible-looking nonsense.
        return [d.embedding for d in sorted(response.data, key=lambda d: d.index)]


# ============================================================
# API: OpenAI
# ============================================================

class OpenAIEmbedder(Embedder):
    def __init__(self, model_name: str = "text-embedding-3-small", dimensions: int = 1536):
        from openai import OpenAI

        self.name = f"openai/{model_name}"
        self.model_name = model_name
        self.dimensions = dimensions
        self._client = OpenAI()

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model_name, input=texts)
        return [d.embedding for d in sorted(response.data, key=lambda d: d.index)]


# ============================================================
# OFFLINE: deterministic hash embedder
# ============================================================

class HashEmbedder(Embedder):
    """NOT a real embedding model. A deterministic stand-in for tests.

    It hashes tokens into buckets, so identical text gives identical vectors and
    texts sharing words score above zero -- enough to exercise indexing,
    caching, storage and retrieval plumbing with no model download and no
    network.

    It has NO semantic understanding: "Torch" and "PyTorch" are unrelated to it.
    Never use it for anything you intend to measure quality with. It exists so
    that a test suite can run in CI without a 100MB download.
    """

    def __init__(self, dimensions: int = 128):
        self.name = "hash-test"
        self.dimensions = dimensions

    def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in text.lower().split():
                bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dimensions
                vector[bucket] += 1.0
            vectors.append(vector)
        return vectors


# ============================================================
# FACTORY
# ============================================================

EMBEDDERS = {
    "fastembed": FastEmbedEmbedder,
    "sentence-transformers": SentenceTransformerEmbedder,
    "gemini": GeminiEmbedder,
    "openai": OpenAIEmbedder,
    "hash": HashEmbedder,
}

DEFAULT_EMBEDDER = "fastembed"

_instances: dict[str, Embedder] = {}


def get_embedder(backend: Optional[str] = None, **kwargs) -> Embedder:
    """The only way the rest of the project obtains an embedder.

    Instances are cached because loading a local model takes a second or two and
    holds memory -- creating one per call would reload the weights every time.
    """
    backend = backend or os.getenv("EMBEDDING_BACKEND") or DEFAULT_EMBEDDER
    if backend not in EMBEDDERS:
        raise ValueError(
            f"unknown embedding backend '{backend}'. "
            f"choose from: {', '.join(EMBEDDERS)}"
        )
    if backend not in _instances:
        _instances[backend] = EMBEDDERS[backend](**kwargs)
    return _instances[backend]