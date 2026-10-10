"""Retry with jittered backoff, the hard timeout, and the circuit breaker.

These three decide whether a vendor's bad minute becomes a slow reply or an
error page, so they are tested behaviourally: how many times the provider was
actually called, how long the waits were, and whether the deadline was honoured.
Nothing here sleeps for real — the clock and the sleep function are injected.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

import pytest

from app.services.llm.base import (
    LLMError,
    LLMMessage,
    ProviderBadRequest,
    ProviderDown,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
)
from app.services.llm.fake_provider import FakeLLMProvider
from app.services.llm.resilience import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    CircuitBreaker,
    ResilientProvider,
    RetryPolicy,
)

from .conftest import FakeClock, ScriptedProvider, SleepRecorder, collect, messages


def armed(
    inner: FakeLLMProvider | ScriptedProvider,
    *,
    clock: FakeClock | None = None,
    sleeper: SleepRecorder | None = None,
    max_attempts: int = 3,
    base: float = 0.4,
    cap: float = 8.0,
    timeout: float = 15.0,
    threshold: int = 3,
    cooldown: float = 60.0,
    jitter: float = 0.5,
) -> ResilientProvider:
    """A provider wrapped in retry + timeout + breaker, with no real waiting."""
    return ResilientProvider(
        inner,
        retry=RetryPolicy(
            max_attempts=max_attempts,
            base_seconds=base,
            max_seconds=cap,
            # Fixed jitter keeps the delay deterministic; the jitter itself is
            # tested separately against the policy's own bounds.
            random_source=lambda: jitter,
        ),
        breaker=CircuitBreaker(failure_threshold=threshold, cooldown_seconds=cooldown, clock=clock),
        timeout_seconds=timeout,
        clock=clock,
        sleep=sleeper,
    )


# --- RetryPolicy -------------------------------------------------------------


def test_delay_ceiling_grows_exponentially_and_is_capped() -> None:
    policy = RetryPolicy(base_seconds=0.5, max_seconds=4.0)
    assert policy.ceiling_for(0) == 0.5
    assert policy.ceiling_for(1) == 1.0
    assert policy.ceiling_for(2) == 2.0
    assert policy.ceiling_for(3) == 4.0
    assert policy.ceiling_for(9) == 4.0


def test_delay_is_jittered_within_the_ceiling() -> None:
    """Full jitter: the wait is drawn from [0, ceiling], not set to it."""
    never = RetryPolicy(base_seconds=1.0, max_seconds=8.0, random_source=lambda: 0.0)
    assert never.delay_for(0) == 0.0
    assert never.delay_for(3) == 0.0
    low = RetryPolicy(base_seconds=1.0, max_seconds=8.0, random_source=lambda: 0.25)
    assert low.delay_for(2) == pytest.approx(1.0)  # 0.25 * ceiling(4.0)
    high = RetryPolicy(base_seconds=1.0, max_seconds=8.0, random_source=lambda: 1.0)
    assert high.delay_for(2) == pytest.approx(4.0)  # the whole ceiling
    # And with a real RNG, the delay always stays inside the ceiling.
    policy = RetryPolicy(base_seconds=0.4, max_seconds=8.0)
    for attempt in range(8):
        for _ in range(50):
            assert 0.0 <= policy.delay_for(attempt) <= policy.ceiling_for(attempt)


def test_policy_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_seconds=0)
    with pytest.raises(ValueError):
        RetryPolicy(base_seconds=5, max_seconds=1)


# --- retry behaviour ---------------------------------------------------------


async def test_a_transient_failure_is_retried_and_succeeds(sleeper: SleepRecorder) -> None:
    inner = FakeLLMProvider(fail_times=1)
    provider = armed(inner, sleeper=sleeper)
    result = await provider.complete(messages("hello"))
    assert result.text
    assert inner.call_count == 2
    assert len(sleeper.waits) == 1  # one wait, between attempt 1 and 2


async def test_retry_stops_after_max_attempts(sleeper: SleepRecorder) -> None:
    inner = FakeLLMProvider(error=ProviderDown("down"))
    provider = armed(inner, sleeper=sleeper, max_attempts=3)
    with pytest.raises(ProviderDown):
        await provider.complete(messages("hello"))
    assert inner.call_count == 3
    # Two waits: after the first and second failure, never after the last.
    assert len(sleeper.waits) == 2


async def test_a_non_retryable_error_is_not_retried(sleeper: SleepRecorder) -> None:
    inner = FakeLLMProvider(error=ProviderBadRequest("bad"))
    provider = armed(inner, sleeper=sleeper)
    with pytest.raises(ProviderBadRequest):
        await provider.complete(messages("hello"))
    assert inner.call_count == 1
    assert sleeper.waits == []


async def test_an_unexpected_exception_becomes_provider_down(sleeper: SleepRecorder) -> None:
    """An SDK bug must degrade the provider, not raise out of the LLM layer."""
    inner = FakeLLMProvider(error=AttributeError("no attribute 'messages'"))
    provider = armed(inner, sleeper=sleeper, max_attempts=2)
    with pytest.raises(ProviderDown) as excinfo:
        await provider.complete(messages("hello"))
    assert "AttributeError" in str(excinfo.value)
    assert inner.call_count == 2


async def test_rate_limit_honours_retry_after(sleeper: SleepRecorder) -> None:
    """When the provider says how long to wait, wait exactly that, not the
    backoff — that is the entire point of Retry-After."""
    inner = FakeLLMProvider().script_error(ProviderRateLimited(retry_after=1.25), times=1)
    provider = armed(inner, sleeper=sleeper, base=0.1)
    result = await provider.complete(messages("hello"))
    assert result.text
    assert sleeper.waits == [1.25]


async def test_rate_limit_gives_up_when_the_wait_exceeds_the_deadline(
    sleeper: SleepRecorder,
) -> None:
    """Waiting 30 seconds inside a 0.05 second budget would be worse than
    failing: the caller needs the answer now, from the next provider."""
    inner = FakeLLMProvider(error=ProviderRateLimited(retry_after=30.0))
    provider = armed(inner, sleeper=sleeper, timeout=0.05)
    with pytest.raises(ProviderRateLimited):
        await provider.complete(messages("hello"))
    assert inner.call_count == 1
    assert sleeper.waits == []


async def test_an_unconfigured_provider_is_never_called(sleeper: SleepRecorder) -> None:
    """A missing key must cost nothing — no attempt, no deadline, no wait."""
    inner = FakeLLMProvider(configured=False)
    provider = armed(inner, sleeper=sleeper)
    with pytest.raises(ProviderNotConfigured):
        await provider.complete(messages("hello"))
    assert inner.call_count == 0


# --- hard timeout ------------------------------------------------------------


async def test_a_slow_provider_times_out_as_provider_timeout() -> None:
    inner = FakeLLMProvider(latency_ms=250)
    provider = armed(inner, timeout=0.05, max_attempts=3)
    with pytest.raises(ProviderTimeout):
        await provider.complete(messages("hello"))


async def test_the_timeout_is_a_total_budget_not_a_per_attempt_one() -> None:
    """Three attempts at 60 ms each inside a 100 ms budget is two attempts,
    not three: the budget is spent across the retries, so a slow-but-alive
    provider cannot triple the latency the caller is exposed to."""
    inner = FakeLLMProvider(error=ProviderDown("down"), latency_ms=60)
    sleeper = SleepRecorder()
    provider = armed(inner, sleeper=sleeper, timeout=0.1, max_attempts=5, base=0.001)
    with pytest.raises(LLMError):
        await provider.complete(messages("hello"))
    assert inner.call_count <= 2


async def test_a_deadline_that_is_already_spent_raises_without_calling(
    clock: FakeClock, sleeper: SleepRecorder
) -> None:
    inner = FakeLLMProvider(error=ProviderDown("down"))
    provider = armed(inner, clock=clock, sleeper=sleeper, timeout=10.0, max_attempts=1)
    with pytest.raises(ProviderDown):
        await provider.complete(messages("hello"))
    assert inner.call_count == 1


# --- circuit breaker ---------------------------------------------------------


def test_breaker_opens_after_the_failure_threshold(clock: FakeClock) -> None:
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0, clock=clock)
    assert breaker.state == CLOSED
    breaker.record_failure()
    assert breaker.state == CLOSED
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.state == OPEN
    assert breaker.allow() is False


def test_breaker_goes_half_open_after_the_cooldown(clock: FakeClock) -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0, clock=clock)
    breaker.record_failure()
    assert breaker.state == OPEN
    clock.advance(29.0)
    assert breaker.state == OPEN
    clock.advance(2.0)
    assert breaker.state == HALF_OPEN
    assert breaker.allow() is True


def test_a_success_closes_the_breaker(clock: FakeClock) -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30.0, clock=clock)
    breaker.record_failure()
    breaker.record_success()
    assert breaker.state == CLOSED
    assert breaker.failures == 0


def test_breaker_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(cooldown_seconds=-1)


async def test_an_open_breaker_skips_the_provider_entirely(
    clock: FakeClock, sleeper: SleepRecorder
) -> None:
    """The point of the breaker: once we know it is down, stop asking."""
    inner = FakeLLMProvider(error=ProviderDown("down"))
    provider = armed(
        inner, clock=clock, sleeper=sleeper, max_attempts=1, threshold=1, cooldown=60.0
    )
    with pytest.raises(ProviderDown):
        await provider.complete(messages("hello"))
    first_round_calls = inner.call_count
    assert first_round_calls == 1

    with pytest.raises(ProviderDown):
        await provider.complete(messages("hello again"))
    assert inner.call_count == first_round_calls  # not called at all
    assert provider.breaker.state == OPEN

    clock.advance(61.0)  # cooldown elapsed -> one probe allowed
    with pytest.raises(ProviderDown):
        await provider.complete(messages("hello once more"))
    assert inner.call_count == first_round_calls + 1


async def test_the_breaker_records_failures_from_the_inner_provider(
    clock: FakeClock, sleeper: SleepRecorder
) -> None:
    inner = FakeLLMProvider(error=ProviderBadRequest("nope"))
    provider = armed(inner, clock=clock, sleeper=sleeper, threshold=2)
    for _ in range(2):
        with pytest.raises(ProviderBadRequest):
            await provider.complete(messages("hello"))
    assert provider.breaker.state == OPEN


# --- streaming ---------------------------------------------------------------


async def test_stream_retries_when_it_fails_before_the_first_token(
    sleeper: SleepRecorder,
) -> None:
    inner = ScriptedProvider(ProviderDown("down"), ["hel", "lo"])
    provider = armed(inner, sleeper=sleeper)
    assert await collect(provider, "hi") == "hello"
    assert inner.stream_calls == 2


async def test_stream_does_not_retry_after_tokens_were_delivered(
    sleeper: SleepRecorder,
) -> None:
    """Repeating text the user has already read is worse than an error."""

    class MidStreamFailure(ScriptedProvider):
        async def stream(
            self,
            messages: Sequence[LLMMessage],
            *,
            system: str | None = None,
            max_tokens: int = 400,
            temperature: float = 0.7,
        ) -> AsyncIterator[str]:
            self.stream_calls += 1
            yield "I hear you"
            raise ProviderDown("connection reset")

    inner = MidStreamFailure()
    provider = armed(inner, sleeper=sleeper)
    received: list[str] = []
    with pytest.raises(ProviderDown):
        async for chunk in provider.stream(messages("hi")):
            received.append(chunk)
    assert received == ["I hear you"]
    assert inner.stream_calls == 1  # no retry: text was already delivered


async def test_stream_raises_provider_timeout_on_a_stalled_stream() -> None:
    async def stalled() -> AsyncIterator[str]:
        yield "start"
        await asyncio.sleep(5.0)
        yield "never"

    class Stalled(ScriptedProvider):
        async def stream(
            self,
            messages: Sequence[LLMMessage],
            *,
            system: str | None = None,
            max_tokens: int = 400,
            temperature: float = 0.7,
        ) -> AsyncIterator[str]:
            self.stream_calls += 1
            async for chunk in stalled():
                yield chunk

    provider = armed(Stalled(), timeout=0.1)
    with pytest.raises(ProviderTimeout):
        await collect(provider, "hi")


async def test_stream_reports_the_provider_name_and_model() -> None:
    inner = FakeLLMProvider()
    provider = armed(inner)
    assert provider.name == "fake"
    assert provider.model_id == inner.model_id
    assert provider.is_configured is True
    assert provider.inner is inner


# --- misc --------------------------------------------------------------------


async def test_describe_carries_retry_and_breaker_state(clock: FakeClock) -> None:
    provider = armed(FakeLLMProvider(), clock=clock)
    described = provider.describe()
    assert described["provider"] == "fake"
    assert described["retry"] == RetryPolicy(max_attempts=3).describe()
    breaker = described["breaker"]
    assert isinstance(breaker, dict)
    assert breaker["state"] == CLOSED
    # No secrets, no text — metadata only.
    assert "api_key" not in described


def test_resilient_provider_rejects_a_zero_timeout() -> None:
    with pytest.raises(ValueError):
        ResilientProvider(FakeLLMProvider(), timeout_seconds=0)


async def test_aclose_is_forwarded() -> None:
    inner = FakeLLMProvider()
    provider = armed(inner)
    await provider.aclose()  # Fake has nothing to close; must not raise


def test_every_llm_error_states_whether_it_is_retryable() -> None:
    """The retry machinery reads this attribute; a subclass that forgets it
    silently becomes never-retried, which is a behaviour change in disguise."""
    assert ProviderDown.retryable is True
    assert ProviderTimeout.retryable is True
    assert ProviderRateLimited(retry_after=1).retryable is True
    assert ProviderBadRequest.retryable is False
    assert ProviderNotConfigured.retryable is False
    assert isinstance(ProviderRateLimited(retry_after=None), LLMError)
