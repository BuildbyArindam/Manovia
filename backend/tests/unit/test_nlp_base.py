"""Unit tests for the emotion contract: taxonomy, result shape, derivation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.nlp.base import (
    EMOTION_DIMENSIONS,
    EMOTIONS,
    NEUTRAL,
    EmotionAnalyzer,
    EmotionResult,
    build_result,
    dimensions_for,
    neutral_result,
    normalize_scores,
)


class TestTaxonomy:
    def test_the_taxonomy_is_the_nine_agreed_labels(self) -> None:
        assert EMOTIONS == (
            "joy",
            "sadness",
            "anger",
            "fear",
            "anxiety",
            "shame",
            "loneliness",
            "calm",
            "neutral",
        )

    def test_every_label_has_a_valence_arousal_anchor(self) -> None:
        """A label without an anchor would silently skew every valence."""
        assert set(EMOTION_DIMENSIONS) == set(EMOTIONS)

    def test_anchors_are_in_range_and_ordered_sensibly(self) -> None:
        for label, (valence, arousal) in EMOTION_DIMENSIONS.items():
            assert -1.0 <= valence <= 1.0, label
            assert 0.0 <= arousal <= 1.0, label
        # The circumplex claims the suite holds the product to.
        assert EMOTION_DIMENSIONS["joy"][0] > 0 > EMOTION_DIMENSIONS["sadness"][0]
        assert EMOTION_DIMENSIONS["fear"][1] > EMOTION_DIMENSIONS["sadness"][1]
        assert EMOTION_DIMENSIONS["anger"][1] > EMOTION_DIMENSIONS["calm"][1]
        assert EMOTION_DIMENSIONS[NEUTRAL] == (0.0, 0.3)


class TestEmotionResult:
    def _valid(self, **overrides: object) -> EmotionResult:
        payload: dict[str, object] = {
            "primary": "joy",
            "scores": normalize_scores({"joy": 1.0}),
            "valence": 0.8,
            "arousal": 0.6,
            "analyzer": "test",
        }
        payload.update(overrides)
        return EmotionResult(**payload)

    def test_a_well_formed_result_is_accepted(self) -> None:
        result = self._valid()
        assert result.primary == "joy"
        assert result.scores["joy"] == 1.0
        assert result.intensity == 1.0
        assert result.is_informative is True

    @pytest.mark.parametrize("field", ["primary", "scores"])
    def test_an_unknown_label_is_rejected(self, field: str) -> None:
        if field == "primary":
            with pytest.raises(ValidationError, match="unknown emotion"):
                self._valid(primary="ecstasy")
        else:
            with pytest.raises(ValidationError, match="unknown emotion label"):
                self._valid(scores={"ecstasy": 1.0}, primary="ecstasy")

    def test_out_of_range_valence_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"valence must be within \[-1, 1\]"):
            self._valid(valence=1.5)
        with pytest.raises(ValidationError, match=r"valence must be within \[-1, 1\]"):
            self._valid(valence=-1.5)

    def test_out_of_range_arousal_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"arousal must be within \[0, 1\]"):
            self._valid(arousal=-0.1)
        with pytest.raises(ValidationError, match=r"arousal must be within \[0, 1\]"):
            self._valid(arousal=1.2)

    def test_a_score_outside_zero_one_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be within \\[0, 1\\]"):
            self._valid(scores={"joy": 1.4})

    def test_empty_scores_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="scores must not be empty"):
            self._valid(scores={})

    def test_confidence_outside_zero_one_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="confidence must be within"):
            self._valid(confidence=2.0)

    def test_primary_must_agree_with_the_highest_score(self) -> None:
        """A mismatch is a bug in an analyzer, not a modelling opinion."""
        with pytest.raises(ValidationError, match="is not the highest score"):
            self._valid(primary="sadness", scores=normalize_scores({"joy": 1.0}))

    def test_a_confident_neutral_is_informative_but_a_bare_one_is_not(self) -> None:
        flat = neutral_result(analyzer="test")
        assert flat.primary == NEUTRAL
        assert flat.confidence == 0.0
        assert flat.is_informative is False
        considered = build_result({NEUTRAL: 0.9}, analyzer="test", confidence=0.9)
        assert considered.primary == NEUTRAL
        assert considered.is_informative is True


class TestDerivation:
    def test_scores_are_normalised_over_every_label(self) -> None:
        scores = normalize_scores({"joy": 3.0, "calm": 1.0})
        assert set(scores) == set(EMOTIONS)
        assert scores["joy"] == pytest.approx(0.75)
        assert scores["calm"] == pytest.approx(0.25)
        assert sum(scores.values()) == pytest.approx(1.0, abs=1e-6)

    def test_negative_weights_are_clamped_not_subtracted(self) -> None:
        scores = normalize_scores({"joy": -2.0, "calm": 1.0})
        assert scores["joy"] == 0.0
        assert scores["calm"] == 1.0

    def test_no_signal_at_all_becomes_neutral(self) -> None:
        scores = normalize_scores({})
        assert scores[NEUTRAL] == 1.0
        assert sum(scores.values()) == pytest.approx(1.0)

    def test_unknown_labels_are_ignored_rather_than_raising(self) -> None:
        scores = normalize_scores({"joy": 1.0, "schadenfreude": 5.0})
        assert scores["joy"] == 1.0

    def test_valence_and_arousal_are_weighted_and_clamped(self) -> None:
        valence, arousal = dimensions_for(normalize_scores({"joy": 0.5, "sadness": 0.5}))
        expected_valence = (EMOTION_DIMENSIONS["joy"][0] + EMOTION_DIMENSIONS["sadness"][0]) / 2
        assert valence == pytest.approx(expected_valence, abs=1e-6)
        assert 0.0 <= arousal <= 1.0
        extreme, _ = dimensions_for(normalize_scores({"joy": 1.0}))
        assert -1.0 <= extreme <= 1.0

    def test_build_result_picks_the_top_label_and_keeps_raw_confidence(self) -> None:
        result = build_result(
            {"sadness": 0.7, "loneliness": 0.4}, analyzer="test", model="m", language="en"
        )
        assert result.primary == "sadness"
        # Raw model confidence survives normalisation.
        assert result.confidence == pytest.approx(0.7)
        assert result.model == "m"
        assert result.language == "en"
        assert result.valence == pytest.approx(EMOTION_DIMENSIONS["sadness"][0], abs=0.15)

    def test_build_result_derives_confidence_when_not_given(self) -> None:
        result = build_result({"joy": 0.42}, analyzer="test")
        assert result.confidence == pytest.approx(0.42)
        assert build_result({}, analyzer="test").confidence == 0.0

    def test_build_result_clamps_confidence_into_range(self) -> None:
        assert build_result({"joy": 1.0}, analyzer="t", confidence=4.0).confidence == 1.0
        assert build_result({"joy": 1.0}, analyzer="t", confidence=-1.0).confidence == 0.0

    def test_neutral_result_is_the_zero_reading(self) -> None:
        result = neutral_result(analyzer="keyword", language="hi", truncated=True)
        assert result.primary == NEUTRAL
        assert result.valence == 0.0
        assert result.arousal == EMOTION_DIMENSIONS[NEUTRAL][1]
        assert result.language == "hi"
        assert result.truncated is True
        assert result.analyzer == "keyword"

    def test_truncation_and_cache_flags_round_trip(self) -> None:
        result = build_result({"joy": 1.0}, analyzer="t")
        assert result.truncated is False
        assert result.cached is False
        assert result.model_copy(update={"cached": True}).cached is True


class TestAnalyzerInterface:
    def test_the_base_class_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            EmotionAnalyzer()  # type: ignore[abstract]

    def test_the_default_batch_implementation_loops(self) -> None:
        class One(EmotionAnalyzer):
            name = "one"

            def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
                return neutral_result(analyzer=self.name, language=lang)

        analyzer = One()
        results = analyzer.analyze_many(["a", "b", ""], "en")
        assert len(results) == 3
        assert all(result.language == "en" for result in results)
        assert analyzer.model_id is None
        assert analyzer.describe() == {"analyzer": "one", "model": None}
        analyzer.close()  # no-op by default
