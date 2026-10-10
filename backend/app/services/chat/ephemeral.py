"""Ephemeral (in-memory) chat sessions — no DB rows, TTL 30 min.

When a user has *not* granted ``store_chat`` consent, their conversation lives
only in this process, for :data:`~app.core.config.Settings.chat_ephemeral_ttl_seconds`
seconds after the last activity. No row is written to ``chat_sessions`` or
``messages``, which is what the verification step checks.

Design notes:

* Thread-safe via a single :class:`threading.RLock` — FastAPI may run with
  several workers in production (the real fix is Redis, parked in PROGRESS.md),
  but for the single-process dev and test environments a lock is enough.
* Expiry is checked on every access, not by a background sweeper, so a dead
  session costs no timer thread.
* Messages are plain Python objects; encryption is unnecessary because they
  never touch disk.
* The store is process-wide and shared by every request (installed on
  ``app.state.ephemeral_store``).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from app.models.enums import MessageRole, RiskLevel


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class EphemeralMessage:
    """One turn in an ephemeral session."""

    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    risk_level: int = int(RiskLevel.NONE)
    emotion: str | None = None


@dataclass
class EphemeralSession:
    """A session that lives only in memory."""

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    last_active: datetime
    messages: list[EphemeralMessage] = field(default_factory=list)
    ended_at: datetime | None = None

    @property
    def is_expired(self) -> bool:
        # Expiry is decided by the store using its TTL and clock; this is a
        # convenience for callers that already hold the session.
        return False

    @property
    def message_count(self) -> int:
        return len(self.messages)


DEFAULT_TTL_SECONDS: Final = 1800  # 30 min


class EphemeralStore:
    """In-memory session store with TTL."""

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._clock = clock or time.monotonic
        self._sessions: dict[uuid.UUID, EphemeralSession] = {}
        self._lock = threading.RLock()
        self._created = 0
        self._expired = 0

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def _now_monotonic(self) -> float:
        return self._clock()

    def _is_expired_locked(self, sess: EphemeralSession, now_mono: float) -> bool:
        # Store the monotonic expiry alongside? Simpler: use wall-clock age
        # from last_active, but compare via monotonic delta from creation.
        # We keep last_active as wall time and also track monotonic last active
        # via a parallel dict.
        # For simplicity, we store expiry as wall-clock: if now - last_active > ttl
        # then expired. We use utcnow for that.
        age = (_utcnow() - sess.last_active).total_seconds()
        return age > self._ttl

    def create(self, *, user_id: uuid.UUID) -> EphemeralSession:
        sess = EphemeralSession(
            id=uuid.uuid4(),
            user_id=user_id,
            created_at=_utcnow(),
            last_active=_utcnow(),
        )
        with self._lock:
            self._sessions[sess.id] = sess
            self._created += 1
        return sess

    def get(self, session_id: uuid.UUID) -> EphemeralSession | None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return None
            if self._is_expired_locked(sess, self._now_monotonic()):
                del self._sessions[session_id]
                self._expired += 1
                return None
            return sess

    def get_for_user(self, session_id: uuid.UUID, user_id: uuid.UUID) -> EphemeralSession | None:
        sess = self.get(session_id)
        if sess is None:
            return None
        if sess.user_id != user_id:
            return None
        return sess

    def touch(self, session_id: uuid.UUID) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is not None:
                sess.last_active = _utcnow()

    def add_message(
        self,
        session_id: uuid.UUID,
        *,
        role: MessageRole,
        content: str,
        risk_level: int | RiskLevel = RiskLevel.NONE,
        emotion: str | None = None,
    ) -> EphemeralMessage | None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return None
            if self._is_expired_locked(sess, self._now_monotonic()):
                del self._sessions[session_id]
                self._expired += 1
                return None
            msg = EphemeralMessage(
                id=uuid.uuid4(),
                role=role,
                content=content,
                created_at=_utcnow(),
                risk_level=int(risk_level),
                emotion=emotion,
            )
            sess.messages.append(msg)
            sess.last_active = _utcnow()
            return msg

    def list_messages(
        self, session_id: uuid.UUID, *, limit: int = 100, offset: int = 0
    ) -> list[EphemeralMessage] | None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return None
            if self._is_expired_locked(sess, self._now_monotonic()):
                del self._sessions[session_id]
                self._expired += 1
                return None
            # oldest first, same as DB repo
            ordered = sorted(sess.messages, key=lambda m: (m.created_at, m.id))
            return ordered[offset : offset + limit]

    def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> list[EphemeralSession]:
        with self._lock:
            now = self._now_monotonic()
            # purge expired first
            expired_ids = [
                sid for sid, s in self._sessions.items() if self._is_expired_locked(s, now)
            ]
            for sid in expired_ids:
                del self._sessions[sid]
                self._expired += 1
            sessions = [s for s in self._sessions.values() if s.user_id == user_id]
            sessions.sort(key=lambda s: s.created_at, reverse=True)
            return sessions[offset : offset + limit]

    def delete(self, session_id: uuid.UUID) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def purge_expired(self) -> int:
        with self._lock:
            now = self._now_monotonic()
            expired = [sid for sid, s in self._sessions.items() if self._is_expired_locked(s, now)]
            for sid in expired:
                del self._sessions[sid]
            self._expired += len(expired)
            return len(expired)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def stats(self) -> dict[str, object]:
        with self._lock:
            return {
                "sessions": len(self._sessions),
                "created": self._created,
                "expired": self._expired,
                "ttl_seconds": self._ttl,
            }


# Process-wide singleton accessor (used by deps)
_store: EphemeralStore | None = None
_store_lock = threading.Lock()


def get_ephemeral_store(ttl_seconds: int = DEFAULT_TTL_SECONDS) -> EphemeralStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = EphemeralStore(ttl_seconds=ttl_seconds)
        return _store


def build_ephemeral_store(ttl_seconds: int = DEFAULT_TTL_SECONDS) -> EphemeralStore:
    return EphemeralStore(ttl_seconds=ttl_seconds)


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "EphemeralMessage",
    "EphemeralSession",
    "EphemeralStore",
    "build_ephemeral_store",
    "get_ephemeral_store",
]
