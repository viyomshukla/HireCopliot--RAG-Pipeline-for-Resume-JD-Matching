"""
BM25 keyword search: the half of retrieval that embeddings are bad at.

WHY KEYWORD SEARCH AT ALL, WHEN WE HAVE EMBEDDINGS
---------------------------------------------------
Embeddings compress meaning, and compression loses detail. Specifically they
are weak at exactly the things a recruiter filters on:

  EXACT TERMS   "Kubernetes" and "Docker" embed close together because they are
                semantically similar -- but a JD asking for Kubernetes does not
                want Docker. Similar is not the same.
  RARE TOKENS   Product names, internal tools, certifications like "CKAD".
                Rare strings are precisely what a model saw least during
                training, so their vectors are least reliable.
  NUMBERS       "5 years" and "2 years" are nearly identical vectors. This is
                the failure that matters most for hiring.
  NEGATION      "no experience with Java" embeds close to "experience with Java".

BM25 gets all of those right, because it matches strings and does not care what
they mean. And it fails at exactly what embeddings are good at -- "Torch" will
never match "PyTorch", "led a team" will never match "managed engineers".

The two are complementary in a way that is genuinely unusual: their failure
modes barely overlap. That is why fusing them (step 11) beats either alone, and
why every serious search system runs both.

HOW BM25 SCORES
---------------
Three ideas on top of plain word counting:

  IDF          rare words carry more signal than common ones. A chunk matching
               "Kubernetes" tells you more than one matching "the".
  SATURATION   ten occurrences of "Python" is not ten times more relevant than
               one. TF-IDF scales linearly; BM25 flattens the curve with k1.
               This is its main improvement over TF-IDF.
  LENGTH NORM  long chunks contain more words by accident, so matches in them
               count for less. Controlled by b.

THE TOKENISER IS THE PART THAT ACTUALLY DECIDES QUALITY
--------------------------------------------------------
BM25 itself is a fixed formula. The only real engineering decision is how text
becomes tokens, and naive tokenisation quietly destroys technical search:

    "C++"      -> ["c"]        merges with C, C#, and the letter c
    "Node.js"  -> ["node"] ["js"]  or worse, ["node.js"] which never matches "node"
    ".NET"     -> ["net"]      merges with "network"
    "CI/CD"    -> ["ci"] ["cd"]

Our tokeniser keeps + # . inside tokens so C++, C#, .NET and Node.js survive,
AND additionally emits the sub-parts of dotted terms so that "node.js" matches a
query for "node". Recall matters more than precision here, because the reranker
downstream can discard a bad match but cannot recover a missed one.

THE SUBTLE BUG: IDF MUST COME FROM THE WHOLE CORPUS
----------------------------------------------------
The ranker searches within one candidate at a time. The tempting implementation
is a separate BM25 index per candidate -- and it is wrong. IDF computed over one
person's 11 chunks is meaningless: every word looks rare, so "the" scores like
"Kubernetes".

So there is ONE index over every chunk, and per-candidate search filters the
results AFTER scoring. Slightly more work, correct numbers.

PRODUCTION vs LEARNING SHORTCUT
------------------------------
rank_bm25 holds everything in memory and rebuilds from scratch. Fine for
thousands of chunks, hopeless for millions -- production uses an inverted index
(Elasticsearch, OpenSearch, or Postgres full-text) that supports incremental
updates. Building it by hand here is the point: the formula is visible instead
of hidden behind a query DSL.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Optional

from rank_bm25 import BM25Okapi

# Deliberately short. Aggressive stopword lists remove words that carry meaning
# in a technical context -- "C" is a language, "R" is a language, "IT" is a
# department. When in doubt, keep the token: BM25's IDF already down-weights
# common words automatically, which is most of what a stopword list is for.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "of", "on", "or", "that", "the", "to", "was",
    "were", "will", "with", "this", "these", "those", "we", "our",
}

# Keeps +, #, . and - inside tokens so C++, C#, .NET, Node.js and CI-CD survive.
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")


def tokenize(text: str) -> list[str]:
    """Text -> tokens, preserving technical terms.

    Also emits the parts of dotted/hyphenated tokens as extra tokens, so
    "node.js" is findable by a query for "node" and vice versa. This inflates
    term counts slightly, which is acceptable: a missed match cannot be
    recovered downstream, a spurious one can be discarded by the reranker.
    """
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text.lower()):
        token = raw.strip(".-")
        if not token or token in STOPWORDS:
            continue
        tokens.append(token)
        if "." in token or "-" in token:
            for part in re.split(r"[.\-]", token):
                if len(part) > 1 and part not in STOPWORDS:
                    tokens.append(part)
    return tokens


class BM25Index:
    """Keyword search over the same chunks the vector store holds.

    The method signatures deliberately mirror VectorStore, so step 11 can fuse
    two ranked lists without either side knowing what the other is.
    """

    def __init__(self, chunks: list[dict], k1: float = 1.5, b: float = 0.75):
        # k1 controls how quickly repeated terms stop adding score. b controls
        # how much a chunk's length penalises it. 1.5 / 0.75 are the standard
        # defaults and are a sensible starting point; step 13 can tune them
        # against the evaluation set rather than by intuition.
        self.chunks = chunks
        self.k1 = k1
        self.b = b

        # Index the DISPLAY text, not embedding_text. The "[Name | SECTION]"
        # prefix exists to help a vector find the right region of space; in a
        # keyword index it would make every chunk match its owner's name, so a
        # query containing a common first name would rank that candidate's
        # entire resume above everyone else's.
        self.corpus_tokens = [tokenize(c["text"]) for c in chunks]
        self.bm25 = BM25Okapi(self.corpus_tokens, k1=k1, b=b)

        # chunk_id -> position, so results can be traced back.
        self.index_by_id = {c["chunk_id"]: i for i, c in enumerate(chunks)}
        # resume_id -> positions, for per-candidate filtering after scoring.
        self.positions_by_resume: dict[str, list[int]] = {}
        for i, c in enumerate(chunks):
            self.positions_by_resume.setdefault(
                c["metadata"]["resume_id"], []
            ).append(i)

    # ---------- search ----------

    def search(
        self,
        query: str,
        n_results: int = 5,
        resume_id: Optional[str] = None,
    ) -> list[dict]:
        """Returns the best-matching chunks, highest score first.

        When resume_id is given, scoring still runs over the WHOLE corpus and
        only the results are filtered. See the module docstring -- IDF computed
        within one candidate would make every one of their words look rare.
        """
        tokens = tokenize(query)
        if not tokens:
            return []

        scores = self.bm25.get_scores(tokens)

        positions = (
            self.positions_by_resume.get(resume_id, [])
            if resume_id is not None
            else range(len(self.chunks))
        )

        ranked = sorted(positions, key=lambda i: scores[i], reverse=True)

        results = []
        for i in ranked[:n_results]:
            # A zero score means not one query token appears. Returning it would
            # be worse than returning nothing: the fusion step treats anything
            # in the list as a candidate, and an empty list is honest.
            if scores[i] <= 0:
                continue
            chunk = self.chunks[i]
            results.append({
                "chunk_id": chunk["chunk_id"],
                "text": chunk["text"],
                "metadata": chunk["metadata"],
                # Raw BM25 score. UNBOUNDED and corpus-dependent -- unlike cosine
                # similarity it has no fixed range, so it cannot be compared
                # across queries or averaged with a vector score. That is exactly
                # why fusion at step 11 uses RANKS rather than scores.
                "score": float(scores[i]),
            })
        return results

    def search_within_candidate(
        self, query: str, resume_id: str, n_results: int = 3
    ) -> list[dict]:
        return self.search(query, n_results=n_results, resume_id=resume_id)

    # ---------- inspection ----------

    def explain(self, query: str, chunk_id: str) -> list[tuple[str, float, int]]:
        """Per-term contribution to one chunk's score: (term, idf, count).

        Being able to say WHY a chunk matched is not a debugging luxury here --
        it is the same explainability the recruiter-facing evidence panel needs,
        and it is the thing a vector score can never give you.
        """
        i = self.index_by_id[chunk_id]
        doc = Counter(self.corpus_tokens[i])
        n_docs = len(self.corpus_tokens)

        out = []
        for term in set(tokenize(query)):
            n_containing = sum(1 for d in self.corpus_tokens if term in d)
            if n_containing == 0:
                continue
            idf = math.log(1 + (n_docs - n_containing + 0.5) / (n_containing + 0.5))
            out.append((term, round(idf, 3), doc.get(term, 0)))
        return sorted(out, key=lambda x: -x[1])

    def vocabulary_size(self) -> int:
        return len({t for doc in self.corpus_tokens for t in doc})

    def __len__(self) -> int:
        return len(self.chunks)