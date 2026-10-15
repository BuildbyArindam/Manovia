"""Cross-encoder reranker: features, the untrained scorer, and the artefact contract.

The reranker is the only stage in the hybrid that can *reorder* what the fusion
produced, so its tests are mostly about the contract that makes reordering
safe: a fixed feature list, a fixed version, and coefficients that refuse to
load when either has moved underneath them. A silently shifted feature column
is the failure mode that produces a confident, plausible, wrong ranking.
"""

import json
from pathlib import Path

import pytest

from app.services.rag.rerank import (
    BM25_SATURATION_PIVOT,
    DEFAULT_WEIGHTS,
    FEATURE_NAMES,
    FEATURE_VERSION,
    RERANKER_METADATA_FILE,
    RERANKER_WEIGHTS_FILE,
    CrossEncoder,
    CrossEncoderUnavailable,
    LexicalCrossEncoder,
    NullCrossEncoder,
    RerankCandidate,
    TransformerCrossEncoder,
    cross_encoder_features,
    load_cross_encoder,
    saturate_bm25,
)


def candidate(
    *,
    title: str = "Box breathing",
    text: str = "Count the breath in four equal steps of four counts each.",
    language: str = "en",
    tags: tuple[str, ...] = ("breathing",),
    dense: float = 0.4,
    bm25: float = 6.0,
    confidence: float = 0.9,
) -> RerankCandidate:
    return RerankCandidate(
        chunk_id="breathing-box#00",
        doc_id="breathing-box",
        title=title,
        text=text,
        language=language,
        tags=tags,
        dense_score=dense,
        bm25_score=bm25,
        fused_confidence=confidence,
    )


def write_artifact(
    directory: Path,
    *,
    weights: list[float] | None = None,
    features: list[str] | None = None,
    feature_version: int = FEATURE_VERSION,
    pivot: float = BM25_SATURATION_PIVOT,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / RERANKER_WEIGHTS_FILE).write_text(
        json.dumps(
            {
                "features": features if features is not None else list(FEATURE_NAMES),
                "weights": (
                    weights
                    if weights is not None
                    else [round(value, 4) for value in DEFAULT_WEIGHTS]
                ),
            }
        ),
        encoding="utf-8",
    )
    (directory / RERANKER_METADATA_FILE).write_text(
        json.dumps(
            {
                "feature_version": feature_version,
                "bm25_saturation_pivot": pivot,
                "trained_on": "day35-train",
            }
        ),
        encoding="utf-8",
    )
    return directory


# -------------------------------------------------------------------- features #


