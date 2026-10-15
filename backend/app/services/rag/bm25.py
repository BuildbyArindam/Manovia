"""BM25 over the ingested chunks: the lexical half of hybrid retrieval.

Day 13 shipped a dense-only retriever, and the Day-35 eval set says why that
costs something: exact-word queries ("box breathing how many counts", "5 4 3 2 1
technique", "pomodoro for revision") and every code-mixed Hinglish row are cases
where a cosine over a *semantic* embedding space has nothing to hold on to, while
a lexical score finds the shared token immediately. Fusing the two by rank
(:mod:`app.services.rag.fusion`) is what keeps both.

This is textbook Okapi BM25 — Robertson/Spärck Jones, with the Lucene-style IDF
that cannot go negative — plus one corpus-specific choice:

* **Title boost.** The curated topical words of this corpus live in its titles
  ("Sleep basics", "Grounding with your five senses"). The ingest already anchors
  the *dense* vectors on the title (``ingest.plan_ingest`` embeds it twice);
  BM25 gets the same anchor by repeating the title tokens ``title_boost`` times,
  so both halves of the hybrid agree about where a document's topic is stated.

Everything is built from the store's own chunks at load time — no extra artefact,
no extra file, nothing to keep in sync with the index. Tokenisation is the shared
:func:`app.services.rag.embedders.tokenize`, so BM25 and the TF-IDF embedder
cannot disagree about what a word is (they did, for Hindi and Bengali, until
Day 35; see ADR 0035).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.services.rag.base import ChunkMetadata
from app.services.rag.embedders import tokenize

__all__ = ["Bm25Index", "Bm25Match"]

#: Lucene-style IDF floor constant: ``ln(1 + (N - df + 0.5) / (df + 0.5))`` is
#: always non-negative, unlike the classic BM25 form, so a term that appears in
#: every document can never *subtract* relevance.
_IDF_FLOOR = 0.5


@dataclass(frozen=True)
class Bm25Match:
    """One BM25 result: the chunk id, its score, its metadata."""

    id: str
    score: float
    metadata: ChunkMetadata


class Bm25Index:
    """An exact, in-memory BM25 index over the store's chunks.

    The corpus is a few hundred chunks, so the honest implementation is the
    simple one: inverted lists, exact scoring over the terms a query actually
    contains, no approximate structure to be wrong about. A query costs one pass
    over its own tokens.
    """

    def __init__(
        self,
        *,
        k1: float = 1.2,
        b: float = 0.75,
        title_boost: int = 2,
    ) -> None:
        if not 0.0 < k1 <= 5.0:
            raise ValueError("k1 must be in (0, 5]")
        if not 0.0 <= b <= 1.0:
            raise ValueError("b must be in [0, 1]")
        if title_boost < 0:
            raise ValueError("title_boost must not be negative")
        self.k1 = k1
        self.b = b
        self.title_boost = title_boost
        #: term -> {chunk id: term frequency}
        self._postings: dict[str, dict[str, int]] = {}
        self._lengths: dict[str, int] = {}
        self._metadata: dict[str, ChunkMetadata] = {}
        self._ids: list[str] = []
        self._avg_length: float = 0.0

    # ------------------------------------------------------------- build #

    @classmethod
    def build(
        cls,
        chunks: Iterable[tuple[str, str, ChunkMetadata]],
        *,
        k1: float = 1.2,
        b: float = 0.75,
        title_boost: float = 2.0,
    ) -> Bm25Index:
        """Index ``(chunk id, chunk text, metadata)`` triples."""

        # round(), never a clamp: a negative boost is a mistake in the caller's
        # config, and __init__ refuses it. Silently turning -1 into 0 would
        # serve a different index than the one that was asked for.
        index = cls(k1=k1, b=b, title_boost=round(title_boost))
        repeats = index.title_boost
        for chunk_id, text, metadata in chunks:
            if chunk_id in index._lengths:
                raise ValueError(f"duplicate chunk id {chunk_id!r}")
            tokens = tokenize(text)
            for _ in range(repeats):
                tokens.extend(tokenize(metadata.title))
            index._ids.append(chunk_id)
            index._metadata[chunk_id] = metadata
            index._lengths[chunk_id] = len(tokens)
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            for token, frequency in counts.items():
                index._postings.setdefault(token, {})[chunk_id] = frequency
        total = sum(index._lengths.values())
        index._avg_length = total / len(index._lengths) if index._lengths else 0.0
        return index

    # ------------------------------------------------------------- query #

    def __len__(self) -> int:
        return len(self._ids)

    @property
    def vocabulary(self) -> Mapping[str, Mapping[str, int]]:
        """The inverted lists. Exposed for tests and for the reranker's IDF."""

        return self._postings

    def idf(self, term: str) -> float:
        """The IDF of ``term`` in this corpus: the formula, with no special cases.

        A term the corpus has never seen gets the ``df = 0`` value, which is the
        *maximum* — mathematically the most informative a term can be, and
        practically useless, since no chunk can match it. Callers that need
        "this corpus has no evidence about that word" should ask
        :attr:`vocabulary` instead; the reranker's IDF map is built from the
        vocabulary for exactly that reason, and gives an unseen query token a
        neutral weight rather than a maximal one.
        """

        documents = len(self._ids)
        if not documents:
            return 0.0
        frequency = len(self._postings.get(term, ()))
        return math.log(1.0 + (documents - frequency + _IDF_FLOOR) / (frequency + _IDF_FLOOR))

    def search(
        self,
        query: str,
        *,
        k: int = 20,
        where: dict[str, str] | None = None,
    ) -> list[Bm25Match]:
        """The ``k`` best chunks for ``query``, best first.

        Chunks with no query term in common score 0 and are not returned: BM25
        has nothing to say about them, and inventing a rank for silence is what
        makes a fused list lie.
        """

        if k < 1 or not self._ids:
            return []
        terms = set(tokenize(query))
        if not terms:
            return []
        documents = len(self._ids)
        scores: dict[str, float] = {}
        for term in terms:
            postings = self._postings.get(term)
            if not postings:
                continue
            frequency = len(postings)
            weight = math.log(1.0 + (documents - frequency + _IDF_FLOOR) / (frequency + _IDF_FLOOR))
            for chunk_id, term_frequency in postings.items():
                length = self._lengths[chunk_id]
                denominator = term_frequency + self.k1 * (
                    1.0 - self.b + self.b * (length / self._avg_length if self._avg_length else 1.0)
                )
                scores[chunk_id] = (
                    scores.get(chunk_id, 0.0)
                    + weight * (term_frequency * (self.k1 + 1.0)) / denominator
                )
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        matches: list[Bm25Match] = []
        for chunk_id, score in ranked:
            metadata = self._metadata[chunk_id]
            if where is not None and not _matches(metadata, where):
                continue
            matches.append(Bm25Match(id=chunk_id, score=score, metadata=metadata))
            if len(matches) == k:
                break
        return matches

    def score_pair(self, query: str, chunk_id: str) -> float:
        """The BM25 score of one specific chunk (0.0 when it shares no term)."""

        metadata = self._metadata.get(chunk_id)
        if metadata is None:
            return 0.0
        documents = len(self._ids)
        total = 0.0
        for term in set(tokenize(query)):
            postings = self._postings.get(term)
            if not postings or chunk_id not in postings:
                continue
            frequency = len(postings)
            weight = math.log(1.0 + (documents - frequency + _IDF_FLOOR) / (frequency + _IDF_FLOOR))
            term_frequency = postings[chunk_id]
            length = self._lengths[chunk_id]
            denominator = term_frequency + self.k1 * (
                1.0 - self.b + self.b * (length / self._avg_length if self._avg_length else 1.0)
            )
            total += weight * (term_frequency * (self.k1 + 1.0)) / denominator
        return total

    def chunk_ids(self) -> Sequence[str]:
        return tuple(self._ids)


def _matches(metadata: ChunkMetadata, where: dict[str, str]) -> bool:
    """The store's metadata filter, applied the same way (see ``store._matches``)."""

    for key, want in where.items():
        have: str | None
        if key == "language":
            have = metadata.language
        elif key == "doc_id":
            have = metadata.doc_id
        elif key == "tag":
            have = want if want in metadata.tags else None
        else:
            have = None
        if have != want:
            return False
    return True
