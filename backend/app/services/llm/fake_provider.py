"""A deterministic, offline LLM provider for tests.

AGENTS.md: every external dependency sits behind an interface with a Fake, and
the suite passes fully offline. This is that Fake for the LLM layer.

It is scriptable per test in three independent ways, and all three are
deterministic:

* **a reply queue** — ``script("first reply", "second reply")``; the Nth call
  returns the Nth reply, the last one repeating forever. This is how the
  fallback chain is tested: the primary is scripted to fail, the next to
  answer.
* **failures** — ``script_error(ProviderDown(), times=2)`` or ``fail_times=N``
  for the first N calls, which is what exercises retry and the breaker.
* **latency** — ``latency_ms`` makes calls slow enough to trip the hard
  timeout without a test that actually waits.

Every call is recorded in full (:attr:`FakeLLMProvider.calls`) so a test can
assert on the *exact payload* the provider received. That is the mechanism
behind "prove no PII reaches the provider": the redaction test reads
``provider.calls[0].messages`` and asserts the email and phone number are gone.

Deterministic means deterministic: no randomness, no clock-dependent text, and
the default reply is a fixed string that does not depend on the input.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from typing import Final

from app.services.llm.base import (
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMResult,
    LLMUsage,
)

#: What the Fake says when a test has not scripted anything. Fixed, warm, and
#: deliberately unrelated to the input: a Fake that reacts to input is a Fake
#: that can leak input into an assertion or a log.
DEFAULT_REPLY: Final[str] = (
    "Thanks for telling me that. That sounds like a lot to carry. What has today been like for you?"
)

#: Model id reported in results. Not a real model; named so a test failure
#: message says plainly which double answered.
FAKE_MODEL: Final = "fake-deterministic-v1"


class FakeCall:
    """One recorded call: everything the provider was asked for."""

    __slots__ = ("index", "max_tokens", "messages", "system", "temperature")

    def __init__(
        self,
        *,
        index: int,
        messages: Sequence[LLMMessage],
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> None:
        self.index = index
        self.messages: tuple[LLMMessage, ...] = tuple(messages)
        self.system = system
        self.max_tokens = max_tokens
        self.temperature = temperature

    @property
    def user_text(self) -> str:
        """Concatenated user-role content — what a PII assertion looks at."""
        return "\n".join(m.content for m in self.messages if m.role == "user")

    @property
    def all_text(self) -> str:
        """Every role's content plus the system prompt, for blanket assertions."""
        parts = [self.system or ""]
        parts.extend(m.content for m in self.messages)
        return "\n".join(parts)

    def as_payload(self) -> dict[str, object]:
        """The request as a plain dict — the exact thing an API would receive."""
        return {
            "messages": [{"role": m.role, "content": m.content} for m in self.messages],
            "system": self.system,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"FakeCall(index={self.index}, messages={len(self.messages)})"


class FakeLLMProvider(LLMProvider):
    """Offline, scriptable :class:`LLMProvider`. Never touches the network."""

    name = "fake"

    def __init__(
        self,
        *,
        replies: Sequence[str] | None = None,
        error: Exception | None = None,
        fail_times: int = 0,
        latency_ms: float = 0.0,
        chunks: Sequence[str] | None = None,
        configured: bool = True,
        model: str = FAKE_MODEL,
    ) -> None:
        self._replies: list[str] = list(replies) if replies is not None else [DEFAULT_REPLY]
        self._error = error
        self._fail_times = max(0, fail_times)
        self._latency_ms = max(0.0, latency_ms)
        self._chunks = list(chunks) if chunks is not None else None
        self._configured = configured
        self._model = model
        self.calls: list[FakeCall] = []
        self.stream_calls: list[FakeCall] = []
        self._scripted_errors: list[tuple[Exception, int]] = []

    # --- scripting ---

    def script(self, *replies: str) -> FakeLLMProvider:
        """Queue replies; the last repeats forever."""
        if not replies:
            raise ValueError("script() needs at least one reply")
        self._replies = list(replies)
        return self

    def script_error(self, error: Exception, *, times: int = 1) -> FakeLLMProvider:
        """Raise ``error`` for the next ``times`` calls, then behave normally."""
        self._scripted_errors.append((error, max(1, times)))
        return self

    def script_stream(self, *chunks: str) -> FakeLLMProvider:
        """Yield ``chunks`` from :meth:`stream` instead of splitting a reply."""
        self._chunks = list(chunks)
        return self

    def reset(self) -> None:
        """Forget recorded calls and scripted failures (keeps replies)."""
        self.calls.clear()
        self.stream_calls.clear()
        self._scripted_errors.clear()

    # --- introspection for tests ---

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> FakeCall | None:
        return self.calls[-1] if self.calls else None

    @property
    def model_id(self) -> str | None:
        return self._model

    @property
    def is_configured(self) -> bool:
        return self._configured

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "model": self._model,
            "configured": self._configured,
            "calls": len(self.calls),
        }

    # --- the interface ---

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> LLMResult:
        call = FakeCall(
            index=len(self.calls),
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.calls.append(call)
        await self._wait()
        self._raise_if_scripted()
        if self._error is not None:
            raise self._error
        if self._fail_times > 0:
            self._fail_times -= 1
            raise _as_llm_error(RuntimeError("fake provider failure (test-simulated)"))
        return LLMResult(
            text=self._reply_for(call.index),
            provider=self.name,
            model=self._model,
            finish_reason="stop",
            usage=LLMUsage(input_tokens=_estimate_tokens(call.all_text), output_tokens=8),
            latency_ms=0.0,
        )

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        call = FakeCall(
            index=len(self.stream_calls),
            messages=messages,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self.stream_calls.append(call)
        await self._wait()
        self._raise_if_scripted()
        if self._error is not None:
            raise self._error
        if self._fail_times > 0:
            self._fail_times -= 1
            raise _as_llm_error(RuntimeError("fake provider failure (test-simulated)"))
        for chunk in self._chunks or _split(self._reply_for(call.index)):
            yield chunk
            await asyncio.sleep(0)

    # --- internals ---

    async def _wait(self) -> None:
        if self._latency_ms:
            await asyncio.sleep(self._latency_ms / 1000.0)

    def _raise_if_scripted(self) -> None:
        if not self._scripted_errors:
            return
        error, remaining = self._scripted_errors[0]
        remaining -= 1
        if remaining <= 0:
            self._scripted_errors.pop(0)
        else:
            self._scripted_errors[0] = (error, remaining)
        raise error

    def _reply_for(self, index: int) -> str:
        if not self._replies:
            return DEFAULT_REPLY
        return self._replies[min(index, len(self._replies) - 1)]


def _as_llm_error(exc: Exception) -> LLMError:
    """Wrap a non-LLM exception so the Fake behaves like a real provider."""
    from app.services.llm.base import ProviderDown

    return ProviderDown(str(exc))


def _estimate_tokens(text: str) -> int:
    """A crude, deterministic token estimate (4 characters ≈ 1 token).

    Good enough for a Fake's usage accounting; the real number is whatever the
    provider reports.
    """
    return max(1, len(text) // 4)


def _split(text: str, words_per_chunk: int = 4) -> list[str]:
    """Split into word-boundary chunks that concatenate back to ``text``."""
    pieces = text.split(" ")
    out: list[str] = []
    for index in range(0, len(pieces), words_per_chunk):
        group = pieces[index : index + words_per_chunk]
        chunk = " ".join(group)
        if index + words_per_chunk < len(pieces):
            chunk += " "
        out.append(chunk)
    return out


class Clock:
    """A manual monotonic clock for tests that need to advance time.

    Used by the retry/breaker tests so a 60-second circuit cooldown is one
    assignment rather than one minute of wall clock.
    """

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def tick(self, seconds: float = 0.001) -> None:
        """Advance by a hair, so "time passed" is true without sleeping."""
        self.advance(seconds)


def real_clock() -> float:
    """The production clock, for callers that do not inject one."""
    return time.monotonic()
