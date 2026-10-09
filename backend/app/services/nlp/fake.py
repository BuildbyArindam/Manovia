"""A deterministic, offline emotion analyzer for tests.

AGENTS.md requires every external dependency to sit behind an interface with a
Fake, and the suite must pass fully offline. This is that Fake: no model, no
network, no randomness, same input → same output, forever.

It is deliberately *not* a good analyzer. It matches a fixed keyword table and
otherwise reports ``neutral``; it exists to exercise the plumbing (caching,
fallback chains, endpoints, log redaction) rather than to produce insight.

It can also misbehave on demand, which is what makes the degradation paths
testable: ``error`` raises, ``fail_times`` raises for the first N calls and
then recovers, and ``latency_ms`` makes it slow enough to trip the chain's
"too slow, use the fallback" rule.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Final

from app.services.nlp.base import EmotionAnalyzer, EmotionResult, build_result, neutral_result

#: Fixed keyword → emotion table. Ordered, so ties always resolve the same way.
FAKE_KEYWORDS: Final[Mapping[str, str]] = {
    # joy
    "happy": "joy",
    "joy": "joy",
    "smiling": "joy",
    "smile": "joy",
    "great": "joy",
    "excited": "joy",
    "grateful": "joy",
    "job": "joy",
    "khush": "joy",
    "खुश": "joy",
    # sadness
    "sad": "sadness",
    "crying": "sadness",
    "hopeless": "sadness",
    "depressed": "sadness",
    "udaas": "sadness",
    # anger
    "angry": "anger",
    "furious": "anger",
    "hate": "anger",
    "gussa": "anger",
    # fear
    "scared": "fear",
    "afraid": "fear",
    "terrified": "fear",
    "dar": "fear",
    "darr": "fear",
    "डर": "fear",
    # anxiety
    "anxious": "anxiety",
    "worried": "anxiety",
    "exam": "anxiety",
    "pareshan": "anxiety",
    # shame
    "ashamed": "shame",
    "guilty": "shame",
    "sharminda": "shame",
    # loneliness
    "lonely": "loneliness",
    "alone": "loneliness",
    "akela": "loneliness",
    # calm
    "calm": "calm",
    "peaceful": "calm",
    "relaxed": "calm",
    "fine": "calm",
    "theek": "calm",
}

#: Score the matched keyword gets; the rest of the mass goes to neutral.
FAKE_PRIMARY_SCORE: Final[float] = 0.9


class FakeEmotionAnalyzer(EmotionAnalyzer):
    """Deterministic keyword analyzer for tests. Never touches the network."""

    name = "fake"

    def __init__(
        self,
        *,
        keywords: Mapping[str, str] = FAKE_KEYWORDS,
        latency_ms: float = 0.0,
        error: Exception | None = None,
        fail_times: int = 0,
    ) -> None:
        self._keywords = dict(keywords)
        self._latency_ms = max(0.0, latency_ms)
        self._error = error
        self._fail_times = max(0, fail_times)
        self.calls: list[tuple[str, str | None]] = []

    @property
    def model_id(self) -> str | None:
        return "fake-deterministic-keyword"

    @property
    def call_count(self) -> int:
        """How many times :meth:`analyze` was entered (cache hits skip it)."""
        return len(self.calls)

    def reset(self) -> None:
        """Forget recorded calls (tests that reuse one instance)."""
        self.calls.clear()

    def _wait(self) -> None:
        if self._latency_ms:
            time.sleep(self._latency_ms / 1000.0)

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        self.calls.append((text, lang))
        self._wait()
        if self._error is not None:
            raise self._error
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("fake analyzer failure (test-simulated)")
        return self._result(text, lang)

    def analyze_many(self, texts: Sequence[str], lang: str | None = None) -> list[EmotionResult]:
        return [self.analyze(text, lang) for text in texts]

    def _result(self, text: str, lang: str | None) -> EmotionResult:
        if not text or not text.strip():
            return neutral_result(analyzer=self.name, model=self.model_id, language=lang)
        haystack = text.casefold()
        # Fixed table order: the first keyword in FAKE_KEYWORDS that appears
        # wins, so the outcome never depends on dict iteration of the input.
        for keyword, emotion in self._keywords.items():
            if keyword in haystack:
                return build_result(
                    {emotion: FAKE_PRIMARY_SCORE},
                    analyzer=self.name,
                    model=self.model_id,
                    language=lang,
                    confidence=FAKE_PRIMARY_SCORE,
                )
        return neutral_result(analyzer=self.name, model=self.model_id, language=lang)
