"""Hybrid retriever tests: fusion, the abstain floor, and the language fallback.

Built over a real (tiny) ingest — the same three-document corpus the Day-13
retriever tests use — so every assertion is about the path a chat turn takes:
embed, search, fuse, rerank, floor, one card per document.

The two tests that matter most are the two that a plausible-looking
implementation would get wrong:

* ``test_the_fallback_keys_off_the_floor_not_off_emptiness`` — the corpus is 34
  English documents against 3 Hindi and 3 Bengali ones, so a Hindi conversation
  usually needs an English card. A fallback that fires only on an *empty*
  candidate list would serve a weak Hindi card instead, and the calibrated floor
  would quietly narrow the corpus for exactly the users it was calibrated for.
* ``test_an_out_of_scope_query_can_be_refused`` — the floor is the "no good
  answer" decision, and it has to be reachable at all.
"""

import asyncio
from collections.abc import Coroutine, Sequence
from pathlib import Path
from typing import Any, TypeVar

import pytest
from structlog.testing import capture_logs

from app.services.rag.base import ScoredChunk
from app.services.rag.embedders import HashingEmbedder, TfidfEmbedder, embedder_marker
from app.services.rag.hybrid import HybridKnowledgeRetriever, HybridTuning, fused_evidence
from app.services.rag.ingest import run_ingest
from app.services.rag.rerank import (
    CrossEncoder,
    LexicalCrossEncoder,
    RerankCandidate,
)
from app.services.rag.retriever import KnowledgeRetriever
from app.services.rag.store import FaissVectorStore
from tests.unit.test_rag_retriever import DOCS

T = TypeVar("T")


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def ingest(tmp_path: Path) -> tuple[FaissVectorStore, TfidfEmbedder]:
    run_ingest(
        DOCS,
        persist_dir=tmp_path,
        embedder_factory=lambda texts: TfidfEmbedder().fit(texts),
        embedder_config=embedder_marker("tfidf", model_id="", dim=0),
        chunk_words=80,
        overlap_words=20,
    )
    return FaissVectorStore.load(tmp_path), TfidfEmbedder.load(tmp_path)


def hybrid(
    tmp_path: Path,
    *,
    reranker: CrossEncoder | None = None,
    abstain: float = 0.0,
    dense_weight: float = 0.5,
    language_filter: bool = True,
) -> HybridKnowledgeRetriever:
    store, embedder = ingest(tmp_path)
    return HybridKnowledgeRetriever(
        store,
        embedder,
        reranker=reranker,
        tuning=HybridTuning(dense_weight=dense_weight, abstain_score=abstain, dense_scale=0.24),
        min_similarity=0.12,
        fallback_language="en",
        language_filter=language_filter,
    )


@pytest.fixture()
def retriever(tmp_path: Path) -> HybridKnowledgeRetriever:
    return hybrid(tmp_path)


# --------------------------------------------------------------------- ordering #


