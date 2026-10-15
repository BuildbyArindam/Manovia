"""The retriever: top-k over the knowledge store, with a hard relevance floor.

This is the class that plugs the knowledge base into the chat pipeline. It
implements the chat-side :class:`~app.services.chat.retrieval.Retriever`
protocol, so the orchestrator neither knows nor cares that FAISS exists.

Three behaviours are the point:

* **The threshold is a floor, not a ranking nicety.** Chunks below
  ``RAG_MIN_SIMILARITY`` are dropped; if none survive, the retriever returns
  ``[]``. Forcing irrelevant context into a mental-health prompt is worse
  than providing none, and the system prompt tells the model what to do with
  an empty shelf (general supportive guidance).
* **Language is a metadata filter.** A query tagged ``hi`` retrieves chunks
  whose document is marked ``hi``. If the requested language has nothing
  above the floor, the retriever falls back to English once — an English
  document beats no document — and the caller can disable that with
  ``fallback_language=None``.
* **It runs the blocking parts off the event loop** (embedding and search in
  the threadpool), because a chat turn must not stall behind a CPU-bound
  encode.

The query the orchestrator hands over is already redacted; this module never
sees raw identifiers and has nothing to scrub.
"""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from starlette.concurrency import run_in_threadpool

from app.services.chat.retrieval import RetrievedChunk
from app.services.rag.base import Embedder, ScoredChunk
from app.services.rag.store import FaissVectorStore, Hit

_log = structlog.get_logger()


def normalize_language(language: str | None) -> str | None:
    """A wire locale ("en-GB", "hi", "und", "") → a corpus language or None."""
    if not language:
        return None
    base = language.strip().casefold().split("-", 1)[0]
    if base in {"en", "hi", "bn"}:
        return base
    return None


class KnowledgeRetriever:
    """Top-k retrieval over the ingested knowledge base, floored by similarity."""

    def __init__(
        self,
        store: FaissVectorStore,
        embedder: Embedder,
        *,
        min_similarity: float = 0.35,
        fallback_language: str | None = "en",
    ) -> None:
        if not 0.0 < min_similarity <= 1.0:
            raise ValueError("min_similarity must be in (0, 1]")
        self._store = store
        self._embedder = embedder
        self._min_similarity = min_similarity
        self._fallback_language = normalize_language(fallback_language)

    @property
    def is_empty(self) -> bool:
        """True when no store has been ingested; retrieve() will return []."""
        return self._store.count() == 0

    @property
    def min_similarity(self) -> float:
        return self._min_similarity

    async def rank(
        self,
        query: str,
        *,
        limit: int = 3,
        language: str | None = None,
        apply_floor: bool = True,
    ) -> list[ScoredChunk]:
        """The ranked candidates, best first, as :class:`ScoredChunk`.

        Day 35 added this next to ``retrieve`` because the retrieval eval and
        the abstain calibration both need the score behind the ordering: a
        failure case is only diagnosable with the number that beat the right
        answer, and a threshold can only be fitted on scores that exist for the
        queries where the honest answer is "nothing here".

        ``apply_floor=False`` returns those candidates *before* the similarity
        floor, which is the view the calibration has to see. The language
        fallback still keys off the floored result — "this language has nothing
        good enough" is exactly what it has always meant — and ``retrieve`` is
        this method with the floor on, so the two can never disagree about the
        ordering.
        """
        if limit < 1 or not query.strip() or self.is_empty:
            return []
        want = normalize_language(language)

        every, above = await self._search(query, limit=limit, language=want)
        if not above and want is not None and want != self._fallback_language:
            # The requested language has nothing strong enough; curated
            # English material is still better than nothing. Logged so the
            # corpus gap is visible rather than silent.
            _log.info("rag_language_fallback", requested=want, fallback=self._fallback_language)
            every, above = await self._search(query, limit=limit, language=self._fallback_language)

        hits = above if apply_floor else every
        return [self._to_scored(hit) for hit in self._dedupe(hits, limit)]

    async def retrieve(
        self, query: str, *, limit: int = 3, language: str | None = None
    ) -> list[RetrievedChunk]:
        """Up to ``limit`` passages above the similarity floor, best first.

        Empty is a normal answer: nothing matched well enough, and returning
        it is the retriever's job rather than the prompt's problem.
        """
        ranked = await self.rank(query, limit=limit, language=language)
        return [item.as_chunk() for item in ranked]

    async def _search(
        self, query: str, *, limit: int, language: str | None
    ) -> tuple[list[Hit], list[Hit]]:
        """Embed, search and filter by language; return (all hits, hits above floor).

        Both lists come back from one search so ``rank`` can serve the floored
        view to a chat turn and the unfloored one to an evaluation without
        paying for the encode twice.
        """
        vectors = await run_in_threadpool(self._embedder.embed, [query])
        if not vectors:
            # An embedder that answers nothing (e.g. the zero embedder over an
            # unusable store) means "no reference material", never a crash.
            return [], []
        where = {"language": language} if language else None
        # Over-fetch so a document with several strong chunks still leaves
        # room for `limit` *distinct* documents after the dedupe below.
        hits = await run_in_threadpool(self._store.query, vectors[0], k=limit * 3, where=where)
        return hits, [hit for hit in hits if hit.score >= self._min_similarity]

    @staticmethod
    def _dedupe(hits: Sequence[Hit], limit: int) -> list[Hit]:
        """One card per document, best chunk first, at most ``limit`` of them.

        A document's second-best chunk is never a different recommendation, and
        duplicate cards read as a bug.
        """
        kept: list[Hit] = []
        seen_docs: set[str] = set()
        for hit in hits:
            if hit.metadata.doc_id in seen_docs:
                continue
            seen_docs.add(hit.metadata.doc_id)
            kept.append(hit)
            if len(kept) >= limit:
                break
        return kept

    def _to_scored(self, hit: Hit) -> ScoredChunk:
        return ScoredChunk(
            source_id=hit.metadata.doc_id,
            title=hit.metadata.title,
            text=self._store.text_of(hit.id),
            score=hit.score,
            stage="dense",
            chunk_id=hit.id,
        )


__all__ = ["KnowledgeRetriever", "normalize_language"]
