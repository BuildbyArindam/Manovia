"""Unit tests for the polarity sentiment analyzer (the legacy-chatbot idea)."""

from __future__ import annotations

import pytest

from app.services.nlp.base import EMOTION_DIMENSIONS, NEUTRAL
from app.services.nlp.sentiment import (
    NEGATIVE_TERMS,
    POSITIVE_TERMS,
    SentimentAnalyzer,
    emotion_for,
    polarity,
)


@pytest.fixture
def analyzer() -> SentimentAnalyzer:
    return SentimentAnalyzer()


class TestPolarity:
    def test_positive_text_is_positive(self) -> None:
        assert polarity("today was absolutely wonderful and I feel grateful") > 0.3

    def test_negative_text_is_negative(self) -> None:
        assert polarity("everything is terrible and I am drained") < -0.3

    def test_the_scale_is_bounded(self) -> None:
        text = " ".join(["wonderful amazing excellent great"] * 10)
        assert -1.0 <= polarity(text) <= 1.0

    def test_one_word_cannot_reach_the_extreme(self) -> None:
        """tanh needs a lot of evidence before a reading saturates."""
        assert abs(polarity("wonderful")) < 0.5
        assert abs(polarity("awful")) < 0.5

    def test_empty_text_is_zero(self) -> None:
        assert polarity("") == 0.0
        assert polarity("   ") == 0.0

    def test_unknown_text_is_zero(self) -> None:
        assert polarity("the quarterly report is on the desk") == 0.0

    def test_negation_flips_a_positive_word(self) -> None:
        assert polarity("I am happy") > 0
        assert polarity("I am not happy") < 0

    def test_negation_flips_a_negative_word(self) -> None:
        assert polarity("I am sad") < 0
        assert polarity("I am not sad") > 0

    def test_the_clause_after_but_dominates(self) -> None:
        """The sentence people actually type before saying something hard."""
        assert polarity("I am ok, but I feel awful") < 0
        assert polarity("it is awful, but I feel calm") > 0

    def test_without_the_contrast_the_same_words_read_the_other_way(self) -> None:
        assert polarity("I am ok and I feel awful") > polarity("I am ok, but I feel awful")

    def test_romanised_and_script_terms_count(self) -> None:
        assert polarity("main bahut udaas hoon") < 0
        assert polarity("আমার মন খারাপ") < 0
        assert polarity("আমি ভালো আছি") > 0

    def test_emphasis_pushes_further_from_zero(self) -> None:
        plain = polarity("I am so sad")
        shouted = polarity("I AM SO SAD!!!")
        assert abs(shouted) >= abs(plain)

    def test_polarity_is_deterministic(self) -> None:
        text = "I feel awful but I am grateful for my friend"
        assert polarity(text) == polarity(text)


class TestBands:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0.9, "joy"),
            (0.35, "joy"),
            (0.2, "calm"),
            (0.12, "calm"),
            (0.0, NEUTRAL),
            (-0.11, NEUTRAL),
            (-0.12, "sadness"),
            (-1.0, "sadness"),
        ],
    )
    def test_the_bands_cover_the_whole_range(self, score: float, expected: str) -> None:
        assert emotion_for(score) == expected


class TestAnalyzer:
    def test_positive_text_reports_joy(self, analyzer: SentimentAnalyzer) -> None:
        result = analyzer.analyze("today was absolutely wonderful and I feel supported")
        assert result.primary == "joy"
        assert result.valence > 0

    def test_negative_text_reports_sadness(self, analyzer: SentimentAnalyzer) -> None:
        result = analyzer.analyze("everything is terrible and I am drained")
        assert result.primary == "sadness"
        assert result.valence < 0

    def test_valence_is_the_polarity_score_not_the_anchor(
        self, analyzer: SentimentAnalyzer
    ) -> None:
        text = "everything is terrible and I am drained"
        result = analyzer.analyze(text)
        assert result.valence == pytest.approx(polarity(text))
        assert result.valence != EMOTION_DIMENSIONS["sadness"][0]

    def test_arousal_comes_from_the_banded_emotion(self, analyzer: SentimentAnalyzer) -> None:
        result = analyzer.analyze("everything is terrible and I am drained")
        assert result.arousal == EMOTION_DIMENSIONS[result.primary][1]

    def test_empty_text_is_neutral_with_no_confidence(self, analyzer: SentimentAnalyzer) -> None:
        for text in ["", "   "]:
            result = analyzer.analyze(text)
            assert result.primary == NEUTRAL
            assert result.valence == 0.0
            assert result.confidence == 0.0
            assert result.is_informative is False

    def test_unreadable_text_is_neutral(self, analyzer: SentimentAnalyzer) -> None:
        result = analyzer.analyze("the quarterly report is on the desk")
        assert result.primary == NEUTRAL
        assert result.confidence == 0.0

    def test_confidence_tracks_the_strength_of_the_reading(
        self, analyzer: SentimentAnalyzer
    ) -> None:
        weak = analyzer.analyze("I am ok")
        strong = analyzer.analyze("today was absolutely wonderful and I feel grateful")
        assert 0.0 <= weak.confidence < strong.confidence <= 1.0

    def test_the_language_hint_is_carried_through(self, analyzer: SentimentAnalyzer) -> None:
        assert analyzer.analyze("I am sad", "hi").language == "hi"

    def test_name_and_model(self, analyzer: SentimentAnalyzer) -> None:
        assert analyzer.name == "sentiment"
        assert analyzer.model_id is None
        assert analyzer.term_count == len(POSITIVE_TERMS) + len(NEGATIVE_TERMS)

    def test_custom_lexicons_can_be_injected(self) -> None:
        custom = SentimentAnalyzer({"brilliant": 1.0}, {"awful": 1.0})
        assert custom.analyze("brilliant").primary == "joy"
        assert custom.analyze("awful").primary == "sadness"
        assert custom.analyze("I am happy").primary == NEUTRAL
        assert custom.term_count == 2

    def test_the_lexicons_are_disjoint_enough_to_be_meaningful(self) -> None:
        """A term in both lists is read as negative - so overlaps must be rare
        and deliberate, not accidental duplicates."""
        overlap = set(POSITIVE_TERMS) & set(NEGATIVE_TERMS)
        assert not overlap, overlap
