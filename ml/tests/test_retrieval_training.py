"""The contrastive training stack: encoders, pair mining, the loss, and the harness.

What has to be true for the Day-35 numbers to mean anything:

* **The encoders are deterministic and normalised.** A cosine over an
  unnormalised vector is not a cosine, and a run that cannot be repeated cannot
  be compared with the run next to it in the table.
* **An encoder is also an embedder.** ``manovia_ml.retrieval.embeddings.Encoder``
  declares the four members ``app.services.rag.base.Embedder`` requires, so the
  thing that is trained and the thing that serves are the same object rather
  than two implementations with a translation layer between them.
* **A hard negative is never a relevant document.** The single bug that would
  most quietly destroy this training run: contrastive learning pushes apart
  whatever it is told to, so a false negative teaches the encoder that the right
  answer is wrong.
* **The loss is the loss the brief names.** ``multiple_negatives_ranking`` has a
  closed form worth pinning: in-batch negatives plus the mined ones, labels on
  the diagonal, temperature ``scale``, and a gradient that a numerical check
  agrees with.
* **Epoch selection reads dev, never test.** ``train_projection`` only accepts a
  callback, so the test asserts the callback is what decides — the mechanism
  that keeps the frozen split frozen.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from manovia_ml.config import (
    RetrievalConfig,
    RetrievalEncoderConfig,
    RetrievalFeaturesConfig,
    load_retrieval_config,
)
from manovia_ml.retrieval.contrastive import (
    contrastive_step,
    multiple_negatives_ranking,
    train_projection,
)
from manovia_ml.retrieval.corpus import Document, Passage, build_passages
from manovia_ml.retrieval.data import RetrievalQuery, load_eval_set, split_rows
from manovia_ml.retrieval.embeddings import (
    EncoderUnavailable,
    HashingEncoder,
    LexicalFeatures,
    ProjectionEncoder,
    TfidfEncoder,
    build_encoder,
)
from manovia_ml.retrieval.harness import (
    SETTINGS,
    Harness,
    HarnessUnavailable,
    SettingSpec,
    language_of,
    rank_documents,
    read_tuning,
)
from manovia_ml.retrieval.pairs import TrainingPair, build_pairs, mine_hard_negatives

SEED = 20261013


def tiny_docs() -> tuple[Document, ...]:
    return (
        Document(
            id="sleep-rhythm",
            title="A steady sleep rhythm",
            language="en",
            tags=("sleep",),
            body=(
                "Sleep improves when the day has a shape. Wake at the same time and keep "
                "the evenings dim. " * 6
            ),
        ),
        Document(
            id="worry-window",
            title="A window for worries",
            language="en",
            tags=("worry",),
            body=(
                "Give worries one slot of the day. Write each worry down and let them wait "
                "together until the window opens. " * 6
            ),
        ),
        Document(
            id="sleep-rhythm-hi",
            title="सोने का समय",
            language="hi",
            tags=("sleep",),
            body=(
                "नींद तब बेहतर होती है जब दिन का एक ढाँचा हो। एक ही समय पर उठें और शाम को "
                "रोशनी कम करें। " * 6
            ),
        ),
    )


def tiny_passages() -> tuple[Passage, ...]:
    return build_passages(tiny_docs(), chunk_words=40, overlap_words=10)


def tiny_queries() -> list[RetrievalQuery]:
    return [
        RetrievalQuery.model_validate(
            {
                "id": "rt-t1",
                "query": "how do I keep a steady sleep rhythm",
                "lang": "en",
                "style": "direct",
                "relevant_docs": ["sleep-rhythm"],
            }
        ),
        RetrievalQuery.model_validate(
            {
                "id": "rt-t2",
                "query": "where do I put my worries for the night",
                "lang": "en",
                "style": "scenario",
                "relevant_docs": ["worry-window"],
            }
        ),
        RetrievalQuery.model_validate(
            {
                "id": "rt-t3",
                "query": "नींद का ढाँचा कैसे बनाएँ",
                "lang": "hi",
                "style": "direct",
                "relevant_docs": ["sleep-rhythm-hi", "sleep-rhythm"],
            }
        ),
        RetrievalQuery.model_validate(
            {
                "id": "rt-t4",
                "query": "best crypto to buy today",
                "lang": "en",
                "style": "no-answer",
                "relevant_docs": [],
                "has_answer": False,
                "no_answer_reason": "out-of-scope",
            }
        ),
    ]


def unit_rows(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise every row — what the training path feeds the loss."""

    norms: np.ndarray = np.linalg.norm(matrix, axis=-1, keepdims=True)
    safe: np.ndarray = np.where(norms == 0, 1.0, norms)
    scaled: np.ndarray = matrix / safe
    return scaled


