"""Hybrid retrieval: BM25 + dense, fused by rank, optionally reranked.

This is the retriever ``RETRIEVER=hybrid`` and ``RETRIEVER=hybrid_rerank`` build.
It implements the same Day-11 :class:`~app.services.chat.retrieval.Retriever`
protocol as :class:`~app.services.rag.retriever.KnowledgeRetriever`, so the chat
orchestrator does not know which one it has — that is the point of the seam, and
it is what makes ``RETRIEVER=baseline`` a rollback rather than a revert.

The pipeline, in order:

1. **Dense** — the store's own vectors, cosine, language-filtered like Day 13.
2. **Lexical** — :class:`~app.services.rag.bm25.Bm25Index` over the same chunks.
3. **Fusion** — reciprocal rank fusion with a weight tuned on the Day-35 dev
   split (:mod:`app.services.rag.fusion`).
4. **Rerank** (``hybrid_rerank`` only) — a
   :class:`~app.services.rag.rerank.CrossEncoder` over the top
   ``rerank_candidates`` documents.
5. **Abstain** — candidates below the calibrated floor are dropped, and an empty
   list is a normal answer (the prompt tells the model what to do with an empty
   shelf). The floor is a *calibrated* number in ``[0, 1]``: the fused
   confidence, or the reranker's probability, both fitted on the calibration
   split by ``ml/src/manovia_ml/retrieval/tuning.py``.
6. **One card per document**, then the language fallback: if the requested
   language produced nothing above the floor, curated English is tried once —
   the same behaviour as Day 13, logged the same way.

Latency is the trade this class makes, so it is measured rather than argued:
the Day-35 report quotes p50/p95 per setting on one CPU. Blocking work runs in
the threadpool, as in the baseline retriever, because a chat turn must not stall
behind a CPU-bound encode.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from starlette.concurrency import run_in_threadpool

from app.services.chat.retrieval import RetrievedChunk
from app.services.rag.base import CHUNK_ID_SEP, Embedder, ScoredChunk
from app.services.rag.bm25 import Bm25Index
from app.services.rag.fusion import FusedCandidate, reciprocal_rank_fusion
from app.services.rag.rerank import (
    CrossEncoder,
    NullCrossEncoder,
    RerankCandidate,
    saturate_bm25,
)
from app.services.rag.retriever import normalize_language
from app.services.rag.store import FaissVectorStore, Hit

_log = structlog.get_logger()

#: The two dense/lexical systems the fusion knows by name.
DENSE_SYSTEM = "dense"
BM25_SYSTEM = "bm25"


@dataclass(frozen=True)
class HybridTuning:
    """The calibrated knobs, in one place, with the defaults the report used.

    ``abstain_score`` is the number the whole "no good answer" story rests on,
    so it is explicit rather than derived: a deployment that does not set it
    gets 0.0 (answer whenever anything fused) and the report says so.
    """

    #: Weight of the dense system in the fusion (the lexical system gets
    #: ``1 - dense_weight`` after renormalisation). Tuned on the dev split.
    dense_weight: float = 0.5
    #: RRF smoothing constant. Larger flattens the difference between ranks.
    fusion_k: int = 60
    #: How many chunk ids each system contributes to the fusion.
    candidates: int = 20
    #: How many *documents* the reranker sees.
    rerank_candidates: int = 20
    #: Floor on the calibrated score. The default is deliberately conservative
    #: rather than 0.0: at 0.0 a candidate with *no* evidence at all still
    #: counts as an answer, which both serves an empty card and suppresses the
    #: language fallback (``0.0 >= 0.0``). A deployment without a fitted
    #: tuning.json therefore abstains more, not less.
    abstain_score: float = 0.3
    #: Cosine that counts as full dense evidence in :func:`fused_evidence`.
    #: Derived from the embedder's own recommended floor (``2 x floor``) unless
    #: the tuning artefact says otherwise: TF-IDF cosines on this corpus live in
    #: 0.05-0.3 while a sentence-transformer's live in 0.2-0.8, and a mix that
    #: ignores that is a mix where one system always wins.
    dense_scale: float = 0.24
    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    title_boost: float = 2.0


class HybridKnowledgeRetriever:
    """Top-k over fused lexical + dense evidence, with a calibrated abstain floor."""

    def __init__(
        self,
        store: FaissVectorStore,
        embedder: Embedder,
        *,
        bm25: Bm25Index | None = None,
        reranker: CrossEncoder | None = None,
        tuning: HybridTuning | None = None,
        min_similarity: float = 0.0,
        fallback_language: str | None = "en",
        language_filter: bool = True,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._tuning = tuning or HybridTuning()
        self._reranker: CrossEncoder = reranker or NullCrossEncoder()
        self._bm25 = bm25 if bm25 is not None else self._build_bm25(store, self._tuning)
        self._min_similarity = min_similarity
        self._fallback_language = normalize_language(fallback_language)
        self._language_filter = language_filter

    @staticmethod
    def _build_bm25(store: FaissVectorStore, tuning: HybridTuning) -> Bm25Index:
        """Index the store's own chunks — no second artefact to keep in sync."""

        chunks = store.chunks()
        return Bm25Index.build(
            ((chunk.id, chunk.text, chunk.metadata) for chunk in chunks),
            k1=tuning.bm25_k1,
            b=tuning.bm25_b,
            title_boost=tuning.title_boost,
        )

    @property
    def is_empty(self) -> bool:
        return self._store.count() == 0

    @property
    def min_similarity(self) -> float:
        """The dense floor, kept for interface parity with the baseline.

        The hybrid's real floor is :attr:`abstain_score`: it is applied to the
        calibrated fused/reranked score, not to a cosine, because a cosine from
        one system is not comparable to a rank agreed by two.
        """

        return self._min_similarity

    @property
    def abstain_score(self) -> float:
        return self._tuning.abstain_score

    @property
    def tuning(self) -> HybridTuning:
        return self._tuning

    @property
    def setting(self) -> str:
        """``hybrid`` or ``hybrid_rerank[:untrained]`` — what a report quotes."""

        if isinstance(self._reranker, NullCrossEncoder):
            return "hybrid"
        return f"hybrid_rerank:{self._reranker.name}"

    async def rank(
        self,
        query: str,
        *,
        limit: int = 3,
        language: str | None = None,
        apply_floor: bool = True,
    ) -> list[ScoredChunk]:
        """The ranked candidates, best first, as :class:`ScoredChunk`.

        ``apply_floor=False`` returns them *before* the abstain floor, which is
        the view the calibration has to see: the floor is a decision, and a
        decision can only be tuned on the scores it was made from. ``retrieve``
        is this method with the floor on, so the two cannot disagree about order.

        The language fallback keys off the *floored* result, not off emptiness,
        and that distinction is load-bearing: the corpus is 34 English documents
        against 3 Hindi and 3 Bengali ones, and the Day-35 eval set says most of
        the relevant material for a Hindi or Bengali query is an English
        document. A fallback that fired only on an empty candidate list would
        leave those users with a weak card in their own language instead of the
        right card in English — a calibrated floor quietly narrowing the corpus.
        """

        if limit < 1 or not query.strip() or self.is_empty:
            return []
        want = normalize_language(language) if self._language_filter else None
        scored = await run_in_threadpool(self._rank_sync, query, limit, want)
        if want is not None and want != self._fallback_language and not self._answers(scored):
            _log.info(
                "rag_language_fallback",
                requested=want,
                fallback=self._fallback_language,
                retriever=self.setting,
            )
            scored = await run_in_threadpool(self._rank_sync, query, limit, self._fallback_language)
        if not apply_floor:
            return scored
        return [item for item in scored if item.score >= self._tuning.abstain_score]

    def _answers(self, scored: Sequence[ScoredChunk]) -> bool:
        """Whether this language produced anything the floor would serve."""

        return any(item.score >= self._tuning.abstain_score for item in scored)

    async def retrieve(
        self, query: str, *, limit: int = 3, language: str | None = None
    ) -> list[RetrievedChunk]:
        """Up to ``limit`` passages above the calibrated floor, best first.

        Empty is a normal answer — that is what the floor is for — and the
        orchestrator's prompt already tells the model what to do with an empty
        shelf.
        """

        ranked = await self.rank(query, limit=limit, language=language)
        return [item.as_chunk() for item in ranked[:limit]]

    # ------------------------------------------------------------- sync core #

    def _rank_sync(self, query: str, limit: int, language: str | None) -> list[ScoredChunk]:
        """One query, both systems, fused, optionally reranked. Returns RRF order.

        The ordering and the score are two different things here, on purpose:

        * **Order** is reciprocal rank fusion — the weight between the systems
          is the one number tuned on the dev split, and rank fusion is what makes
          a cosine and a BM25 score comparable at all.
        * **Score** is a *cardinal* number in ``[0, 1]``, because the abstain
          floor has to mean something absolute. With a reranker, that is its
          probability. Without one, it is :func:`fused_evidence` below — never
          the RRF confidence, which is ordinal and saturates near 1.0 for any
          query both systems agree on, including "best crypto to buy today"
          (0.995 on this corpus). A threshold on an ordinal number is a
          threshold on nothing.
        """

        candidates, evidence = self.candidates(query, limit=limit, language=language)
        if not candidates:
            return []

        if isinstance(self._reranker, NullCrossEncoder):
            scores, stage, reorder = evidence, "fused", False
        else:
            scores = self._reranker.score(query, candidates)
            stage, reorder = "reranked", True

        ranked = [
            ScoredChunk(
                source_id=candidate.doc_id,
                title=candidate.title,
                text=self._store.text_of(candidate.chunk_id),
                score=float(score),
                stage=stage,
                chunk_id=candidate.chunk_id,
            )
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        if reorder:
            # A reranker earns the right to reorder; the fused list does not,
            # because its order *is* the fusion.
            ranked.sort(key=lambda item: (-item.score, item.source_id))
        # The caller's `limit` is the contract: `rank(limit=3)` returns three.
        # The reranker still saw `rerank_candidates` documents, which is the
        # point — it chooses *which* three, not how many exist.
        return ranked[:limit]

    def candidates(
        self, query: str, *, limit: int | None = None, language: str | None = None
    ) -> tuple[list[RerankCandidate], list[float]]:
        """The first stage's output: one candidate per document, plus its evidence.

        Both systems run, their rankings are fused, the documents are grouped to
        one card each (keeping the document's best chunk), and every candidate
        carries the scores the reranker is allowed to see. Public because the
        reranker's coefficients are fitted offline on *exactly* these rows
        (``ml/src/manovia_ml/retrieval/reranker.py``): a fitter that rebuilt the
        features itself would drift from what serving computes, and the drift
        would stay invisible until the numbers moved.

        ``limit`` only widens the retrieval pool the way :meth:`rank` does; the
        candidate list is capped by ``tuning.rerank_candidates``, since that is
        what a reranker ever sees.
        """

        pool = max(self._tuning.rerank_candidates, self._tuning.candidates, (limit or 0) * 3)
        where = {"language": language} if language else None

        dense_hits = self._dense(query, k=pool, where=where)
        dense_by_chunk = {hit.id: hit.score for hit in dense_hits}
        bm25_matches = self._bm25.search(query, k=pool, where=where)
        bm25_by_chunk = {match.id: match.score for match in bm25_matches}
        if not dense_hits and not bm25_matches:
            return [], []

        fused = reciprocal_rank_fusion(
            {
                DENSE_SYSTEM: [hit.id for hit in dense_hits],
                BM25_SYSTEM: [match.id for match in bm25_matches],
            },
            weights={DENSE_SYSTEM: self._tuning.dense_weight, BM25_SYSTEM: 1.0},
            k=self._tuning.fusion_k,
        )

        # One card per document, keeping the document's best chunk: the same rule
        # as the baseline retriever, because a document's second-best passage is
        # never a second recommendation. Evidence, though, is the document's best
        # *either* signal — a document the lexical half found in one chunk and
        # the dense half found in another is supported, not half-supported.
        groups: dict[str, list[FusedCandidate]] = {}
        for candidate in fused:
            groups.setdefault(candidate.id.split(CHUNK_ID_SEP, 1)[0], []).append(candidate)
        ordered = sorted(
            groups.items(),
            key=lambda item: (-max(c.confidence for c in item[1]), item[0]),
        )[: self._tuning.rerank_candidates]

        candidates: list[RerankCandidate] = []
        evidence: list[float] = []
        for doc_id, group in ordered:
            best = max(group, key=lambda c: (c.confidence, c.id))
            dense = max(
                (min(1.0, max(0.0, dense_by_chunk.get(c.id, 0.0))) for c in group), default=0.0
            )
            lexical = max((bm25_by_chunk.get(c.id, 0.0) for c in group), default=0.0)
            candidates.append(self._candidate(doc_id, best, dense, lexical))
            evidence.append(
                fused_evidence(dense, lexical, self._tuning.dense_weight, self._tuning.dense_scale)
            )

        return candidates, evidence

    def _dense(self, query: str, *, k: int, where: dict[str, str] | None) -> list[Hit]:
        vectors = self._embedder.embed([query])
        if not vectors:
            # An embedder that answers nothing (an unavailable checkpoint) still
            # leaves the lexical half working — degraded, not dead.
            _log.warning("rag_dense_embedder_empty", retriever=self.setting)
            return []
        return self._store.query(vectors[0], k=k, where=where)

    def _candidate(
        self, doc_id: str, best: FusedCandidate, dense_score: float, bm25_score: float
    ) -> RerankCandidate:
        chunk = self._store.chunk(best.id)
        return RerankCandidate(
            chunk_id=best.id,
            doc_id=doc_id,
            title=chunk.metadata.title,
            text=chunk.text,
            language=chunk.metadata.language,
            tags=chunk.metadata.tags,
            dense_score=dense_score,
            bm25_score=bm25_score,
            fused_confidence=best.confidence,
        )


def fused_evidence(
    dense_score: float, bm25_score: float, dense_weight: float, dense_scale: float = 0.24
) -> float:
    """A cardinal relevance estimate in ``[0, 1]`` from the two systems' scores.

    The convex mix is deliberately crude — it is only ever used to decide
    *whether to answer*, never to order (see
    :meth:`HybridKnowledgeRetriever._rank_sync`). Three properties make it
    usable for that:

    * both terms land in ``[0, 1]``, so the threshold is readable and does not
      have to be re-derived when the corpus grows;
    * the dense term is divided by ``dense_scale`` — the cosine this embedding
      space treats as "clearly a match" — because a TF-IDF cosine of 0.3 and a
      sentence-transformer cosine of 0.3 are not the same claim, and a mix that
      ignored that would let whichever system happens to have the larger numbers
      win every time;
    * the BM25 term saturates through the *same* function the reranker's
      ``bm25_saturated`` feature uses, so moving a deployment from ``hybrid`` to
      ``hybrid_rerank`` does not silently rescale the number its abstain
      threshold was calibrated against.
    """

    weight = min(1.0, max(0.0, dense_weight))
    scale = dense_scale if dense_scale > 0.0 else 1.0
    dense = min(1.0, max(0.0, dense_score) / scale)
    return weight * dense + (1.0 - weight) * saturate_bm25(bm25_score)


__all__ = [
    "BM25_SYSTEM",
    "DENSE_SYSTEM",
    "HybridKnowledgeRetriever",
    "HybridTuning",
    "fused_evidence",
]
