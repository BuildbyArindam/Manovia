"""Account lockout with exponential backoff (Day 4)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.core.lockout import LoginLockout
from app.models.base import utcnow

KEY = "person@example.test"


def test_failures_accumulate_below_the_threshold() -> None:
    lockout = LoginLockout(max_failures=3, lock_seconds=60)
    assert lockout.record_failure(KEY).failures == 1
    assert lockout.record_failure(KEY).failures == 2
    status = lockout.record_failure(KEY)
    assert status.failures == 3
    assert status.locked
    assert lockout.remaining_lock(KEY) is not None


def test_lockout_starts_at_the_threshold() -> None:
    lockout = LoginLockout(max_failures=2, lock_seconds=60)
    assert not lockout.record_failure(KEY).locked
    assert lockout.record_failure(KEY).locked


def test_lock_duration_doubles_with_each_further_failure() -> None:
    lockout = LoginLockout(max_failures=2, lock_seconds=100, max_lock_seconds=1000)
    now = utcnow()
    lockout.record_failure(KEY, now=now)
    first = lockout.record_failure(KEY, now=now)  # 2 failures: 100 s
    assert first.locked_until == now + timedelta(seconds=100)
    second = lockout.record_failure(KEY, now=now)  # 3 failures: 200 s
    assert second.locked_until == now + timedelta(seconds=200)
    third = lockout.record_failure(KEY, now=now)  # 4 failures: 400 s
    assert third.locked_until == now + timedelta(seconds=400)
    capped = lockout.record_failure(KEY, now=now)  # 5 failures: 800 s
    assert capped.locked_until == now + timedelta(seconds=800)
    beyond = lockout.record_failure(KEY, now=now)  # 6 failures: capped at 1000 s
    assert beyond.locked_until == now + timedelta(seconds=1000)


def test_lock_expiry_is_reported_as_unlocked() -> None:
    lockout = LoginLockout(max_failures=1, lock_seconds=60)
    now = utcnow()
    lockout.record_failure(KEY, now=now)
    assert lockout.remaining_lock(KEY, now=now) == timedelta(seconds=60)
    assert lockout.remaining_lock(KEY, now=now + timedelta(seconds=59)) is not None
    assert lockout.remaining_lock(KEY, now=now + timedelta(seconds=61)) is None
    assert not lockout.status(KEY, now=now + timedelta(seconds=61)).locked


def test_successful_sign_in_clears_everything() -> None:
    lockout = LoginLockout(max_failures=1, lock_seconds=60)
    lockout.record_failure(KEY)
    assert lockout.status(KEY).locked
    lockout.record_success(KEY)
    assert lockout.status(KEY).failures == 0
    assert lockout.remaining_lock(KEY) is None


def test_counters_are_per_key() -> None:
    lockout = LoginLockout(max_failures=1, lock_seconds=60)
    lockout.record_failure("a@example.test")
    assert lockout.remaining_lock("a@example.test") is not None
    assert lockout.remaining_lock("b@example.test") is None


def test_reset_forgets_everything() -> None:
    lockout = LoginLockout(max_failures=1, lock_seconds=60)
    lockout.record_failure(KEY)
    lockout.reset()
    assert lockout.status(KEY).failures == 0


def test_configuration_is_validated() -> None:
    with pytest.raises(ValueError, match="max_failures"):
        LoginLockout(max_failures=0)
    with pytest.raises(ValueError, match="lock windows"):
        LoginLockout(lock_seconds=0)
    with pytest.raises(ValueError, match="lock windows"):
        LoginLockout(lock_seconds=100, max_lock_seconds=50)


def test_default_clock_is_utcnow() -> None:
    lockout = LoginLockout(max_failures=1, lock_seconds=60)
    before = datetime.now(tz=utcnow().tzinfo)
    status = lockout.record_failure(KEY)
    assert status.locked_until is not None
    assert status.locked_until > before
