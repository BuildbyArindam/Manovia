"""In-process rate limiting: sliding-window counters with two scopes.

Two limiters are wired in :func:`app.main.create_app`: a strict one for the
auth endpoints (credential stuffing is the threat) and a moderate global one
for everything else. Both are configurable from the environment and can be
disabled entirely for load tests.

State is per process. One uvicorn worker = one honest counter; behind several
workers or pods each gets its own budget (documented in PROGRESS.md — a shared
store such as Redis is the production answer, behind the same tiny interface).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of recording one request against a bucket."""

    allowed: bool
    retry_after: int
    """Seconds until the caller may retry (0 when the request was allowed)."""


class RateLimiter(Protocol):
    """One fixed-allowance window over arbitrary string keys."""

    def hit(self, key: str) -> RateLimitResult:
        """Record one request for ``key`` and say whether it is allowed."""

    def reset(self) -> None:
        """Forget all state (tests, admin tooling)."""


class InMemoryRateLimiter:
    """Sliding-window counter: at most ``limit`` hits per ``window_seconds``.

    Hits over the limit are not recorded, so a caller that keeps hammering does
    not extend its own penalty window; ``retry_after`` is derived from the
    oldest hit still inside the window.
    """

    def __init__(
        self,
        limit: int,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> RateLimitResult:
        now = self._clock()
        window_start = now - self._window
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= window_start:
            hits.popleft()
        if len(hits) >= self._limit:
            retry_after = max(1, int(hits[0] + self._window - now + 0.999))
            return RateLimitResult(allowed=False, retry_after=retry_after)
        hits.append(now)
        return RateLimitResult(allowed=True, retry_after=0)

    def reset(self) -> None:
        self._hits.clear()
