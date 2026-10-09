"""The analyzer chain: try the model, fall back to the lexicons, cache the win.

This is where "graceful degradation" actually happens, and it has two triggers
rather than one:

* **unavailable** - the model fails to load or throws during inference. After
  :class:`ModelHealth` sees a few failures it opens a circuit for a cooldown,
  so a broken model id costs one warning and a cooldown, not a failed import
  per request.
* **too slow** - an exponential moving average of the model's own latency. If
  the model is consistently slower than ``slow_ms`` the chain stops routing to
  it, because a wellbeing companion that stalls mid-conversation is worse than
  one that answers with a cruder reading.

The chain **never raises**. If every analyzer fails the caller gets a neutral
result with ``analyzer="unavailable"`` - degraded, but not an error page in
front of someone who just said something difficult.

Failed-everything results are deliberately *not* cached: a transient model
failure must not be served as truth for the next ten minutes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

import structlog

from app.services.nlp.base import EmotionAnalyzer, EmotionResult, neutral_result
from app.services.nlp.cache import AnalysisCache, text_fingerprint

#: Smoothing for the latency average: new sample gets 30% of the weight.
EMA_ALPHA: float = 0.3


class ModelHealth:
    """A circuit breaker plus a latency governor for the primary analyzer."""

    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 60.0,
        slow_ms: float = 1500.0,
        slow_min_samples: int = 3,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")
        if slow_min_samples < 1:
            raise ValueError("slow_min_samples must be at least 1")
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._slow_ms = slow_ms
        self._slow_min_samples = slow_min_samples
        self._samples = 0
        self._clock = clock or time.monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self._ema_ms: float | None = None
        self._lock = threading.Lock()
        self._skipped_slow = 0
        self._skipped_open = 0

    @property
    def ema_ms(self) -> float | None:
        """Latency moving average, or ``None`` before the first success."""
        with self._lock:
            return self._ema_ms

    @property
    def state(self) -> str:
        """``closed`` (routing), ``open`` (cooling down) or ``half_open``."""
        with self._lock:
            return self._state_locked()

    def _state_locked(self) -> str:
        if self._failures < self._failure_threshold:
            return "closed"
        # record_failure always stamps both; a missing stamp means "just now".
        opened = self._opened_at if self._opened_at is not None else self._clock()
        return "half_open" if self._clock() - opened >= self._cooldown_seconds else "open"

    def allow(self) -> bool:
        """Should the primary analyzer be tried for this request?"""
        with self._lock:
            state = self._state_locked()
            if state == "open":
                self._skipped_open += 1
                return False
            ema = self._ema_ms
            # A minimum sample count: one slow call must not switch the model
            # off, or a single GC pause would do it.
            if ema is not None and self._samples >= self._slow_min_samples and ema > self._slow_ms:
                self._skipped_slow += 1
                return False
            return True

    def record_success(self, duration_ms: float) -> None:
        with self._lock:
            sample = max(0.0, float(duration_ms))
            self._ema_ms = (
                sample
                if self._ema_ms is None
                else ((1 - EMA_ALPHA) * self._ema_ms + EMA_ALPHA * sample)
            )
            self._samples += 1
            # A success after a half-open probe closes the circuit again.
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            self._opened_at = self._clock()

    def describe(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state_locked(),
                "failures": self._failures,
                "ema_ms": round(self._ema_ms, 3) if self._ema_ms is not None else None,
                "samples": self._samples,
                "slow_ms_threshold": self._slow_ms,
                "skipped_open": self._skipped_open,
                "skipped_slow": self._skipped_slow,
            }


class AnalyzerChain(EmotionAnalyzer):
    """Primary analyzer plus ordered fallbacks, with an LRU cache in front."""

    name = "chain"

    def __init__(
        self,
        primary: EmotionAnalyzer,
        fallbacks: Sequence[EmotionAnalyzer] = (),
        *,
        cache: AnalysisCache | None = None,
        health: ModelHealth | None = None,
        gate_primary: bool = True,
    ) -> None:
        self._candidates: tuple[EmotionAnalyzer, ...] = (primary, *fallbacks)
        self._cache = cache
        self._health = health or ModelHealth()
        self._gate_primary = gate_primary
        self._lock = threading.Lock()
        self._degraded = 0

    @property
    def primary(self) -> EmotionAnalyzer:
        return self._candidates[0]

    @property
    def candidates(self) -> tuple[EmotionAnalyzer, ...]:
        return self._candidates

    @property
    def cache(self) -> AnalysisCache | None:
        return self._cache

    @property
    def model_id(self) -> str | None:
        return self.primary.model_id

    @property
    def degraded_count(self) -> int:
        """Requests the primary analyzer could not serve.

        Counted once per *request*, however many analyzers it fell through:
        the number is meant to be a rate, not a tally of attempts.
        """
        with self._lock:
            return self._degraded

    def _mark_degraded(self) -> None:
        with self._lock:
            self._degraded += 1

    def _variant(self) -> str:
        """Cache namespace: a model swap must not serve the old model's answer."""
        return f"{self.primary.name}:{self.primary.model_id or 'none'}"

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        cache = self._cache
        key = cache.key_for(text, lang, variant=self._variant()) if cache is not None else None
        if cache is not None and key is not None:
            cached = cache.get(key)
            if cached is not None:
                return cached.model_copy(update={"cached": True})

        # Nothing to analyze: do not spend three analyzers proving it.
        if not text or not text.strip():
            empty = neutral_result(analyzer=self.name, model=self.model_id, language=lang)
            if cache is not None and key is not None:
                cache.set(key, empty)
            return empty

        first_neutral: EmotionResult | None = None
        degraded = False
        for index, analyzer in enumerate(self._candidates):
            if index == 0 and self._gate_primary and not self._health.allow():
                continue
            warm_before = analyzer.is_warm
            started = time.perf_counter()
            try:
                result = analyzer.analyze(text, lang)
            except Exception as exc:  # degrade, never propagate
                if index == 0:
                    self._health.record_failure()
                degraded = True
                structlog.get_logger().warning(
                    "emotion_analyzer_failed",
                    analyzer=analyzer.name,
                    error_type=type(exc).__name__,
                    # A fingerprint, never the text (AGENTS.md rule 5).
                    text_sha=text_fingerprint(text),
                    text_length=len(text),
                )
                continue

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if index == 0:
                # Skip the sample that paid for the model load: it measures
                # startup, not inference, and would trip the slow rule once.
                if warm_before:
                    self._health.record_success(elapsed_ms)
            else:
                degraded = True

            if not result.is_informative:
                # "I looked and found nothing" - give the next analyzer a turn,
                # but remember the first answer so the chain can still report
                # which analyzer it gave up on.
                first_neutral = first_neutral or result
                continue

            if degraded:
                self._mark_degraded()
            if cache is not None and key is not None:
                cache.set(key, result)
            return result

        if first_neutral is not None:
            # Nobody had an opinion. Report the primary's honest "neutral"
            # rather than inventing one, and cache it: the answer will not
            # change until the model or the lexicon does.
            if degraded:
                self._mark_degraded()
            if cache is not None and key is not None:
                cache.set(key, first_neutral)
            return first_neutral

        # Every analyzer failed. Not cached: this must not become sticky.
        self._mark_degraded()
        return neutral_result(analyzer="unavailable", model=self.model_id, language=lang)

    def describe(self) -> dict[str, object]:
        return {
            "analyzer": self.name,
            "primary": self.primary.name,
            "primary_model": self.primary.model_id,
            "fallbacks": [candidate.name for candidate in self._candidates[1:]],
            "health": self._health.describe(),
            "cache": self._cache.describe() if self._cache is not None else None,
            "degraded": self.degraded_count,
        }

    def close(self) -> None:
        for candidate in self._candidates:
            candidate.close()
