"""Shared fixtures for the LLM tests.

Everything here is offline and deterministic. Two helpers do the heavy lifting:

* :class:`FakeClock` — a monotonic clock a test advances by hand, so a 60-second
  circuit-breaker cooldown costs one assignment instead of one minute.
* :func:`recorder` — an ``asyncio.sleep`` replacement that records the waits
  instead of waiting, so retry tests assert on the backoff schedule without
  making the suite slower.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.services.llm.base import LLMMessage, LLMProvider, LLMResult, ProviderDown
from app.services.llm.fake_provider import FakeCall, FakeLLMProvider


class FakeClock:
    """A clock a test controls. ``advance(seconds)`` moves it forward."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class SleepRecorder:
    """Stands in for ``asyncio.sleep`` and remembers every wait."""

    waits: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)

    @property
    def total(self) -> float:
        return sum(self.waits)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sleeper() -> SleepRecorder:
    return SleepRecorder()


def messages(*texts: str) -> list[LLMMessage]:
    """Build a user/assistant alternation from bare strings."""
    out: list[LLMMessage] = []
    for index, text in enumerate(texts):
        role = "user" if index % 2 == 0 else "assistant"
        out.append(LLMMessage(role=role, content=text))
    return out


def last_call(provider: FakeLLMProvider) -> FakeCall:
    """The most recent recorded call, or a clear failure if there was none.

    Tests read this constantly, and "AttributeError: 'NoneType'" when a
    provider was never called tells nobody anything.
    """
    call = provider.last_call
    assert call is not None, "the provider was never called"
    return call


def last_stream_call(provider: FakeLLMProvider) -> FakeCall:
    call = provider.stream_calls[-1] if provider.stream_calls else None
    assert call is not None, "the provider was never streamed from"
    return call


def raw(result: LLMResult) -> dict[str, Any]:
    """The result's metadata dict, never ``None``."""
    return dict(result.raw or {})


class ScriptedProvider(LLMProvider):
    """A provider whose behaviour is a list of per-call outcomes.

    Used where :class:`~app.services.llm.fake_provider.FakeLLMProvider`'s knobs
    are not expressive enough — most importantly for a **stream that fails
    partway through**, which is the case that must *not* be retried.
    """

    def __init__(
        self,
        *outcomes: str | Exception | Sequence[str],
        name: str = "scripted",
        configured: bool = True,
        latency_ms: float = 0.0,
    ) -> None:
        self._outcomes = list(outcomes)
        self.name = name
        self._configured = configured
        self._latency_ms = latency_ms
        self.calls = 0
        self.stream_calls = 0
        self._served = 0

    @property
    def model_id(self) -> str | None:
        return f"{self.name}-model"

    @property
    def is_configured(self) -> bool:
        return self._configured

    def _next(self) -> str | Exception | Sequence[str]:
        """Consume the next outcome; the last one repeats forever."""
        if not self._outcomes:
            return ""
        index = min(self._served, len(self._outcomes) - 1)
        self._served += 1
        return self._outcomes[index]

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> LLMResult:
        self.calls += 1
        if self._latency_ms:
            import asyncio

            await asyncio.sleep(self._latency_ms / 1000.0)
        outcome = self._next()
        if isinstance(outcome, Exception):
            raise outcome
        return LLMResult(text=str(outcome), provider=self.name, model=self.model_id)

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        self.stream_calls += 1
        outcome = self._next()
        if isinstance(outcome, Exception):
            raise outcome
        chunks = outcome if isinstance(outcome, (list, tuple)) else [str(outcome)]
        for chunk in chunks:
            yield chunk


async def collect(provider: LLMProvider, *texts: str, **kwargs: object) -> str:
    """Drain :meth:`LLMProvider.stream` into one string."""
    parts: list[str] = []
    async for chunk in provider.stream(messages(*texts), **kwargs):  # type: ignore[arg-type]
        parts.append(chunk)
    return "".join(parts)


def always_down() -> Exception:
    """A retryable failure, for tests that want the chain to move on."""
    return ProviderDown("simulated outage")