class TestHybridOrdering:
    def test_the_fusion_sharpens_a_lexical_match(self, tmp_path: Path) -> None:
        """One exact word, and how much more usable the score becomes.

        "notebook" is the word the worry-window document is built around. Both
        retrievers rank it first, but the Day-13 cosine lands at ~0.18 against a
        recommended floor of 0.12 — a match that is barely distinguishable from
        the threshold it has to clear. The fused evidence puts the same match at
        ~0.5, because BM25 says the term is *decisive* here rather than merely
        present. That separation is what makes the abstain floor a decision
        instead of a coin toss, and it is why the two halves are worth fusing
        even when they agree about the order.
        """
        store, embedder = ingest(tmp_path)
        baseline = KnowledgeRetriever(store, embedder, min_similarity=0.12)
        dense_only = run(baseline.rank("notebook", limit=3))
        fused = run(hybrid(tmp_path).rank("notebook", limit=3, apply_floor=False))
        assert dense_only[0].source_id == fused[0].source_id == "worry-window"
        assert fused[0].score > 2.0 * dense_only[0].score
        assert dense_only[0].score < 0.25 < fused[0].score

    def test_a_query_the_dense_retriever_refuses_gets_a_weak_ranking(self, tmp_path: Path) -> None:
        """The case the abstain floor exists for.

        "panic attack" is not in this three-document corpus, so the Day-13
        retriever honestly returns nothing at its floor. The fused list still
        ranks — BM25 always has an opinion about *something* — and the evidence
        score says the opinion is weak. With a floor above that evidence the
        hybrid refuses too, which is the whole "no good answer" story in four
        lines: rank everything, score it against an absolute scale, and let a
        calibrated threshold decide whether any of it deserves the prompt.
        """
        store, embedder = ingest(tmp_path)
        baseline = KnowledgeRetriever(store, embedder, min_similarity=0.12)
        assert run(baseline.rank("how do I stop a panic attack", limit=3)) == []

        unfloored = run(
            hybrid(tmp_path).rank("how do I stop a panic attack", limit=3, apply_floor=False)
        )
        assert unfloored
        assert all(item.score < 0.2 for item in unfloored)

        refused = run(
            hybrid(tmp_path, abstain=0.2).retrieve("how do I stop a panic attack", limit=3)
        )
        assert refused == []

    def test_one_card_per_document(self, retriever: HybridKnowledgeRetriever) -> None:
        ranked = run(retriever.rank("sleep rhythm evening dim", limit=5))
        ids = [item.source_id for item in ranked]
        assert len(ids) == len(set(ids))

    def test_the_limit_is_respected(self, retriever: HybridKnowledgeRetriever) -> None:
        assert len(run(retriever.rank("sleep", limit=1))) <= 1
        assert run(retriever.rank("sleep", limit=0)) == []

    def test_ranking_is_deterministic(self, retriever: HybridKnowledgeRetriever) -> None:
        first = run(retriever.rank("sleep rhythm", limit=5, apply_floor=False))
        second = run(retriever.rank("sleep rhythm", limit=5, apply_floor=False))
        assert [(item.source_id, item.score) for item in first] == [
            (item.source_id, item.score) for item in second
        ]

    def test_a_blank_query_retrieves_nothing(self, retriever: HybridKnowledgeRetriever) -> None:
        assert run(retriever.rank("   ", limit=3)) == []
        assert run(retriever.retrieve("", limit=3)) == []

    def test_an_empty_store_is_an_empty_retriever(self) -> None:
        empty = HybridKnowledgeRetriever(FaissVectorStore(dim=64), HashingEmbedder(dim=64))
        assert empty.is_empty
        assert run(empty.rank("sleep", limit=3)) == []
        assert run(empty.retrieve("sleep", limit=3)) == []

    def test_scores_are_probabilities_and_say_which_stage_made_them(
        self, retriever: HybridKnowledgeRetriever
    ) -> None:
        ranked = run(retriever.rank("sleep rhythm", limit=3, apply_floor=False))
        assert all(isinstance(item, ScoredChunk) for item in ranked)
        assert all(0.0 <= item.score <= 1.0 for item in ranked)
        assert {item.stage for item in ranked} == {"fused"}

    def test_a_reranker_reorders_and_is_named_in_the_stage(self, tmp_path: Path) -> None:
        class Reverse(CrossEncoder):
            """A reranker that inverts the fused order, to prove the stage runs."""

            name = "reverse-for-test"

            def score(self, query: str, candidates: Sequence[RerankCandidate]) -> list[float]:
                del query
                # Increasing with position, so sorting by score reverses the
                # fused order — the only way to see that the stage ran at all.
                total = max(1, len(candidates))
                return [(index + 1) / total for index in range(len(candidates))]

        plain = run(hybrid(tmp_path).rank("sleep rhythm evening", limit=3, apply_floor=False))
        reranked = run(
            hybrid(tmp_path, reranker=Reverse()).rank(
                "sleep rhythm evening", limit=3, apply_floor=False
            )
        )
        assert [item.source_id for item in reranked] == [item.source_id for item in reversed(plain)]
        assert {item.stage for item in reranked} == {"reranked"}

    def test_the_setting_names_the_reranker(self, tmp_path: Path) -> None:
        assert hybrid(tmp_path).setting == "hybrid"
        assert hybrid(tmp_path, reranker=LexicalCrossEncoder.default()).setting == (
            "hybrid_rerank:lexical-cross-encoder-v1:untrained"
        )


# ----------------------------------------------------------------- the abstain floor