class TestFeatures:
    def test_the_vector_is_exactly_the_named_features(self) -> None:
        features = cross_encoder_features("box breathing counts", candidate())
        assert len(features) == len(FEATURE_NAMES) == len(DEFAULT_WEIGHTS)
        assert features[FEATURE_NAMES.index("bias")] == 1.0

    def test_every_feature_is_bounded_except_the_two_documented_ones(self) -> None:
        features = cross_encoder_features("box breathing counts", candidate())
        unbounded = {"bias", "log_query_length"}
        for name, value in zip(FEATURE_NAMES, features, strict=True):
            if name in unbounded:
                continue
            assert 0.0 <= value <= 1.0, name

    def test_a_matching_candidate_outscores_an_unrelated_one_on_every_overlap(self) -> None:
        query = "box breathing four counts"
        matched = cross_encoder_features(query, candidate())
        unrelated = cross_encoder_features(
            query, candidate(title="Tax filing", text="Submit the return before the deadline.")
        )
        for name in ("token_overlap", "idf_overlap", "title_overlap", "char_trigram_overlap"):
            slot = FEATURE_NAMES.index(name)
            assert matched[slot] > unrelated[slot], name

    def test_idf_weighting_prefers_the_informative_shared_term(self) -> None:
        # ``idf_overlap`` is the fraction of the *query's IDF mass* a candidate
        # covers, so the informative term has to be shared while a filler term
        # is not: matching "crypto" (idf 4) and missing "zzz" (unseen, neutral
        # weight 1) covers 4/5, where matching "the" (idf 0.1) and missing the
        # same "zzz" covers 0.1/1.1. Two candidates that share one token each
        # are not equal, and that is the whole reason the feature is IDF-weighted
        # rather than a count.
        idf = {"crypto": 4.0, "the": 0.1}
        slot = FEATURE_NAMES.index("idf_overlap")
        informative = cross_encoder_features(
            "crypto zzz", candidate(text="crypto assets are volatile"), idf=idf
        )
        filler = cross_encoder_features(
            "the zzz", candidate(text="the assets are volatile"), idf=idf
        )
        assert informative[slot] == pytest.approx(0.8)
        assert filler[slot] == pytest.approx(0.1 / 1.1)
        assert informative[slot] > filler[slot]

    def test_idf_overlap_is_the_query_mass_covered_not_the_count(self) -> None:
        # A single shared token always covers all of a one-token query's mass,
        # whatever that token is worth — the property the test above is built
        # around, pinned so the feature cannot drift into a term count.
        idf = {"rare": 9.0, "common": 0.05}
        slot = FEATURE_NAMES.index("idf_overlap")
        rare = cross_encoder_features("rare", candidate(text="rare word"), idf=idf)
        common = cross_encoder_features("common", candidate(text="common word"), idf=idf)
        assert rare[slot] == common[slot] == pytest.approx(1.0)

    def test_script_match_reads_the_candidate_language(self) -> None:
        slot = FEATURE_NAMES.index("script_match")
        hindi = cross_encoder_features("नींद", candidate(language="hi"))
        english = cross_encoder_features("sleep", candidate(language="hi"))
        assert hindi[slot] == 1.0
        assert english[slot] == 0.0

    def test_tag_match_uses_the_curated_tags(self) -> None:
        slot = FEATURE_NAMES.index("tag_match")
        assert cross_encoder_features("breathing", candidate(tags=("breathing",)))[slot] == 1.0
        assert cross_encoder_features("breathing", candidate(tags=("sleep",)))[slot] == 0.0

    def test_a_blank_query_produces_no_overlap_and_no_crash(self) -> None:
        features = cross_encoder_features("", candidate())
        assert features[FEATURE_NAMES.index("token_overlap")] == 0.0
        assert features[FEATURE_NAMES.index("log_query_length")] == 0.0

    def test_bm25_saturation_is_bounded_and_monotone(self) -> None:
        assert saturate_bm25(0.0) == 0.0
        assert saturate_bm25(-3.0) == 0.0
        assert saturate_bm25(5.0) == pytest.approx(0.5)
        assert saturate_bm25(50.0) < 1.0
        assert saturate_bm25(50.0) > saturate_bm25(5.0)


# --------------------------------------------------------------------- scorers #


class TestScorers:
    def test_the_null_scorer_passes_the_fused_confidence_through(self) -> None:
        scores = NullCrossEncoder().score("query", [candidate(confidence=0.42)])
        assert scores == [pytest.approx(0.42)]

    def test_scores_are_probabilities(self) -> None:
        scores = LexicalCrossEncoder.default().score(
            "box breathing four counts", [candidate(), candidate(title="Taxes", text="File them.")]
        )
        assert all(0.0 <= score <= 1.0 for score in scores)

    def test_the_untrained_scorer_prefers_the_matching_candidate(self) -> None:
        scores = LexicalCrossEncoder.default().score(
            "box breathing four counts",
            [candidate(), candidate(title="Taxes", text="File the return before the deadline.")],
        )
        assert scores[0] > scores[1]

    def test_the_untrained_scorer_can_abstain_on_an_unsupported_candidate(self) -> None:
        # The regression this exists for: an early version leaned on the RRF
        # confidence (weight 2.2 against a bias of -2.4), which put *every*
        # candidate both systems agreed on near 0.94 — including the
        # out-of-scope query, which fused at 0.995 on the real corpus. The
        # scorer must be able to say "nothing here" before anybody fits it.
        scores = LexicalCrossEncoder.default().score(
            "best crypto to buy today",
            [
                candidate(
                    title="Sleep basics", text="Keep the evenings dim and the wake time fixed."
                )
            ],
        )
        assert scores[0] < 0.5

    def test_the_scorer_satisfies_the_protocol(self) -> None:
        assert isinstance(LexicalCrossEncoder.default(), CrossEncoder)
        assert isinstance(NullCrossEncoder(), CrossEncoder)

    def test_a_wrong_length_coefficient_vector_is_refused(self) -> None:
        with pytest.raises(ValueError, match="expected 12 weights"):
            LexicalCrossEncoder([1.0, 2.0])

    def test_the_name_says_untrained_until_coefficients_are_fitted(self) -> None:
        assert LexicalCrossEncoder.default().name.endswith(":untrained")
        assert not LexicalCrossEncoder(DEFAULT_WEIGHTS, metadata={"trained": True}).name.endswith(
            ":untrained"
        )

    def test_a_transformer_checkpoint_is_an_actionable_error_here(self) -> None:
        encoder = TransformerCrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        with pytest.raises(CrossEncoderUnavailable, match=r"sentence-transformers|Colab|notebook"):
            encoder.score("query", [candidate()])


