"""
Cross-encoder reranking: the accuracy stage of retrieval.

BI-ENCODER vs CROSS-ENCODER
---------------------------
Everything so far has used a BI-encoder: the query and the chunk are embedded
SEPARATELY and then compared.

    "Kubernetes experience"        -> vector A
    "Containerised 12 services..." -> vector B      score = A . B

Because a chunk's vector does not depend on the query, all 538 can be computed
once, in advance. That is what makes search fast. The cost is that the two texts
never actually meet -- each is compressed into 384 numbers first, and whatever
the compression lost is gone before the comparison happens.

A CROSS-encoder puts both texts through the model TOGETHER:

    ["Kubernetes experience" [SEP] "Containerised 12 services..."] -> 0.94

Now attention can relate "Kubernetes" and "containerised" directly, word to
word. Substantially more accurate -- typically a large jump in ranking quality
on the same candidate set.

The cost is that NOTHING can be precomputed. Every query-document pair needs its
own forward pass. Scoring 538 chunks against 12 requirements is 6,456 passes,
minutes of compute, and it has to happen at query time. A cross-encoder cannot
be a search index.

Hence the standard pattern, which is the whole architecture of modern retrieval:

    RECALL stage      cheap, approximate, over everything    (BM25 + vectors)
    PRECISION stage   expensive, accurate, over ~10 items    (this file)

Retrieve broadly enough that the right answer is somewhere in the top 10, then
spend real compute deciding which of those 10 is actually best.

THE OTHER REASON THIS MATTERS HERE: CALIBRATION
------------------------------------------------
Cosine similarity has no meaningful absolute value. Unrelated professional
English scores 0.4-0.5 on any modern embedding model, so "is this chunk actually
about Kubernetes" cannot be answered by a threshold -- 0.45 is the floor, not a
weak match.

That is a real problem for this project. A candidate with no Kubernetes
experience still returns their best chunk at ~0.45, and without a way to say
"this is genuinely not relevant" every candidate gets partial credit for every
requirement, and the requirement stops discriminating between people.

Cross-encoders are trained on relevance LABELS, so their output is calibrated:
irrelevant pairs score near zero, relevant ones near one. A threshold works.
That is what makes `relevance_floor` below meaningful rather than a guess.

PRODUCTION vs LEARNING SHORTCUT
------------------------------
A small ONNX model on CPU, scoring a few hundred pairs per ranking run. Fine at
this scale. Production would batch on GPU, cache (query, chunk) pairs, and
probably use a larger reranker -- the accuracy gap between the small and base
models is real. The interface below makes that swap one string.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Optional

# Below this calibrated relevance, a chunk is treated as no evidence at all
# rather than weak evidence. See the module docstring: without a floor, every
# candidate scores something on every requirement and nothing discriminates.
# The right value comes from step 13's evaluation, not from intuition -- this is
# a starting point, not an answer.
DEFAULT_RELEVANCE_FLOOR = 0.15


def sigmoid(x: float) -> float:
    """Maps a raw model logit to 0-1.

    Cross-encoders output unbounded logits, not probabilities. Squashing them
    means scores from different rerankers are at least on the same scale, and a
    threshold expressed as 0.15 means something readable.
    """
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)          # avoids overflow for large negative x
    return e / (1.0 + e)


class Reranker(ABC):
    """Same shape as Embedder: one interface, swappable backends, so step 13 can
    compare rerankers by changing one string."""

    name: str = "unset"

    @abstractmethod
    def _score_pairs(self, query: str, texts: list[str]) -> list[float]:
        """Raw scores for (query, text) pairs, in input order."""

    def rerank(
        self,
        query: str,
        results: list[dict],
        top_n: Optional[int] = None,
        relevance_floor: Optional[float] = DEFAULT_RELEVANCE_FLOOR,
        drop_below_floor: bool = False,
        text_key: str = "text",
    ) -> list[dict]:
        """Re-scores retrieved results and returns them best-first.

        The original retrieval score is KEPT as `retrieval_score` rather than
        overwritten. Being able to see that a chunk was ranked 1st by hybrid
        retrieval and 7th by the reranker is how you find out whether reranking
        is helping or hurting -- and it is what step 13 measures.
        """
        if not results:
            return []

        texts = [r.get(text_key, "") for r in results]
        raw = self._score_pairs(query, texts)

        reranked = []
        for result, score in zip(results, raw):
            relevance = sigmoid(score)
            above = relevance_floor is None or relevance >= relevance_floor

            # The floor serves two DIFFERENT purposes and they need different
            # behaviour, which an earlier version conflated:
            #
            #   RANKING  a weak candidate must still get a score. Dropping their
            #            chunks makes them score 0, and every zero-scored
            #            candidate ties at the bottom in arbitrary order --
            #            which measurably cost recall.
            #   DISPLAY  a chunk below the floor must not be shown to a
            #            recruiter as evidence, because an irrelevant quote
            #            presented as proof is worse than no quote at all.
            #
            # So by default nothing is dropped and every result is tagged
            # `above_floor`. The caller decides: the ranker ignores the tag, the
            # evidence panel filters on it.
            if drop_below_floor and not above:
                continue
            reranked.append({
                "above_floor": above,
                **result,
                "retrieval_score": result.get("rrf_score", result.get("score")),
                "rerank_logit": float(score),
                "score": relevance,
            })

        reranked.sort(key=lambda r: r["score"], reverse=True)
        return reranked[:top_n] if top_n else reranked


class FastEmbedReranker(Reranker):
    """ONNX cross-encoder, local, no PyTorch. Matches the embedding backend.

    ms-marco-MiniLM-L-6-v2 is the small default: 6 transformer layers, fast on
    CPU, trained on MS MARCO passage ranking. bge-reranker-base is noticeably
    better and noticeably slower -- worth measuring at step 13 rather than
    guessing which the project needs.
    """

    def __init__(self, model_name: str = "Xenova/ms-marco-MiniLM-L-6-v2"):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.name = model_name
        self._model = TextCrossEncoder(model_name=model_name)

    def _score_pairs(self, query: str, texts: list[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(query, texts)]


class SentenceTransformerReranker(Reranker):
    """The PyTorch alternative. More model choices, much larger install."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base"):
        from sentence_transformers import CrossEncoder

        self.name = model_name
        self._model = CrossEncoder(model_name)

    def _score_pairs(self, query: str, texts: list[str]) -> list[float]:
        pairs = [[query, t] for t in texts]
        return [float(s) for s in self._model.predict(pairs)]