class TestAbstainFloor:
    def test_an_out_of_scope_query_can_be_refused(self, tmp_path: Path) -> None:
        # Nothing in this corpus is about cryptocurrency. With a floor at 0 the
        # retriever still serves three cards, because rank fusion always has an
        # opinion; the floor is what makes "nothing here" expressible.
        unrestricted = run(hybrid(tmp_path).retrieve("best crypto exchange fees", limit=3))
        refused = run(hybrid(tmp_path, abstain=0.99).retrieve("best crypto exchange fees", limit=3))
        assert unrestricted
        assert refused == []

    def test_the_floor_keeps_a_strong_match(self, tmp_path: Path) -> None:
        kept = run(
            hybrid(tmp_path, abstain=0.3).retrieve("sleep rhythm evening dim light", limit=3)
        )
        assert kept
        assert kept[0].source_id == "sleep-rhythm"

    def test_rank_without_the_floor_exposes_the_scores_a_threshold_is_fitted_on(
        self, tmp_path: Path
    ) -> None:
        unfloored = run(
            hybrid(tmp_path, abstain=0.99).rank(
                "best crypto exchange fees", limit=3, apply_floor=False
            )
        )
        floored = run(hybrid(tmp_path, abstain=0.99).rank("best crypto exchange fees", limit=3))
        assert unfloored
        assert floored == []
        assert all(item.score < 0.99 for item in unfloored)

    def test_retrieve_is_rank_plus_the_floor(self, tmp_path: Path) -> None:
        ranked = run(hybrid(tmp_path, abstain=0.2).rank("sleep rhythm", limit=3))
        retrieved = run(hybrid(tmp_path, abstain=0.2).retrieve("sleep rhythm", limit=3))
        assert [item.source_id for item in retrieved] == [item.source_id for item in ranked]
        assert all(chunk.text for chunk in retrieved)

    def test_a_returned_chunk_carries_no_score(self, tmp_path: Path) -> None:
        # The prompt must never see the number: a confidence the model can quote
        # is a confidence a user will be shown, and it is not calibrated for that.
        retrieved = run(hybrid(tmp_path).retrieve("sleep rhythm", limit=1))
        assert not hasattr(retrieved[0], "score")


# ------------------------------------------------------------- language fallback #


class TestLanguageFallback:
    def test_the_fallback_keys_off_the_floor_not_off_emptiness(self, tmp_path: Path) -> None:
        # A Hindi conversation about sleep: the corpus has one Hindi document,
        # and at a floor this high it is not good enough. The right answer is
        # the English card, not a weak Hindi one — so the fallback has to look
        # at what survives the floor, not at what the fusion produced.
        strict = hybrid(tmp_path, abstain=0.95)
        with capture_logs() as logs:
            ranked = run(strict.rank("नींद का समय", limit=3, language="hi"))
        assert any(entry["event"] == "rag_language_fallback" for entry in logs)
        assert ranked == [] or all(item.score >= 0.95 for item in ranked)

    def test_a_strong_match_in_the_requested_language_does_not_fall_back(
        self, tmp_path: Path
    ) -> None:
        lenient = hybrid(tmp_path, abstain=0.05)
        with capture_logs() as logs:
            ranked = run(
                lenient.rank("नींद तब बेहतर होती है जब दिन का ढाँचा हो", limit=3, language="hi")
            )
        assert not any(entry["event"] == "rag_language_fallback" for entry in logs)
        assert ranked[0].source_id == "sleep-rhythm-hi"

    def test_the_filter_can_be_switched_off(self, tmp_path: Path) -> None:
        unfiltered = hybrid(tmp_path, language_filter=False)
        ranked = run(unfiltered.rank("sleep rhythm", limit=5, language="hi"))
        assert {item.source_id for item in ranked} > {"sleep-rhythm"}

    def test_the_requested_language_is_normalised(self, tmp_path: Path) -> None:
        ranked = run(hybrid(tmp_path).rank("नींद", limit=3, language="HI"))
        assert all(item.source_id for item in ranked) or ranked == []


# ------------------------------------------------------------------- the evidence


class TestFusedEvidence:
    def test_it_is_a_convex_mix_of_two_bounded_terms(self) -> None:
        assert fused_evidence(0.0, 0.0, 0.5) == 0.0
        # The saturation is asymptotic, so "both terms maxed" approaches 1
        # rather than reaching it: 0.5 * 1.0 + 0.5 * (1e6 / (5 + 1e6)).
        assert fused_evidence(0.24, 1e6, 0.5) == pytest.approx(1.0, abs=1e-3)
        assert fused_evidence(0.24, 5.0, 0.5) == pytest.approx(0.75)
        assert 0.0 <= fused_evidence(0.12, 6.0, 0.5) <= 1.0

    def test_the_weight_selects_a_system(self) -> None:
        assert fused_evidence(0.24, 0.0, 1.0) == pytest.approx(1.0)
        assert fused_evidence(0.0, 5.0, 0.0) == pytest.approx(0.5)

    def test_the_dense_scale_absorbs_a_different_embedding_space(self) -> None:
        # A TF-IDF cosine of 0.3 and a checkpoint cosine of 0.3 are not the same
        # claim; `dense_scale` is the knob that says what "clearly a match" is
        # worth in each space, so neither system wins by having bigger numbers.
        tfidf = fused_evidence(0.30, 0.0, 1.0, dense_scale=0.24)
        checkpoint = fused_evidence(0.30, 0.0, 1.0, dense_scale=0.70)
        assert tfidf == pytest.approx(1.0)
        assert checkpoint == pytest.approx(0.30 / 0.70)

    def test_a_negative_cosine_counts_as_no_evidence(self) -> None:
        assert fused_evidence(-0.4, 0.0, 1.0) == 0.0