# ------------------------------------------------------------------- encoders #


class TestEncoders:
    def test_hashing_is_deterministic_and_unit_norm(self) -> None:
        encoder = HashingEncoder(dim=64)
        first = encoder.encode(["cannot sleep at night"])
        second = encoder.encode(["cannot sleep at night"])
        assert first.shape == (1, 64)
        assert np.allclose(first, second)
        assert np.linalg.norm(first[0]) == pytest.approx(1.0)

    def test_hashing_separates_different_texts(self) -> None:
        encoder = HashingEncoder(dim=256)
        vectors = encoder.encode(["cannot sleep at night", "crypto portfolio fees"])
        assert float(vectors[0] @ vectors[1]) < 0.5

    def test_tfidf_needs_a_corpus_and_then_needs_nothing(self) -> None:
        encoder = TfidfEncoder.fit([passage.embed_text for passage in tiny_passages()])
        assert encoder.dim > 0
        assert encoder.resolve_dim() == encoder.dim
        vector = encoder.encode(["steady sleep rhythm"])[0]
        assert np.linalg.norm(vector) == pytest.approx(1.0)

    def test_tfidf_matches_a_document_it_was_fitted_on(self) -> None:
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        ranked = rank_documents(encoder, "steady sleep rhythm", passages, limit=3)
        assert ranked[0] == "sleep-rhythm"

    def test_the_hindi_passage_is_reachable_in_hindi(self) -> None:
        # The regression the Day-35 tokeniser fix exists for: with the old
        # alternation pattern, "नींद" was cut at its matras and never matched the
        # Hindi document at all.
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        ranked = rank_documents(encoder, "नींद का ढाँचा", passages, limit=3)
        assert "sleep-rhythm-hi" in ranked

    def test_an_empty_batch_is_an_empty_matrix_not_a_crash(self) -> None:
        encoder = TfidfEncoder.fit(["some text"])
        assert encoder.encode([]).shape == (0, encoder.dim)
        assert encoder.embed([]) == []

    def test_features_round_trip_through_disk(self, tmp_path: Path) -> None:
        texts = [passage.embed_text for passage in tiny_passages()]
        features = LexicalFeatures.fit(texts, RetrievalFeaturesConfig())
        features.save(tmp_path)
        loaded = LexicalFeatures.load(tmp_path)
        assert loaded.dim == features.dim
        assert np.allclose(loaded.transform(texts).toarray(), features.transform(texts).toarray())

    def test_a_projection_round_trips_and_stays_normalised(self, tmp_path: Path) -> None:
        texts = [passage.embed_text for passage in tiny_passages()]
        features = LexicalFeatures.fit(texts, RetrievalFeaturesConfig())
        rng = np.random.default_rng(7)
        # (dim, feature_dim): the projection maps the sparse feature space down.
        encoder = ProjectionEncoder(features, rng.normal(size=(8, features.dim)))
        encoder.save(tmp_path, metadata={"trained_on": "test"})
        loaded = ProjectionEncoder.load(tmp_path)
        assert loaded.dim == 8
        assert loaded.name == encoder.name
        assert np.allclose(loaded.encode(texts), encoder.encode(texts))
        norms = np.linalg.norm(loaded.encode(texts), axis=1)
        assert np.allclose(norms, 1.0)
        assert loaded.weights.shape == (8, features.dim)

    def test_a_saved_projection_records_its_own_provenance(self, tmp_path: Path) -> None:
        # The metadata file is what a report quotes and what a deployment checks
        # before serving: which run made these weights, and over what.
        import json

        features = LexicalFeatures.fit(["some text here"], RetrievalFeaturesConfig())
        encoder = ProjectionEncoder(features, np.eye(4, features.dim))
        encoder.save(tmp_path, metadata={"dev_mrr": 0.5, "run_id": "abc123"})
        payload = json.loads((tmp_path / "encoder.json").read_text(encoding="utf-8"))
        assert payload["dev_mrr"] == 0.5
        assert payload["run_id"] == "abc123"
        assert payload["kind"] == "projection"
        assert payload["weights_sha256"]
        assert ProjectionEncoder.load(tmp_path).dim == 4

    def test_loading_a_projection_without_weights_is_an_actionable_error(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(EncoderUnavailable, match="no projection weights"):
            ProjectionEncoder.load(tmp_path / "nothing-here")

    def test_an_encoder_is_also_a_backend_embedder(self) -> None:
        # The seam, tested from the ml side: same four members, no adapter.
        try:
            from app.services.rag.base import Embedder
        except ImportError:  # pragma: no cover - ml installs without the backend
            pytest.skip("the backend is not installed here")
        encoder = HashingEncoder(dim=64)
        assert isinstance(encoder, Embedder)
        assert isinstance(TfidfEncoder.fit(["text"]), Embedder)

    def test_a_checkpoint_encoder_fails_late_and_actionably(self) -> None:
        # Building must not touch the network (a config is read at startup, in
        # CI, offline); the failure belongs at encode time, where it can say
        # what to do about it.
        encoder_config = RetrievalEncoderConfig(
            kind="sentence_transformers", model_id="paraphrase-multilingual-MiniLM-L12-v2"
        )
        encoder = build_encoder(encoder_config)
        with pytest.raises(EncoderUnavailable) as error:
            encoder.encode(["cannot sleep"])
        message = str(error.value)
        assert "sentence-transformers" in message or "colab_retrieval_finetune" in message

    def test_build_encoder_dispatches_on_kind(self) -> None:
        texts = [passage.embed_text for passage in tiny_passages()]
        assert build_encoder(RetrievalEncoderConfig(kind="hashing", dim=64)).name.startswith(
            "hashing"
        )
        assert build_encoder(RetrievalEncoderConfig(kind="tfidf"), fit_texts=texts).name.startswith(
            "tfidf"
        )
        assert (
            build_encoder(
                RetrievalEncoderConfig(kind="projection", dim=16), fit_texts=texts
            ).resolve_dim()
            == 16
        )

    def test_an_artifact_dir_cannot_turn_a_tfidf_encoder_into_a_trained_one(
        self, tmp_path: Path
    ) -> None:
        # Regression (Day 35): `build_encoder` checked `artifact_dir` before
        # `kind`, so a config saying `kind: tfidf` that also pointed at a trained
        # projection loaded the weights. The harness hit it by overriding the kind
        # on a projection config: it ingested a projection into a directory called
        # `store-tfidf`, and two stages later the reload raised
        # EmbedderUnavailable far from its cause. A config that says TF-IDF gets
        # TF-IDF, whatever else it points at.
        texts = [passage.embed_text for passage in tiny_passages()]
        features = LexicalFeatures.fit(texts, RetrievalFeaturesConfig())
        trained_dir = tmp_path / "trained"
        ProjectionEncoder(features, np.random.default_rng(2).normal(size=(16, features.dim))).save(
            trained_dir
        )

        projection = build_encoder(
            RetrievalEncoderConfig(kind="projection", artifact_dir=str(trained_dir))
        )
        assert projection.name.startswith("projection")
        assert projection.resolve_dim() == 16

        tfidf = build_encoder(RetrievalEncoderConfig(kind="tfidf"), fit_texts=texts)
        assert tfidf.name.startswith("tfidf")
        assert tfidf.resolve_dim() == features.dim

    def test_the_harness_never_leaks_a_trained_encoder_into_an_off_the_shelf_row(
        self, tmp_path: Path
    ) -> None:
        # The other half of the same regression: the harness builds one row per
        # encoder kind from a single config, so overriding the kind has to clear
        # the artefact directory too. `dense_finetuned` and `hybrid` must not be
        # the same encoder, or the comparison table compares a thing with itself.
        config = load_retrieval_config("configs/retrieval_smoke.yaml")
        harness = Harness(config, workdir=tmp_path)
        texts = [passage.embed_text for passage in tiny_passages()]
        assert harness._encoder("tfidf", texts).name.startswith("tfidf")
        assert harness._encoder("hashing", texts).name.startswith("hashing")

    def test_a_corpus_fitted_kind_without_a_corpus_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="fit_texts"):
            build_encoder(RetrievalEncoderConfig(kind="tfidf"))


