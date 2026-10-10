"""Retry with jittered backoff, a hard timeout, and a circuit breaker.

Three separate mechanisms, each answering a different question:

* **Retry** — "that call might have been a blip". Exponential backoff with
  *full* jitter (``random.uniform(0, min(cap, base * 2 ** attempt))``), which is
  the variant that actually flattens the thundering herd: with no jitter every
  client that was rate-limited at the same second retries at the same second.
* **Hard timeout** — "no answer is better than a late answer". The budget is
  spent across *all* attempts for a provider, not per attempt, so a provider
  that is slow-but-alive cannot burn three times the latency allowance before
  the caller falls through.
* **Circuit breaker** — "stop calling something that is broken". After
  ``failure_threshold`` consecutive failures the breaker opens and the provider
  is skipped for ``cooldown_seconds``; after the cooldown one probe is allowed
  through (half-open) and a success closes it again.

All three are wrapped around any :class:`LLMProvider` by
:class:`ResilientProvider`, which still *is* a provider — so the chain sees one
uniform interface and never knows which of its links are armoured.

Every knob is injectable (``clock``, ``sleep``, ``random``) because the whole
point of this module is timing behaviour, and a test that sleeps for real is a
test nobody runs.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import structlog

from app.services.llm.base import (
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMResult,
    ProviderDown,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
)

#: Default retry budget: one try plus two retries.
DEFAULT_MAX_ATTEMPTS: int = 3
DEFAULT_BASE_SECONDS: float = 0.4
DEFAULT_MAX_SECONDS: float = 8.0
DEFAULT_TIMEOUT_SECONDS: float = 15.0
DEFAULT_FAILURE_THRESHOLD: int = 3
DEFAULT_COOLDOWN_SECONDS: float = 60.0

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class RetryPolicy:
    """Exponential backoff with full jitter.

    ``delay_for(0)`` is the wait *after the first failure*. Full jitter means
    the delay is drawn uniformly from ``[0, capped]`` rather than being the
    capped value itself; the expected wait halves, and — more importantly —
    clients that failed together stop retrying together.
    """

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_seconds: float = DEFAULT_BASE_SECONDS,
        max_seconds: float = DEFAULT_MAX_SECONDS,
        random_source: Callable[[], float] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_seconds <= 0:
            raise ValueError("base_seconds must be positive")
        if max_seconds < base_seconds:
            raise ValueError("max_seconds must be >= base_seconds")
        self._max_attempts = max_attempts
        self._base_seconds = base_seconds
        self._max_seconds = max_seconds
        self._random = random_source or random.random

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    @property
    def base_seconds(self) -> float:
        return self._base_seconds

    @property
    def max_seconds(self) -> float:
        return self._max_seconds

    def ceiling_for(self, attempt: int) -> float:
        """The un-jittered (worst case) wait after ``attempt`` (0-based)."""
        return min(self._max_seconds, float(self._base_seconds * (2**attempt)))

    def delay_for(self, attempt: int) -> float:
        """A jittered delay in ``[0, ceiling_for(attempt)]``."""
        ceiling = self.ceiling_for(attempt)
        return max(0.0, min(ceiling, float(self._random()) * ceiling))

    def describe(self) -> dict[str, object]:
        return {
            "max_attempts": self._max_attempts,
            "base_seconds": self._base_seconds,
            "max_seconds": self._max_seconds,
        }


class CircuitBreaker:
    """Consecutive-failure breaker with a cooldown and a half-open probe."""

    def __init__(
        self,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self._rejected = 0

    @property
    def failure_threshold(self) -> int:
        return self._failure_threshold

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def state(self) -> str:
        if self._failures < self._failure_threshold:
            return CLOSED
        opened = self._opened_at if self._opened_at is not None else self._clock()
        elapsed = self._clock() - opened
        return HALF_OPEN if elapsed >= self._cooldown_seconds else OPEN

    def allow(self) -> bool:
        """Whether a call may be attempted right now."""
        allowed = self.state != OPEN
        if not allowed:
            self._rejected += 1
        return allowed

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._opened_at is None:
            self._opened_at = self._clock()

    def describe(self) -> dict[str, object]:
        return {
            "state": self.state,
            "failures": self._failures,
            "threshold": self._failure_threshold,
            "cooldown_seconds": self._cooldown_seconds,
            "rejected": self._rejected,
        }


class ResilientProvider(LLMProvider):
    """Retry + timeout + breaker around one provider.

    Unexpected exceptions from the wrapped provider become
    :class:`ProviderDown`: an ``AttributeError`` inside an SDK is an outage as
    far as the caller is concerned, and letting it escape would turn a provider
    bug into a 500 in front of somebody who just said something difficult.

    The result carries how many attempts it took so the chain can report
    "answered on the second try" instead of pretending nothing happened.
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        retry: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._inner = inner
        self._retry = retry or RetryPolicy()
        self._breaker = breaker or CircuitBreaker()
        self._timeout_seconds = timeout_seconds
        self._clock = clock or time.monotonic
        # Any, not Awaitable: tests inject a plain function that records the
        # wait, and ``await`` on an arbitrary return value is resolved at
        # runtime by the iscoroutine check below.
        self._sleep: Callable[[float], Any] = sleep or asyncio.sleep
        self.attempts_total = 0
        # An instance attribute rather than a property: the base class declares
        # ``name`` as a plain class attribute, and overriding a writeable
        # attribute with a read-only property breaks that contract.
        self.name = inner.name

    @property
    def inner(self) -> LLMProvider:
        return self._inner

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._retry

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def model_id(self) -> str | None:
        return self._inner.model_id

    @property
    def is_configured(self) -> bool:
        return self._inner.is_configured

    async def aclose(self) -> None:
        await self._inner.aclose()

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "model": self.model_id,
            "configured": self.is_configured,
            "attempts_total": self.attempts_total,
            "retry": self._retry.describe(),
            "breaker": self._breaker.describe(),
        }

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> LLMResult:
        started = self._clock()
        deadline = started + self._timeout_seconds
        last: LLMError | None = None

        for attempt in range(self._retry.max_attempts):
            if not self._inner.is_configured:
                # Never spend a deadline on a provider that has no key.
                raise ProviderNotConfigured(f"{self.name} is not configured")
            if not self._breaker.allow():
                raise ProviderDown(f"{self.name} circuit is open")

            remaining = deadline - self._clock()
            if remaining <= 0:
                break

            self.attempts_total += 1
            try:
                result = await asyncio.wait_for(
                    self._inner.complete(
                        messages,
                        system=system,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                self._breaker.record_failure()
                last = ProviderTimeout(f"{self.name} exceeded {self._timeout_seconds:.2f}s")
                _log_provider_failure(self.name, attempt, last)
            except LLMError as exc:
                self._breaker.record_failure()
                if not exc.retryable:
                    _log_provider_failure(self.name, attempt, exc)
                    raise
                last = exc
                _log_provider_failure(self.name, attempt, exc)
            except Exception as exc:  # an SDK bug is an outage, not a 500
                self._breaker.record_failure()
                last = ProviderDown(f"{self.name} raised {type(exc).__name__}")
                _log_provider_failure(self.name, attempt, last)
            else:
                self._breaker.record_success()
                return _annotate(result, attempts=attempt + 1, elapsed_ms=self._elapsed_ms(started))

            wait = self._wait_after(attempt, last)
            remaining = deadline - self._clock()
            if wait is None or wait > remaining:
                break
            if wait > 0:
                maybe = self._sleep(wait)
                if asyncio.iscoroutine(maybe) or asyncio.isfuture(maybe):
                    await maybe

        if last is None:
            # We never got an attempt in: the deadline was already spent.
            raise ProviderTimeout(f"{self.name} had no time left before the first attempt")
        raise last

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream with the same armour, retrying only before the first token.

        Once bytes are on the wire, a retry would mean repeating text the user
        may already have read, so the failure is allowed to surface instead.
        """
        started = self._clock()
        deadline = started + self._timeout_seconds
        last: LLMError | None = None

        for attempt in range(self._retry.max_attempts):
            if not self._inner.is_configured:
                raise ProviderNotConfigured(f"{self.name} is not configured")
            if not self._breaker.allow():
                raise ProviderDown(f"{self.name} circuit is open")

            remaining = deadline - self._clock()
            if remaining <= 0:
                break

            self.attempts_total += 1
            yielded = False
            try:
                chunks = self._inner.stream(
                    messages,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                async for chunk in _with_deadline(chunks, remaining):
                    yielded = True
                    yield chunk
            except TimeoutError:
                self._breaker.record_failure()
                last = ProviderTimeout(f"{self.name} exceeded {self._timeout_seconds:.2f}s")
                _log_provider_failure(self.name, attempt, last)
            except LLMError as exc:
                self._breaker.record_failure()
                if not exc.retryable or yielded:
                    raise
                last = exc
                _log_provider_failure(self.name, attempt, exc)
            except Exception as exc:
                self._breaker.record_failure()
                last = ProviderDown(f"{self.name} raised {type(exc).__name__}")
                _log_provider_failure(self.name, attempt, last)
            else:
                self._breaker.record_success()
                return

            wait = self._wait_after(attempt, last)
            remaining = deadline - self._clock()
            if wait is None or wait > remaining:
                break
            if wait > 0:
                maybe = self._sleep(wait)
                if asyncio.iscoroutine(maybe) or asyncio.isfuture(maybe):
                    await maybe

        if last is None:
            raise ProviderTimeout(f"{self.name} had no time left before the first attempt")
        raise last

    # --- internals ---

    def _elapsed_ms(self, started: float) -> float:
        return round(max(0.0, (self._clock() - started) * 1000.0), 3)

    def _wait_after(self, attempt: int, error: LLMError | None) -> float | None:
        """How long to wait before the next attempt, or ``None`` to give up.

        A rate limit that told us how long to wait is honoured (that is the
        whole point of ``Retry-After``); anything else gets jittered backoff.
        """
        if isinstance(error, ProviderRateLimited) and error.retry_after is not None:
            return max(0.0, float(error.retry_after))
        if attempt + 1 >= self._retry.max_attempts:
            return None
        return self._retry.delay_for(attempt)


async def _with_deadline(chunks: AsyncIterator[str], timeout: float) -> AsyncIterator[str]:
    """Yield from ``chunks``, raising ``TimeoutError`` if a gap exceeds ``timeout``.

    The deadline is a *per-chunk* gap, not a total: a long, slow stream that is
    still producing tokens is doing its job.
    """
    iterator = chunks.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(iterator.__anext__(), timeout=timeout)
        except StopAsyncIteration:
            return
        yield chunk


def _annotate(result: LLMResult, *, attempts: int, elapsed_ms: float) -> LLMResult:
    raw = dict(result.raw or {})
    raw["attempts"] = attempts
    return result.model_copy(update={"latency_ms": elapsed_ms or result.latency_ms, "raw": raw})


def _log_provider_failure(provider: str, attempt: int, error: LLMError) -> None:
    """Warn without ever echoing message text (AGENTS.md safety rule 5)."""
    structlog.get_logger().warning(
        "llm_provider_failed",
        provider=provider,
        attempt=attempt + 1,
        error_type=type(error).__name__,
        retryable=error.retryable,
        retry_after=error.details.get("retry_after"),
    )
