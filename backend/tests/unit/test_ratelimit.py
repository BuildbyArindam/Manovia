"""Sliding-window rate limiter (Day 4)."""

from __future__ import annotations

import pytest

from app.core.ratelimit import InMemoryRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def test_requests_under_the_limit_are_allowed(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(3, window_seconds=60, clock=clock)
    for _ in range(3):
        result = limiter.hit("ip:1.2.3.4")
        assert result.allowed
        assert result.retry_after == 0


def test_requests_over_the_limit_are_rejected_with_retry_after(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(2, window_seconds=60, clock=clock)
    assert limiter.hit("k").allowed
    assert limiter.hit("k").allowed
    rejected = limiter.hit("k")
    assert not rejected.allowed
    assert 1 <= rejected.retry_after <= 60


def test_keys_are_isolated(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(1, window_seconds=60, clock=clock)
    assert limiter.hit("a").allowed
    assert limiter.hit("b").allowed
    assert not limiter.hit("a").allowed


def test_the_window_slides(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(2, window_seconds=60, clock=clock)
    limiter.hit("k")
    clock.advance(30)
    limiter.hit("k")
    assert not limiter.hit("k").allowed
    clock.advance(31)  # first hit has left the window; one slot frees up
    assert limiter.hit("k").allowed


def test_hammering_does_not_extend_the_penalty_window(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(1, window_seconds=60, clock=clock)
    limiter.hit("k")
    for _ in range(10):
        assert not limiter.hit("k").allowed
    clock.advance(61)
    assert limiter.hit("k").allowed


def test_retry_after_shrinks_as_the_window_passes(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(1, window_seconds=60, clock=clock)
    limiter.hit("k")
    first = limiter.hit("k").retry_after
    clock.advance(30)
    second = limiter.hit("k").retry_after
    assert second < first


def test_reset_forgets_everything(clock: FakeClock) -> None:
    limiter = InMemoryRateLimiter(1, window_seconds=60, clock=clock)
    limiter.hit("k")
    assert not limiter.hit("k").allowed
    limiter.reset()
    assert limiter.hit("k").allowed


def test_configuration_is_validated() -> None:
    with pytest.raises(ValueError, match="limit"):
        InMemoryRateLimiter(0)
    with pytest.raises(ValueError, match="window"):
        InMemoryRateLimiter(1, window_seconds=0)