# ---------------------------------------------------------------------- pairs #


class TestPairs:
    def test_a_hard_negative_is_never_a_relevant_document(self) -> None:
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        pairs, report = build_pairs(tiny_queries(), passages, encoder=encoder, negatives=2)
        assert pairs
        # The *first* passage of each document, which is what build_pairs uses:
        # chunk 00 carries the title-anchored opening, and for a short document
        # it is usually the whole document.
        relevant_by_doc: dict[str, Passage] = {}
        for passage in passages:
            relevant_by_doc.setdefault(passage.doc_id, passage)
        for pair in pairs:
            negatives = {chunk.split("#", 1)[0] for chunk in pair.hard_negatives}
            assert pair.positive_doc not in negatives
            assert not (negatives & {pair.positive_doc})
            assert pair.positive == relevant_by_doc[pair.positive_doc].chunk_id
        assert report.pairs == len(pairs)

    def test_a_no_answer_query_produces_no_pairs(self) -> None:
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        pairs, _ = build_pairs(tiny_queries(), passages, encoder=encoder, negatives=2)
        assert all(pair.query_id != "rt-t4" for pair in pairs)

    def test_a_query_with_two_relevant_documents_yields_two_pairs(self) -> None:
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        pairs, report = build_pairs(tiny_queries(), passages, encoder=encoder, negatives=2)
        from_t3 = [pair for pair in pairs if pair.query_id == "rt-t3"]
        assert {pair.positive_doc for pair in from_t3} == {"sleep-rhythm-hi", "sleep-rhythm"}
        assert report.positives_per_query[2] >= 1

    def test_mining_is_a_function_of_its_inputs(self) -> None:
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        first, _ = build_pairs(tiny_queries(), passages, encoder=encoder, negatives=2)
        second, _ = build_pairs(tiny_queries(), passages, encoder=encoder, negatives=2)
        assert [(p.query_id, p.hard_negatives) for p in first] == [
            (p.query_id, p.hard_negatives) for p in second
        ]

    def test_the_miner_prefers_a_near_miss_from_the_same_topic(self) -> None:
        # "sleep-rhythm-hi" shares the `sleep` tag with the relevant document,
        # so it is the negative worth learning from: same topic, wrong language,
        # wrong answer. A random negative teaches the encoder almost nothing.
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        scores = (
            encoder.encode(["steady sleep rhythm"])
            @ encoder.encode([passage.embed_text for passage in passages]).T
        )
        mined, tagged = mine_hard_negatives(
            passages=passages,
            scores=list(scores[0]),
            relevant_docs=["sleep-rhythm"],
            candidates=10,
            negatives=2,
        )
        assert tagged >= 1
        assert all(chunk.split("#", 1)[0] != "sleep-rhythm" for chunk in mined)

    def test_a_split_with_nothing_to_contrast_is_refused(self) -> None:
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        refused = [row for row in tiny_queries() if not row.has_answer]
        with pytest.raises(ValueError, match="no answerable queries"):
            build_pairs(refused, passages, encoder=encoder)


