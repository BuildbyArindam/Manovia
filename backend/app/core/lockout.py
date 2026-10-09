"""Account lockout with exponential backoff after repeated failed sign-ins.

The counter is keyed by the *attempted* account (normalised email), not by
client IP: the attacker may come from anywhere, the account cannot move. After
``max_failures`` consecutive failures the account is locked, and each further
failure doubles the lock (capped) — backoff that punishes spraying without
giving a permanent denial-of-service against a victim's account, because a
successful sign-in clears the counter and the lock expires on its own.

Like the rate limiter, state is per process (see PROGRESS.md for the
multi-worker caveat). Attempts *while locked* are rejected before any password
work and do not extend the lock.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.base import utcnow


@dataclass(frozen=True)
class LockoutStatus:
    """Snapshot of one account's failure state."""

    failures: int
    locked_until: datetime | None

    @property
    def locked(self) -> bool:
        return self.locked_until is not None


class LoginLockout:
    """Failed-sign-in tracker with lockout and exponential backoff."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        lock_seconds: int = 900,
        max_lock_seconds: int = 3600,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if max_failures < 1:
            raise ValueError("max_failures must be at least 1")
        if lock_seconds < 1 or max_lock_seconds < lock_seconds:
            raise ValueError("lock windows must be positive and ordered")
        self._max_failures = max_failures
        self._lock_seconds = lock_seconds
        self._max_lock_seconds = max_lock_seconds
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, datetime] = {}

    def remaining_lock(self, key: str, *, now: datetime | None = None) -> timedelta | None:
        """Time left on the lock for ``key``; ``None`` when not locked."""
        current = now or self._clock()
        until = self._locked_until.get(key)
        if until is None:
            return None
        remaining = until - current
        if remaining <= timedelta(0):
            return None
        return remaining

    def record_failure(self, key: str, *, now: datetime | None = None) -> LockoutStatus:
        """Count one failed sign-in and apply/extend the lock if needed."""
        current = now or self._clock()
        failures = self._failures.get(key, 0) + 1
        self._failures[key] = failures
        if failures >= self._max_failures:
            over = failures - self._max_failures
            lock_seconds = min(self._lock_seconds * (2**over), self._max_lock_seconds)
            self._locked_until[key] = current + timedelta(seconds=lock_seconds)
        return self.status(key, now=current)

    def record_success(self, key: str) -> None:
        """Clear every trace of failure for ``key`` after a good sign-in."""
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)

    def status(self, key: str, *, now: datetime | None = None) -> LockoutStatus:
        """Current failure count and lock for ``key``."""
        current = now or self._clock()
        until = self._locked_until.get(key)
        if until is not None and until <= current:
            until = None
        return LockoutStatus(failures=self._failures.get(key, 0), locked_until=until)

    def reset(self) -> None:
        """Forget all state (tests, admin tooling)."""
        self._failures.clear()
        self._locked_until.clear()
