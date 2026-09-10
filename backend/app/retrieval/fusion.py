"""
Reciprocal Rank Fusion: combining two rankings whose scores cannot be compared.

THE PROBLEM
-----------
Vector search and BM25 both return ranked chunks, and their scores live on
completely different scales:

    BM25    4.95, 4.55, 4.32, 0.0      unbounded, corpus- and query-dependent
    cosine  0.74, 0.68, 0.61, 0.31     roughly 0.2-0.9, never near zero

You cannot add or average these. BM25's 4.95 would swamp any weighted sum, and
its range shifts with every query, so a weight tuned on one query is wrong for
the next.

Normalising first (min-max, z-score) is the obvious fix and it is fragile: it
depends on the score DISTRIBUTION of that particular result set. A query where
everything scores similarly gets its tiny differences stretched to fill 0-1,
manufacturing confidence that is not there. A query with one runaway match
squashes everything else to zero.

THE FIX: IGNORE THE SCORES, USE THE POSITIONS
----------------------------------------------
RRF discards the numbers entirely and keeps only rank order:

    score(doc) = sum over lists of   1 / (k + rank_in_that_list)

Rank 1 contributes 1/61, rank 2 contributes 1/62, and so on with k=60.

Three properties fall out of that, and they are the whole reason RRF is the
default in Elasticsearch, Qdrant and Weaviate despite being four lines of code:

  SCALE FREE   the two systems never need a common unit. You could add a third
               retriever tomorrow with any scoring scheme at all.
  ROBUST       one retriever confidently ranking garbage first cannot dominate,
               because first place is worth 1/61 and not 4.95.
  AGREEMENT    a chunk in both lists at rank 3 (1/63 + 1/63 = 0.0317) beats a
               chunk at rank 1 in only one list (1/61 = 0.0164). Two weak
               independent signals outweigh one strong one -- which is exactly
               right when the two retrievers fail in different ways.

WHY k = 60
----------
k flattens the difference between top ranks. With k=60, rank 1 and rank 2 differ
by about 1.6%; with k=1 they differ by 33%. A large k says "being in the list at
all is most of the signal, exact position is a detail" -- appropriate when each
retriever is individually noisy, which ours are. 60 is the value from the
original paper (Cormack et al., 2009) and every major implementation uses it.
Worth tuning at step 13, but it is a genuinely good default.

WEIGHTING
---------
Real systems often weight one retriever above the other. We support it, but note
that weights break RRF's main virtue -- being parameter-free -- so they should be
set by MEASUREMENT at step 13, not by intuition. The honest default is 1.0 each.

PRODUCTION vs LEARNING SHORTCUT
------------------------------
Hand-written here on purpose. It is four lines, and understanding why it works
matters more than importing it. Production uses the same formula inside the
vector database, which is a good thing to know when someone asks what
"hybrid search" in a product actually does.
"""

from __future__ import annotations

from typing import Callable, Optional

DEFAULT_K = 60


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict]],
    k: int = DEFAULT_K,
    weights: Optional[list[float]] = None,
    id_key: str = "chunk_id",
) -> list[dict]:
    """Fuses several ranked lists into one. Highest fused score first.

    Each input list must already be sorted best-first. Nothing else about them
    matters -- not their score scale, not their length, not whether they
    overlap. That indifference is the point.
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("one weight per ranked list")

    fused: dict[str, dict] = {}

    for list_index, (results, weight) in enumerate(zip(ranked_lists, weights)):
        for rank, item in enumerate(results, start=1):
            key = item[id_key]
            if key not in fused:
                # Keep the first item we saw for this id, so text and metadata
                # survive. The two retrievers return the same chunk content, so
                # whichever arrives first is fine.
                fused[key] = {
                    **item,
                    "rrf_score": 0.0,
                    # Where each retriever placed it. Kept because it is the
                    # cheapest possible explanation of a fused ranking: "ranked
                    # 2nd by keyword, 7th by semantic" tells you far more than
                    # a fused score of 0.028.
                    "ranks": {},
                    "source_scores": {},
                }
            fused[key]["rrf_score"] += weight * (1.0 / (k + rank))
            fused[key]["ranks"][list_index] = rank
            fused[key]["source_scores"][list_index] = item.get("score")

    return sorted(fused.values(), key=lambda x: x["rrf_score"], reverse=True)


class HybridRetriever:
    """Vector search + BM25, fused. The interface the ranker actually calls.

    Deliberately holds both retrievers rather than inheriting from either: it
    is a coordinator, not a kind of search. Adding a third retriever later means
    adding a list entry here and nothing else.
    """

    def __init__(
        self,
        vector_store,
        bm25_index,
        k: int = DEFAULT_K,
        vector_weight: float = 1.0,
        keyword_weight: float = 1.0,
    ):
        self.vector_store = vector_store
        self.bm25 = bm25_index
        self.k = k
        self.weights = [vector_weight, keyword_weight]

    def search(
        self,
        query: str,
        n_results: int = 5,
        candidates_per_source: Optional[int] = None,
    ) -> list[dict]:
        """Runs both retrievers and fuses.

        candidates_per_source is intentionally LARGER than n_results, defaulting
        to 3x. Fusion can only rerank what it is given: a chunk sitting at rank 8
        in both lists is a strong hybrid match, but if each retriever only
        returned its top 5 that chunk was never a candidate. Retrieving deep and
        cutting after fusion is what makes the fusion worth doing.
        """
        depth = candidates_per_source or max(n_results * 3, 10)

        vector_hits = self.vector_store.search(query, n_results=depth)
        keyword_hits = self.bm25.search(query, n_results=depth)

        fused = reciprocal_rank_fusion(
            [vector_hits, keyword_hits], k=self.k, weights=self.weights
        )
        return fused[:n_results]

    def search_within_candidate(
        self,
        query: str,
        resume_id: str,
        n_results: int = 3,
        candidates_per_source: Optional[int] = None,
    ) -> list[dict]:
        """The call the ranker makes: best evidence for one requirement, from
        one candidate, using both retrievers."""
        depth = candidates_per_source or max(n_results * 3, 10)

        vector_hits = self.vector_store.search_within_candidate(
            query, resume_id, n_results=depth
        )
        keyword_hits = self.bm25.search_within_candidate(
            query, resume_id, n_results=depth
        )

        fused = reciprocal_rank_fusion(
            [vector_hits, keyword_hits], k=self.k, weights=self.weights
        )
        return fused[:n_results]


def explain_fusion(item: dict) -> str:
    """One line describing how a chunk earned its fused position.

    Retrieval that cannot be explained is retrieval you cannot debug, and in a
    hiring tool it is also retrieval you cannot defend.
    """
    parts = []
    names = {0: "semantic", 1: "keyword"}
    for list_index, rank in sorted(item["ranks"].items()):
        score = item["source_scores"].get(list_index)
        label = names.get(list_index, f"source{list_index}")
        parts.append(f"{label} #{rank}" + (f" ({score:.2f})" if score is not None else ""))
    missing = [names[i] for i in names if i not in item["ranks"]]
    for name in missing:
        parts.append(f"{name} —")
    return f"rrf {item['rrf_score']:.4f}  [" + ", ".join(parts) + "]"