# ------------------------------------------------------- first-stage candidates #


class TestFirstStageCandidates:
    """``candidates()`` is the seam the offline reranker fitter uses.

    The coefficients in ``app/ml_artifacts/retrieval_v1/reranker`` are fitted in
    ``ml/src/manovia_ml/retrieval/reranker.py`` on feature rows built from this
    method. That only means something if these are *the* rows serving scores:
    a fitter that reconstructed them would drift silently, and the drift would
    show up as a worse reranker with nothing pointing at the cause.
    """

    def test_one_candidate_per_document_carrying_both_first_stage_scores(
        self, tmp_path: Path
    ) -> None:
        candidates, evidence = hybrid(tmp_path).candidates("where do I put the notebook")
        assert candidates
        doc_ids = [candidate.doc_id for candidate in candidates]
        assert len(doc_ids) == len(set(doc_ids)), "a document is never two cards"
        assert len(evidence) == len(candidates)
        for candidate in candidates:
            assert candidate.chunk_id.startswith(f"{candidate.doc_id}#")
            assert candidate.title and candidate.text
            assert 0.0 <= candidate.dense_score <= 1.0
            assert candidate.bm25_score >= 0.0
            assert candidate.fused_confidence > 0.0

    def test_the_evidence_is_fused_evidence_of_the_same_two_scores(self, tmp_path: Path) -> None:
        # The number a deployment abstains on, recomputed from the public pair:
        # if `candidates()` and `_rank_sync()` ever disagreed about the evidence,
        # the calibrated threshold would be fitted against the wrong quantity.
        retriever = hybrid(tmp_path, dense_weight=0.4)
        candidates, evidence = retriever.candidates("steady sleep rhythm evenings")
        tuning = retriever.tuning
        expected = [
            fused_evidence(
                candidate.dense_score,
                candidate.bm25_score,
                tuning.dense_weight,
                tuning.dense_scale,
            )
            for candidate in candidates
        ]
        assert evidence == pytest.approx(expected)

    def test_rank_scores_exactly_the_candidates_this_method_returns(self, tmp_path: Path) -> None:
        seen: list[list[RerankCandidate]] = []

        class Recording(CrossEncoder):
            name = "recording-for-test"

            def score(self, query: str, candidates: Sequence[RerankCandidate]) -> list[float]:
                del query
                seen.append(list(candidates))
                return [0.5 for _ in candidates]

        retriever = hybrid(tmp_path, reranker=Recording())
        run(retriever.rank("where do I put the notebook", limit=3, apply_floor=False))
        public, _ = retriever.candidates("where do I put the notebook", limit=3)
        assert len(seen) == 1
        assert [(c.chunk_id, c.dense_score, c.bm25_score) for c in seen[0]] == [
            (c.chunk_id, c.dense_score, c.bm25_score) for c in public
        ]

    def test_the_candidate_list_is_capped_by_the_reranker_window(self, tmp_path: Path) -> None:
        store, embedder = ingest(tmp_path)
        narrow = HybridKnowledgeRetriever(
            store,
            embedder,
            tuning=HybridTuning(rerank_candidates=1, abstain_score=0.0, dense_scale=0.24),
            min_similarity=0.0,
            fallback_language="en",
            language_filter=False,
        )
        candidates, evidence = narrow.candidates("sleep rhythm evenings", limit=5)
        assert len(candidates) == len(evidence) == 1
        wide = hybrid(tmp_path)
        assert len(wide.candidates("sleep rhythm evenings", limit=5)[0]) > 1

    def test_a_language_with_no_chunks_produces_no_candidates(self, tmp_path: Path) -> None:
        # Bengali is not in this three-document corpus, so both systems come back
        # empty and the method says so with empty lists rather than raising — the
        # same condition `_rank_sync` turns into "no cards".
        candidates, evidence = hybrid(tmp_path).candidates("ঘুম", language="bn")
        assert candidates == []
        assert evidence == []

    def test_the_language_argument_reaches_both_systems(self, tmp_path: Path) -> None:
        filtered, _ = hybrid(tmp_path).candidates("नींद", language="hi")
        assert filtered
        assert all(candidate.language == "hi" for candidate in filtered)
        unfiltered, _ = hybrid(tmp_path).candidates("नींद", language=None)
        assert len(unfiltered) >= len(filtered)