# ----------------------------------------------------------------------- loss #


class TestLoss:
    @staticmethod
    def batch(negatives_per_query: int = 2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """A batch of 4, with ``negatives_per_query`` hard negatives each.

        Negatives are *flattened* rows — shape ``(batch * negatives, dim)`` —
        because the loss builds one candidate matrix (positives then negatives)
        and puts the labels on the diagonal.
        """

        rng = np.random.default_rng(11)

        queries = unit_rows(rng.normal(size=(4, 6)))
        positives = unit_rows(queries + rng.normal(scale=0.05, size=(4, 6)))
        negatives = unit_rows(rng.normal(size=(4 * negatives_per_query, 6)))
        return queries, positives, negatives

    def test_aligned_pairs_beat_shuffled_ones(self) -> None:
        queries, positives, negatives = self.batch()
        aligned, _, _, _ = multiple_negatives_ranking(queries, positives, negatives)
        shuffled, _, _, _ = multiple_negatives_ranking(
            queries, positives[np.roll(np.arange(4), 1)], negatives
        )
        assert aligned < shuffled

    def test_the_loss_of_a_perfect_batch_approaches_zero(self) -> None:
        queries = np.eye(4)
        positives = np.eye(4)
        negatives = np.zeros((8, 4))  # two flattened hard negatives per query
        loss, _, _, _ = multiple_negatives_ranking(queries, positives, negatives, scale=40.0)
        assert loss < 0.05

    def test_the_gradient_shapes_match_the_inputs(self) -> None:
        queries, positives, negatives = self.batch()
        _, d_query, d_positive, d_negative = multiple_negatives_ranking(
            queries, positives, negatives
        )
        assert d_query.shape == queries.shape
        assert d_positive.shape == positives.shape
        assert d_negative is not None
        assert d_negative.shape == negatives.shape

    def test_without_hard_negatives_it_is_plain_in_batch_mnrl(self) -> None:
        queries, positives, _ = self.batch()
        loss, d_query, d_positive, d_negative = multiple_negatives_ranking(queries, positives)
        assert d_negative is None
        assert d_query.shape == queries.shape
        assert d_positive.shape == positives.shape
        # Four in-batch negatives, one of them correct: -log(1/4) at best.
        assert loss > 0.0
        assert loss < np.log(4) + 1.0

    def test_the_gradient_agrees_with_a_numerical_one(self) -> None:
        # The closed-form gradient is the part of this file most worth checking:
        # a wrong sign or a missing temperature trains happily and converges to
        # the wrong space, and nothing downstream would notice.
        rng = np.random.default_rng(3)

        # Unit vectors, as the training path passes them: unnormalised rows with
        # a temperature of 7 saturate the softmax, the probability floor clips,
        # and the numerical derivative goes to zero while the analytic one does
        # not — a test that would pass for the wrong reason or fail for none.
        queries = unit_rows(rng.normal(size=(3, 4)))
        positives = unit_rows(rng.normal(size=(3, 4)))
        negatives = unit_rows(rng.normal(size=(3, 4)))
        _, d_query, _, _ = multiple_negatives_ranking(queries, positives, negatives, scale=7.0)
        step = 1e-6
        for row in range(3):
            for column in range(4):
                perturbed = queries.copy()
                perturbed[row, column] += step
                plus, _, _, _ = multiple_negatives_ranking(
                    perturbed, positives, negatives, scale=7.0
                )
                perturbed[row, column] -= 2 * step
                minus, _, _, _ = multiple_negatives_ranking(
                    perturbed, positives, negatives, scale=7.0
                )
                numeric = (plus - minus) / (2 * step)
                assert d_query[row, column] == pytest.approx(numeric, abs=1e-4)

    def test_a_step_reduces_the_loss_on_the_same_batch(self) -> None:
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        pairs, _ = build_pairs(tiny_queries(), passages, encoder=encoder, negatives=2)
        features = LexicalFeatures.fit(
            [passage.embed_text for passage in passages], RetrievalFeaturesConfig()
        )
        text_of = {passage.chunk_id: passage.embed_text for passage in passages}
        query_features = features.transform([pair.query for pair in pairs])
        positive_features = features.transform([text_of[pair.positive] for pair in pairs])
        negative_features = features.transform(
            [text_of[chunk] for pair in pairs for chunk in pair.hard_negatives]
        )
        assert negative_features.shape[0] == sum(len(pair.hard_negatives) for pair in pairs)
        weights = np.random.default_rng(5).normal(scale=0.01, size=(8, features.dim))
        first, gradient = contrastive_step(
            weights, query_features, positive_features, negative_features, scale=20.0
        )
        assert gradient.shape == weights.shape
        stepped = weights - 0.05 * gradient
        second, _ = contrastive_step(
            stepped, query_features, positive_features, negative_features, scale=20.0
        )
        assert second < first


# -------------------------------------------------------------------- training #


class TestTrainProjection:
    def prep(self) -> tuple[list[TrainingPair], tuple[Passage, ...], LexicalFeatures]:
        passages = tiny_passages()
        queries = [row for row in tiny_queries() if row.has_answer]
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        pairs, _ = build_pairs(queries, passages, encoder=encoder, negatives=2)
        features = LexicalFeatures.fit(
            [passage.embed_text for passage in passages], RetrievalFeaturesConfig()
        )
        return pairs, passages, features

    def test_the_loss_falls_and_an_encoder_comes_out(self) -> None:
        pairs, passages, features = self.prep()
        result = train_projection(
            pairs, passages, features=features, dim=8, epochs=4, batch_size=2, lr=0.05, seed=SEED
        )
        assert result.pairs == len(pairs)
        assert result.dim == 8
        assert result.encoder is not None
        assert result.history[0].train_loss > result.history[-1].train_loss
        assert result.weights.shape == (8, features.dim)

    def test_a_dev_callback_selects_the_epoch_it_prefers(self) -> None:
        pairs, passages, features = self.prep()
        seen: list[int] = []

        def select(weights: np.ndarray) -> float:
            seen.append(len(seen) + 1)
            # Prefer epoch 2 of 4, whatever the weights are: the point is that
            # the returned encoder holds epoch 2's weights and says so.
            return 1.0 if len(seen) == 2 else 0.0

        result = train_projection(
            pairs,
            passages,
            features=features,
            dim=8,
            epochs=4,
            batch_size=2,
            seed=SEED,
            select_score=select,
        )
        assert result.best_epoch == 2
        assert [record.select_score for record in result.history] == [0.0, 1.0, 0.0, 0.0]
        assert result.best_weights().shape == result.weights.shape

    def test_training_is_seeded(self) -> None:
        pairs, passages, features = self.prep()
        first = train_projection(
            pairs, passages, features=features, dim=8, epochs=2, batch_size=2, seed=SEED
        )
        second = train_projection(
            pairs, passages, features=features, dim=8, epochs=2, batch_size=2, seed=SEED
        )
        assert np.allclose(first.weights, second.weights)

    def test_a_trailing_single_batch_is_dropped_rather_than_degenerate(self) -> None:
        # MNRL's in-batch negatives need at least two queries to have anything
        # to contrast, so an odd tail is skipped instead of trained on as a
        # one-class step. Four pairs at batch_size 3 leaves one singleton.
        pairs, passages, features = self.prep()
        result = train_projection(
            pairs, passages, features=features, dim=8, epochs=1, batch_size=3, seed=SEED
        )
        assert result.history[0].steps == 1
        full = train_projection(
            pairs, passages, features=features, dim=8, epochs=1, batch_size=2, seed=SEED
        )
        assert full.history[0].steps == 2

    def test_pairs_naming_an_unknown_passage_are_refused(self) -> None:
        # A pair whose positive is not in the corpus would train on a zero
        # vector: no error, no signal, and a silently worse encoder.
        pairs, passages, features = self.prep()
        broken = [
            TrainingPair(
                query_id=pairs[0].query_id,
                query=pairs[0].query,
                lang=pairs[0].lang,
                positive="document-that-does-not-exist#00",
                positive_doc="document-that-does-not-exist",
                hard_negatives=(),
            )
        ]
        with pytest.raises(ValueError, match="unknown passages"):
            train_projection(broken, passages, features=features, dim=4, epochs=1)


# --------------------------------------------------------------------- harness #


class TestHarness:
    def test_rank_documents_is_backend_free_and_one_card_per_document(self) -> None:
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        ranked = rank_documents(encoder, "steady sleep rhythm", passages, limit=5)
        assert ranked[0] == "sleep-rhythm"
        assert len(ranked) == len(set(ranked))
        assert len(ranked) == len(tiny_docs())

    def test_rank_documents_respects_the_limit_and_empty_input(self) -> None:
        passages = tiny_passages()
        encoder = TfidfEncoder.fit([passage.embed_text for passage in passages])
        assert len(rank_documents(encoder, "sleep", passages, limit=1)) == 1
        assert rank_documents(encoder, "sleep", (), limit=3) == ()
        assert rank_documents(encoder, "sleep", passages, limit=0) == ()

    def test_the_language_map_is_about_the_corpus_not_the_script(self) -> None:
        rows = tiny_queries()
        assert language_of(rows[0]) == "en"
        assert language_of(rows[2]) == "hi"
        romanised = RetrievalQuery.model_validate(
            {
                "id": "rt-latn",
                "query": "ghar pe akela feel hota hai",
                "lang": "hi-Latn",
                "style": "hinglish",
                "relevant_docs": ["sleep-rhythm"],
            }
        )
        assert language_of(romanised) == "hi"

    def test_the_settings_cover_the_four_comparisons_the_brief_asks_for(self) -> None:
        names = {spec.name for spec in SETTINGS}
        assert {"baseline", "dense_finetuned", "hybrid", "hybrid_rerank"} <= names
        # Every row says what it is in the report's own words.
        assert all(spec.describe() for spec in SETTINGS)
        assert {spec.retriever for spec in SETTINGS} == {"baseline", "hybrid"}

    def test_a_row_says_why_it_cannot_be_measured(self, tmp_path: Path) -> None:
        config = load_retrieval_config("configs/retrieval_smoke.yaml")
        harness = Harness(config, workdir=tmp_path)
        spec = next(item for item in SETTINGS if item.name == "dense_finetuned")
        reason = harness.unavailable_reason(spec)
        assert reason is None or "fine-tuned encoder" in reason or "backend" in reason

    def test_a_missing_artifact_is_reported_not_guessed(self, tmp_path: Path) -> None:
        config = load_retrieval_config("configs/retrieval_smoke.yaml")
        harness = Harness(config, workdir=tmp_path, encoder_dirs={"projection": tmp_path / "none"})
        spec = next(item for item in SETTINGS if item.name == "dense_finetuned")
        with pytest.raises(HarnessUnavailable):
            harness.build(spec)

    def test_read_tuning_is_empty_for_a_file_that_is_not_there(self, tmp_path: Path) -> None:
        assert read_tuning(tmp_path / "tuning.json") == {}
        (tmp_path / "tuning.json").write_text('{"dev": {"dense_weight": 0.3}}', encoding="utf-8")
        assert read_tuning(tmp_path / "tuning.json")["dev"]["dense_weight"] == 0.3

    def test_a_spec_can_override_one_knob_without_touching_the_others(self) -> None:
        base = next(item for item in SETTINGS if item.name == "hybrid")
        variant = SettingSpec(
            name="sweep", encoder=base.encoder, dense_weight=0.0, label="bm25 only"
        )
        assert variant.dense_weight == 0.0
        assert base.dense_weight is None


# ---------------------------------------------------------------------- config #


class TestRetrievalConfig:
    def test_the_shipped_configs_load(self) -> None:
        for name in ("retrieval_finetune", "retrieval_hybrid", "retrieval_eval", "retrieval_smoke"):
            config = load_retrieval_config(f"configs/{name}.yaml")
            assert isinstance(config, RetrievalConfig)
            assert config.data.split_seed == SEED

    def test_an_unknown_key_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("name: bad\nseed: 1\ntelepathy: true\n", encoding="utf-8")
        with pytest.raises(Exception, match=r"telepathy|extra"):
            load_retrieval_config(path)

    def test_an_artifact_dir_only_means_something_for_a_projection(self) -> None:
        # Symmetric with `model_id`, which was already refused for the wrong
        # kind: a key that only applies to one encoder kind must not be silently
        # ignored for the others, because "ignored" and "applied" produce
        # different encoders under the same config.
        with pytest.raises(Exception, match="artifact_dir"):
            RetrievalEncoderConfig(kind="tfidf", artifact_dir="data/retrieval_artifacts/x")
        assert RetrievalEncoderConfig(kind="projection", artifact_dir="x").artifact_dir == "x"
        assert RetrievalEncoderConfig(kind="tfidf").artifact_dir == ""

    def test_the_reranker_cannot_see_more_than_the_fusion_produced(self) -> None:
        with pytest.raises(Exception, match=r"reranker\.candidates|candidates"):
            RetrievalConfig.model_validate(
                {
                    "name": "bad",
                    "seed": 1,
                    "hybrid": {"rerank_candidates": 5},
                    "reranker": {"candidates": 9},
                }
            )

    def test_the_calibration_split_is_never_the_test_split(self) -> None:
        # Encoded as a type, not a convention: `fit_split` has two legal values
        # and neither is "test".
        config = load_retrieval_config("configs/retrieval_hybrid.yaml")
        assert config.calibration.fit_split in {"calibration", "dev"}
        assert config.evaluation.split == "dev"
        report_config = load_retrieval_config("configs/retrieval_eval.yaml")
        assert report_config.evaluation.split == "test"

    def test_the_eval_set_and_its_splits_agree_about_membership(self) -> None:
        rows = load_eval_set()
        grouped = split_rows(rows, seed=SEED)
        assert sum(len(value) for value in grouped.values()) == len(rows)
        assert grouped["test"] and grouped["calibration"]
