"""Unit tests for the deterministic Fake used by the offline suite."""

from __future__ import annotations

import time

import pytest

from app.services.nlp.base import EMOTIONS, NEUTRAL
from app.services.nlp.fake import FAKE_KEYWORDS, FakeEmotionAnalyzer


class TestDeterminism:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("I am so happy today", "joy"),
            ("I feel sad and tired", "sadness"),
            ("I am angry with him", "anger"),
            ("I am scared of tomorrow", "fear"),
            ("the exam is tomorrow", "anxiety"),
            ("I feel ashamed", "shame"),
            ("I feel so alone", "loneliness"),
            ("I feel calm now", "calm"),
        ],
    )
    def test_a_keyword_maps_to_its_emotion(self, text: str, expected: str) -> None:
        assert FakeEmotionAnalyzer().analyze(text).primary == expected

    def test_the_same_input_always_gives_the_same_output(self) -> None:
        analyzer = FakeEmotionAnalyzer()
        first = analyzer.analyze("I got the job and I can't stop smiling")
        for _ in range(3):
            assert analyzer.analyze("I got the job and I can't stop smiling") == first

    def test_table_order_beats_input_order(self) -> None:
        """The first keyword in FAKE_KEYWORDS wins, wherever it appears in the
        text - so the outcome cannot depend on dict iteration of the input."""
        analyzer = FakeEmotionAnalyzer()
        assert analyzer.analyze("happy but sad").primary == "joy"
        assert analyzer.analyze("sad but happy").primary == "joy"

    def test_unknown_text_is_neutral(self) -> None:
        result = FakeEmotionAnalyzer().analyze("the quarterly report is due")
        assert result.primary == NEUTRAL
        assert result.confidence == 0.0

    @pytest.mark.parametrize("text", ["", "   ", "\n"])
    def test_empty_text_is_neutral(self, text: str) -> None:
        result = FakeEmotionAnalyzer().analyze(text)
        assert result.primary == NEUTRAL
        assert result.is_informative is False

    def test_matching_is_case_insensitive(self) -> None:
        assert FakeEmotionAnalyzer().analyze("I AM SO HAPPY").primary == "joy"

    def test_the_language_hint_is_recorded(self) -> None:
        assert FakeEmotionAnalyzer().analyze("I am happy", "hi").language == "hi"

    def test_a_model_id_is_reported_so_it_is_obviously_not_real(self) -> None:
        analyzer = FakeEmotionAnalyzer()
        assert analyzer.name == "fake"
        assert analyzer.model_id == "fake-deterministic-keyword"

    def test_batch_matches_single_calls(self) -> None:
        analyzer = FakeEmotionAnalyzer()
        texts = ["I am happy", "", "I feel alone"]
        assert analyzer.analyze_many(texts, "en") == [analyzer.analyze(t, "en") for t in texts]


class TestBookkeeping:
    def test_every_call_is_recorded_with_its_input(self) -> None:
        analyzer = FakeEmotionAnalyzer()
        analyzer.analyze("I am happy", "en")
        analyzer.analyze("I am sad")
        assert analyzer.call_count == 2
        assert analyzer.calls == [("I am happy", "en"), ("I am sad", None)]
        analyzer.reset()
        assert analyzer.call_count == 0


class TestSimulatedFailures:
    def test_an_error_can_be_injected(self) -> None:
        analyzer = FakeEmotionAnalyzer(error=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            analyzer.analyze("I am happy")

    def test_a_limited_number_of_failures_then_recovery(self) -> None:
        analyzer = FakeEmotionAnalyzer(fail_times=2)
        for _ in range(2):
            with pytest.raises(RuntimeError, match="test-simulated"):
                analyzer.analyze("I am happy")
        assert analyzer.analyze("I am happy").primary == "joy"

    def test_latency_can_be_simulated(self) -> None:
        analyzer = FakeEmotionAnalyzer(latency_ms=40)
        started = time.perf_counter()
        analyzer.analyze("I am happy")
        assert (time.perf_counter() - started) * 1000 >= 35

    def test_a_negative_latency_is_clamped_to_zero(self) -> None:
        analyzer = FakeEmotionAnalyzer(latency_ms=-500)
        started = time.perf_counter()
        analyzer.analyze("I am happy")
        assert (time.perf_counter() - started) < 0.2


class TestKeywordTable:
    def test_every_mapped_emotion_is_in_the_taxonomy(self) -> None:
        for keyword, emotion in FAKE_KEYWORDS.items():
            assert emotion in EMOTIONS, (keyword, emotion)
            assert keyword == keyword.casefold(), keyword

    def test_every_emotion_except_neutral_is_reachable(self) -> None:
        assert set(FAKE_KEYWORDS.values()) == set(EMOTIONS) - {NEUTRAL}
