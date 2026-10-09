"""Unit tests for the analyzer chain: fallback, circuit breaking, caching."""

from __future__ import annotations

import time
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.services.nlp.base import EmotionAnalyzer, EmotionResult, build_result, neutral_result
from app.services.nlp.cache import AnalysisCache
from app.services.nlp.chain import AnalyzerChain, ModelHealth
from app.services.nlp.fake import FakeEmotionAnalyzer
from app.services.nlp.keyword import KeywordFallbackAnalyzer
from app.services.nlp.sentiment import SentimentAnalyzer

TEXT = "I got the job and I can't stop smiling"


class Stub(EmotionAnalyzer):
    """A configurable analyzer: a fixed answer, an exception, or nothing."""

    def __init__(
        self, name: str, *, result: EmotionResult | None = None, error: Exception | None = None
    ) -> None:
        self.name = name
        self._result = result
        self._error = error
        self.seen: list[str] = []

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        self.seen.append(text)
        if self._error is not None:
            raise self._error
        if self._result is not None:
            return self._result
        return neutral_result(analyzer=self.name, language=lang)


class RecordingLang(EmotionAnalyzer):
    """Records the language hint it was handed."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.langs: list[str | None] = []

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        self.langs.append(lang)
        return build_result({"joy": 0.9}, analyzer=self.name, language=lang, confidence=0.9)


def joy(name: str = "primary") -> EmotionResult:
    return build_result({"joy": 0.9}, analyzer=name, confidence=0.9)


def flat(name: str) -> EmotionResult:
    """A zero-confidence neutral: "I looked and found nothing"."""
    return neutral_result(analyzer=name)


class TestFallback:
    def test_the_primary_answers_when_it_works(self) -> None:
        primary, backup = Stub("primary", result=joy()), Stub("backup", result=joy("backup"))
        chain = AnalyzerChain(primary, [backup])
        assert chain.analyze(TEXT).analyzer == "primary"
        assert backup.seen == []

    def test_an_exception_falls_through_to_the_next_analyzer(self) -> None:
        primary = Stub("primary", error=RuntimeError("model exploded"))
        backup = Stub("backup", result=joy("backup"))
        chain = AnalyzerChain(primary, [backup])
        result = chain.analyze(TEXT)
        assert result.analyzer == "backup"
        assert result.primary == "joy"

    def test_a_failing_analyzer_is_logged_without_the_text(self) -> None:
        primary = Stub("primary", error=RuntimeError("model exploded"))
        chain = AnalyzerChain(primary, [Stub("backup", result=joy("backup"))])
        with capture_logs() as logs:
            chain.analyze(TEXT)
        warnings = [entry for entry in logs if entry["event"] == "emotion_analyzer_failed"]
        assert len(warnings) == 1
        assert warnings[0]["analyzer"] == "primary"
        assert warnings[0]["error_type"] == "RuntimeError"
        assert warnings[0]["text_length"] == len(TEXT)
        assert len(warnings[0]["text_sha"]) == 16
        assert TEXT not in str(logs)

    def test_a_primary_with_no_opinion_lets_the_fallback_answer(self) -> None:
        """The chain must not stop at an honest "I don't know"."""
        primary = Stub("primary", result=flat("primary"))
        backup = Stub("backup", result=joy("backup"))
        chain = AnalyzerChain(primary, [backup])
        assert chain.analyze(TEXT).analyzer == "backup"

    def test_a_confident_neutral_stops_the_chain(self) -> None:
        considered = build_result({"neutral": 0.9}, analyzer="primary", confidence=0.9)
        backup = Stub("backup", result=joy("backup"))
        chain = AnalyzerChain(Stub("primary", result=considered), [backup])
        assert chain.analyze(TEXT).analyzer == "primary"
        assert backup.seen == []

    def test_when_nobody_has_an_opinion_the_first_neutral_is_reported(self) -> None:
        chain = AnalyzerChain(Stub("primary"), [Stub("backup")])
        result = chain.analyze("the report is on the desk")
        assert result.analyzer == "primary"
        assert result.primary == "neutral"
        assert result.is_informative is False

    def test_every_analyzer_failing_is_neutral_not_an_error(self) -> None:
        boom = RuntimeError("everything is on fire")
        chain = AnalyzerChain(Stub("primary", error=boom), [Stub("backup", error=boom)])
        result = chain.analyze(TEXT)
        assert result.primary == "neutral"
        assert result.analyzer == "unavailable"
        # One degraded *request*, not one per analyzer attempt.
        assert chain.degraded_count == 1

    def test_empty_text_short_circuits_every_analyzer(self) -> None:
        primary, backup = Stub("primary"), Stub("backup")
        chain = AnalyzerChain(primary, [backup])
        for text in ["", "   ", "\n"]:
            result = chain.analyze(text)
            assert result.primary == "neutral"
            assert result.analyzer == "chain"
        assert primary.seen == [] and backup.seen == []

    def test_the_language_hint_reaches_the_analyzer_that_answers(self) -> None:
        primary = Stub("primary", error=RuntimeError("x"))
        backup = RecordingLang("backup")
        result = AnalyzerChain(primary, [backup]).analyze(TEXT, "hi")
        assert backup.langs == ["hi"]
        assert result.language == "hi"


class TestCircuitBreaker:
    def test_a_single_failure_does_not_open_the_circuit(self) -> None:
        health = ModelHealth(failure_threshold=2, cooldown_seconds=60)
        health.record_failure()
        assert health.state == "closed"
        assert health.allow() is True

    def test_repeated_failures_open_the_circuit(self) -> None:
        health = ModelHealth(failure_threshold=2, cooldown_seconds=60)
        health.record_failure()
        health.record_failure()
        assert health.state == "open"
        assert health.allow() is False

    def test_the_circuit_half_opens_after_the_cooldown(self) -> None:
        clock = {"now": 1000.0}
        health = ModelHealth(failure_threshold=1, cooldown_seconds=30, clock=lambda: clock["now"])
        health.record_failure()
        assert health.allow() is False
        clock["now"] += 31
        assert health.state == "half_open"
        assert health.allow() is True

    def test_a_success_closes_the_circuit_again(self) -> None:
        clock = {"now": 0.0}
        health = ModelHealth(failure_threshold=1, cooldown_seconds=10, clock=lambda: clock["now"])
        health.record_failure()
        clock["now"] += 11
        health.record_success(50.0)
        assert health.state == "closed"
        assert health.ema_ms == pytest.approx(50.0)

    def test_the_latency_average_is_smoothed(self) -> None:
        health = ModelHealth()
        health.record_success(100.0)
        health.record_success(200.0)
        assert health.ema_ms == pytest.approx(130.0)  # 0.7*100 + 0.3*200
        assert health.ema_ms is not None

    def test_a_slow_model_is_skipped_even_though_it_never_fails(self) -> None:
        health = ModelHealth(slow_ms=100.0, slow_min_samples=3)
        for _ in range(3):
            health.record_success(500.0)
        assert health.state == "closed"  # slow is not broken
        assert health.allow() is False

    def test_one_slow_call_does_not_switch_the_model_off(self) -> None:
        """A single GC pause or a busy neighbour is not a trend."""
        health = ModelHealth(slow_ms=100.0, slow_min_samples=3)
        health.record_success(5000.0)
        assert health.allow() is True  # one sample proves nothing
        health.record_success(5000.0)
        assert health.allow() is True  # nor do two
        assert health.describe()["samples"] == 2

    def test_a_cold_start_is_not_measured_as_inference(self) -> None:
        """Regression: the model load happened once, and timing it as inference
        pushed the average past the slow threshold, which switched the model
        off for the rest of the process.

        Found by the Day 6 verification run: the first call took 3861ms (load +
        inference) and every later call reported the keyword fallback instead.
        """
        health = ModelHealth(slow_ms=20.0, slow_min_samples=1)

        class Cold(EmotionAnalyzer):
            name = "cold"
            loaded = False

            @property
            def is_warm(self) -> bool:
                return self.loaded

            def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
                if not self.loaded:
                    self.loaded = True
                    time.sleep(0.15)  # standing in for the model load
                return build_result({"joy": 0.9}, analyzer="cold", confidence=0.9)

        chain = AnalyzerChain(Cold(), [Stub("backup", result=joy("backup"))], health=health)
        assert chain.analyze("first").analyzer == "cold"
        # The cold sample was discarded, so the slow rule never engaged.
        assert health.describe()["samples"] == 0
        assert chain.analyze("second").analyzer == "cold"
        assert health.describe()["samples"] == 1

    def test_absurd_sample_gates_are_refused(self) -> None:
        with pytest.raises(ValueError, match="slow_min_samples"):
            ModelHealth(slow_min_samples=0)

    def test_the_chain_stops_routing_to_a_model_that_keeps_failing(self) -> None:
        primary = Stub("primary", error=RuntimeError("down"))
        backup = Stub("backup", result=joy("backup"))
        health = ModelHealth(failure_threshold=2, cooldown_seconds=3600)
        chain = AnalyzerChain(primary, [backup], health=health)

        chain.analyze("one")
        chain.analyze("two")  # second failure opens the circuit
        assert health.state == "open"
        chain.analyze("three")
        # The third request never touched the broken model at all.
        assert len(primary.seen) == 2
        assert health.describe()["skipped_open"] == 1

    def test_the_chain_skips_a_model_that_is_consistently_slow(self) -> None:
        health = ModelHealth(slow_ms=5.0, slow_min_samples=3)

        class Slow(EmotionAnalyzer):
            name = "slow"

            def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
                time.sleep(0.03)  # genuinely slow, not a one-off spike
                return build_result({"joy": 0.9}, analyzer="slow", confidence=0.9)

        backup = Stub("backup", result=joy("backup"))
        chain = AnalyzerChain(Slow(), [backup], health=health)

        # Three slow calls build the evidence...
        for index in range(3):
            assert chain.analyze(f"call {index}").analyzer == "slow"

        # ...and the fourth is routed to the fallback instead.
        assert chain.analyze("call 3").analyzer == "backup"
        assert health.describe()["skipped_slow"] == 1

    def test_the_primary_gate_can_be_disabled(self) -> None:
        health = ModelHealth(failure_threshold=1, cooldown_seconds=3600)
        health.record_failure()
        primary = Stub("primary", result=joy())
        chain = AnalyzerChain(primary, [], health=health, gate_primary=False)
        assert chain.analyze(TEXT).analyzer == "primary"

    def test_absurd_health_settings_are_refused(self) -> None:
        with pytest.raises(ValueError, match="failure_threshold"):
            ModelHealth(failure_threshold=0)
        with pytest.raises(ValueError, match="cooldown_seconds"):
            ModelHealth(cooldown_seconds=-1)


class TestCaching:
    def test_a_repeat_request_is_served_from_the_cache(self) -> None:
        primary = Stub("primary", result=joy())
        chain = AnalyzerChain(primary, cache=AnalysisCache(8))
        first = chain.analyze(TEXT, "en")
        second = chain.analyze(TEXT, "en")
        assert first.cached is False
        assert second.cached is True
        assert second.primary == first.primary
        assert len(primary.seen) == 1

    def test_the_stored_copy_is_not_marked_as_cached(self) -> None:
        """Otherwise the second hit would report a cache hit of a cache hit."""
        chain = AnalyzerChain(Stub("primary", result=joy()), cache=AnalysisCache(8))
        chain.analyze(TEXT, "en")
        assert chain.analyze(TEXT, "en").cached is True
        assert chain.cache is not None
        key = chain.cache.key_for(TEXT, "en", variant=chain._variant())
        stored = chain.cache.get(key)
        assert stored is not None and stored.cached is False

    def test_a_different_language_is_a_different_entry(self) -> None:
        primary = Stub("primary", result=joy())
        chain = AnalyzerChain(primary, cache=AnalysisCache(8))
        chain.analyze(TEXT, "en")
        chain.analyze(TEXT, "hi")
        assert len(primary.seen) == 2

    def test_whitespace_variations_share_one_entry(self) -> None:
        primary = Stub("primary", result=joy())
        chain = AnalyzerChain(primary, cache=AnalysisCache(8))
        chain.analyze(TEXT, "en")
        assert chain.analyze(f"  {TEXT}  ", "en").cached is True
        assert len(primary.seen) == 1

    def test_case_is_not_folded_into_one_entry(self) -> None:
        primary = Stub("primary", result=joy())
        chain = AnalyzerChain(primary, cache=AnalysisCache(8))
        chain.analyze("i am fine", "en")
        chain.analyze("I AM FINE", "en")
        assert len(primary.seen) == 2

    def test_a_total_failure_is_not_cached(self) -> None:
        """A transient outage must not become the answer for ten minutes."""
        cache = AnalysisCache(8)
        primary = Stub("primary", error=RuntimeError("down"))
        chain = AnalyzerChain(primary, [], cache=cache)
        assert chain.analyze(TEXT).analyzer == "unavailable"
        assert len(cache) == 0
        primary._error = None
        primary._result = joy()
        assert chain.analyze(TEXT).analyzer == "primary"

    def test_an_uninformative_answer_is_cached(self) -> None:
        cache = AnalysisCache(8)
        chain = AnalyzerChain(Stub("primary"), [Stub("backup")], cache=cache)
        chain.analyze("the report is on the desk", "en")
        assert len(cache) == 1
        assert chain.analyze("the report is on the desk", "en").cached is True

    def test_a_disabled_cache_stores_nothing(self) -> None:
        primary = Stub("primary", result=joy())
        chain = AnalyzerChain(primary, cache=AnalysisCache(0))
        chain.analyze(TEXT, "en")
        assert chain.analyze(TEXT, "en").cached is False
        assert len(primary.seen) == 2

    def test_no_cache_means_no_cache_object(self) -> None:
        chain = AnalyzerChain(Stub("primary", result=joy()))
        assert chain.cache is None
        assert chain.analyze(TEXT).cached is False


class TestShape:
    def test_the_chain_reports_what_it_is_made_of(self) -> None:
        chain = AnalyzerChain(
            KeywordFallbackAnalyzer(),
            [SentimentAnalyzer()],
            cache=AnalysisCache(4),
        )
        described = chain.describe()
        assert described["primary"] == "keyword"
        assert described["fallbacks"] == ["sentiment"]
        assert described["cache"] is not None
        assert described["degraded"] == 0
        assert chain.model_id is None
        assert chain.candidates[0].name == "keyword"
        assert chain.primary.name == "keyword"
        assert chain.name == "chain"

    def test_the_model_id_comes_from_the_primary(self) -> None:
        chain = AnalyzerChain(FakeEmotionAnalyzer())
        assert chain.model_id == "fake-deterministic-keyword"

    def test_close_reaches_every_candidate(self) -> None:
        closed: list[str] = []

        class Closing(Stub):
            def close(self) -> None:
                closed.append(self.name)

        AnalyzerChain(Closing("primary"), [Closing("backup")]).close()
        assert closed == ["primary", "backup"]

    def test_the_default_batch_implementation_uses_the_chain(self) -> None:
        primary = Stub("primary", result=joy())
        chain = AnalyzerChain(primary, [Stub("backup", result=joy("backup"))])
        results = chain.analyze_many(["a", "b"], "en")
        assert [result.analyzer for result in results] == ["primary", "primary"]

    def test_describe_never_contains_the_analyzed_text(self) -> None:
        chain = AnalyzerChain(Stub("primary", result=joy()), cache=AnalysisCache(4))
        chain.analyze(TEXT, "en")
        assert TEXT not in str(chain.describe())
        assert TEXT not in str(chain.cache.describe() if chain.cache else {})


class TestHealthDescription:
    def test_the_description_carries_the_knobs_an_operator_needs(self) -> None:
        health = ModelHealth(failure_threshold=2, slow_ms=250.0)
        health.record_success(12.3456)
        described: dict[str, Any] = health.describe()
        assert described["state"] == "closed"
        assert described["failures"] == 0
        assert described["ema_ms"] == pytest.approx(12.346)
        assert described["slow_ms_threshold"] == 250.0
        assert described["samples"] == 1
        assert described["skipped_open"] == 0
        assert described["skipped_slow"] == 0

    def test_the_average_is_none_before_the_first_success(self) -> None:
        assert ModelHealth().ema_ms is None
        assert ModelHealth().describe()["ema_ms"] is None