class NoOpReranker(Reranker):
    """Keeps the retrieval order unchanged. For offline tests and as a baseline.

    Useful beyond testing: step 13 compares retrieval WITH and WITHOUT reranking,
    and this is the 'without' arm. A reranker that does not measurably improve
    ranking is not worth its latency, and you can only know that by comparing.
    """

    def __init__(self):
        self.name = "noop"

    def _score_pairs(self, query: str, texts: list[str]) -> list[float]:
        # Descending logits preserve input order after the sort, and sigmoid
        # keeps them above any sensible floor.
        return [float(len(texts) - i) for i in range(len(texts))]


RERANKERS = {
    "fastembed": FastEmbedReranker,
    "sentence-transformers": SentenceTransformerReranker,
    "noop": NoOpReranker,
}

DEFAULT_RERANKER = "fastembed"
_instances: dict[str, Reranker] = {}


def get_reranker(backend: Optional[str] = None, **kwargs) -> Reranker:
    """Cached, because loading the model takes a second and holds memory."""
    backend = backend or DEFAULT_RERANKER
    if backend not in RERANKERS:
        raise ValueError(
            f"unknown reranker '{backend}'. choose from: {', '.join(RERANKERS)}"
        )
    if backend not in _instances:
        _instances[backend] = RERANKERS[backend](**kwargs)
    return _instances[backend]


class RerankedRetriever:
    """Hybrid retrieval followed by cross-encoder reranking.

    This is the complete retrieval stack, and the object the ranker will call
    once per candidate per requirement.
    """

    def __init__(
        self,
        hybrid_retriever,
        reranker: Optional[Reranker] = None,
        retrieve_depth: int = 10,
        relevance_floor: float = DEFAULT_RELEVANCE_FLOOR,
    ):
        self.hybrid = hybrid_retriever
        self.reranker = reranker or get_reranker()
        # Deeper than we return, on purpose. The reranker can only reorder what
        # retrieval handed it: if the best chunk sat at rank 9 in the hybrid
        # list and we only passed 3, no amount of reranking recovers it.
        # Too deep costs latency linearly, since every pair is a forward pass.
        self.retrieve_depth = retrieve_depth
        self.relevance_floor = relevance_floor

    def search_within_candidate(
        self, query: str, resume_id: str, n_results: int = 3
    ) -> list[dict]:
        candidates = self.hybrid.search_within_candidate(
            query, resume_id, n_results=self.retrieve_depth
        )
        return self.reranker.rerank(
            query, candidates,
            top_n=n_results,
            relevance_floor=self.relevance_floor,
        )

    def evidence_for(self, query: str, resume_id: str, n_results: int = 3) -> list[dict]:
        """Only chunks strong enough to show a recruiter. Use for the evidence
        panel; use search_within_candidate for scoring."""
        return [h for h in self.search_within_candidate(query, resume_id, n_results)
                if h["above_floor"]]

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        candidates = self.hybrid.search(query, n_results=self.retrieve_depth)
        return self.reranker.rerank(
            query, candidates,
            top_n=n_results,
            relevance_floor=self.relevance_floor,
        )