# ------------------------------------------------------------- artifact contract


class TestLoadCrossEncoder:
    def test_a_written_artifact_loads_and_scores_the_same(self, tmp_path: Path) -> None:
        write_artifact(tmp_path)
        loaded = load_cross_encoder(tmp_path, idf={"box": 2.0})
        assert loaded.metadata["trained"] is True
        assert loaded.metadata["trained_on"] == "day35-train"
        expected = LexicalCrossEncoder(DEFAULT_WEIGHTS, idf={"box": 2.0}).score(
            "box breathing", [candidate()]
        )
        assert loaded.score("box breathing", [candidate()]) == pytest.approx(expected)

    def test_a_missing_directory_is_unavailable(self, tmp_path: Path) -> None:
        with pytest.raises(CrossEncoderUnavailable, match="no reranker weights"):
            load_cross_encoder(tmp_path / "nope")

    def test_a_moved_feature_list_is_refused(self, tmp_path: Path) -> None:
        features = [*FEATURE_NAMES[:-1], "something_new"]
        write_artifact(tmp_path, features=features)
        with pytest.raises(CrossEncoderUnavailable, match="feature names"):
            load_cross_encoder(tmp_path)

    def test_a_different_feature_version_is_refused(self, tmp_path: Path) -> None:
        write_artifact(tmp_path, feature_version=FEATURE_VERSION + 1)
        with pytest.raises(CrossEncoderUnavailable, match="feature_version"):
            load_cross_encoder(tmp_path)

    def test_a_different_saturation_pivot_is_refused(self, tmp_path: Path) -> None:
        # The pivot rescales a feature every coefficient was fitted against, so
        # an artefact from another pivot is a shifted model, not a model.
        write_artifact(tmp_path, pivot=BM25_SATURATION_PIVOT * 2)
        with pytest.raises(CrossEncoderUnavailable, match="bm25_saturation_pivot"):
            load_cross_encoder(tmp_path)

    def test_a_truncated_weight_vector_is_refused(self, tmp_path: Path) -> None:
        write_artifact(tmp_path, weights=[0.1] * (len(FEATURE_NAMES) - 1))
        with pytest.raises(CrossEncoderUnavailable, match="expected"):
            load_cross_encoder(tmp_path)

    def test_corrupt_json_is_unavailable_not_a_crash(self, tmp_path: Path) -> None:
        write_artifact(tmp_path)
        (tmp_path / RERANKER_WEIGHTS_FILE).write_text("{not json", encoding="utf-8")
        with pytest.raises(CrossEncoderUnavailable, match="unreadable"):
            load_cross_encoder(tmp_path)

    def test_metadata_is_optional(self, tmp_path: Path) -> None:
        write_artifact(tmp_path)
        (tmp_path / RERANKER_METADATA_FILE).unlink()
        loaded = load_cross_encoder(tmp_path)
        assert loaded.metadata["trained"] is True
