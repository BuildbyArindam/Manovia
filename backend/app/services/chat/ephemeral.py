"""Ephemeral chat sessions: process memory only, gone after 30 idle minutes.

A person who has not opted in to saving their chats gets a conversation that
leaves no trace in the database — no ``chat_sessions`` row, no ``messages``
rows. The conversation lives in this dictionary so the model still has the
thread to work with, and it is dropped when the person stops talking.

Properties, each deliberate:

* **Sliding TTL.** The 30 minutes run from the last activity, not from creation:
  a conversation that is still going must not vanish mid-sentence.
* **Owner-scoped lookup.** ``get(session_id, user_id)`` answers ``None`` for a
  session that belongs to somebody else exactly as it does for one that does not
  exist, so a probe learns nothing.
* **Bounded.** A total cap, a per-user cap and a per-session turn cap, so one
  client cannot grow the process without limit. At a cap the *oldest idle*
  session goes first.
* **No background task.** Expiry is checked lazily on every access and swept on
  every ``create``; there is no timer to leak or to forget to cancel.
* **Per process.** Two workers have two stores, so a session is only reachable on
  the worker that created it. That is a documented limit (PROGRESS.md), the same
  as the in-process rate limiter; a shared store is the production answer.

Plaintext lives here (it must — the model needs it), which is why the store is
never serialised, logged or exposed other than through its owner's own session.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

#: Live sessions one user may hold at once; the oldest idle one is evicted first.
MAX_SESSIONS_PER_USER = 20


@dataclass
class EphemeralTurn:
    """One in-memory message."""

    id: uuid.UUID
    role: str
    text: str
    #: The stored four-tier risk (0-3) of the turn; 3 marks a crisis exchange.
    risk: int
    emotion: str | None
    created_at: datetime

    @property
    def crisis(self) -> bool:
        return self.risk >= 3


@dataclass
class EphemeralSession:
    """A conversation held only in memory."""

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    last_active: float
    expires_at: float
    turns: list[EphemeralTurn] = field(default_factory=list)


class EphemeralSessionStore:
    """Sessions keyed by id, expiring after ``ttl_seconds`` of inactivity."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 1800,
        max_sessions: int = 5000,
        max_turns: int = 60,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be at least 1")
        if max_sessions < 1:
            raise ValueError("max_sessions must be at least 1")
        if max_turns < 2:
            raise ValueError("max_turns must be at least 2")
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._max_turns = max_turns
        self._clock = clock
        self._sessions: dict[uuid.UUID, EphemeralSession] = {}

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def __len__(self) -> int:
        return len(self._sessions)

    def to_datetime(self, epoch_seconds: float) -> datetime:
        """Epoch seconds from this store's clock as an aware UTC datetime."""
        return datetime.fromtimestamp(epoch_seconds, tz=UTC)

    def create(self, user_id: uuid.UUID) -> EphemeralSession:
        """Open a session for ``user_id``, sweeping expired ones first."""
        self.purge()
        now = self._clock()
        mine = sorted(
            (s for s in self._sessions.values() if s.user_id == user_id),
            key=lambda s: s.last_active,
        )
        while len(mine) >= MAX_SESSIONS_PER_USER:
            self._sessions.pop(mine.pop(0).id, None)
        while len(self._sessions) >= self._max_sessions:
            oldest = min(self._sessions.values(), key=lambda s: s.last_active)
            self._sessions.pop(oldest.id, None)
        session = EphemeralSession(
            id=uuid.uuid4(),
            user_id=user_id,
            created_at=self.to_datetime(now),
            last_active=now,
            expires_at=now + self._ttl,
        )
        self._sessions[session.id] = session
        return session

    def get(
        self, session_id: uuid.UUID, user_id: uuid.UUID, *, touch: bool = False
    ) -> EphemeralSession | None:
        """The session if it is live **and** owned by ``user_id``; else ``None``.

        ``touch=True`` slides the expiry forward (a message was sent). Reading
        the history does not extend a session's life.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return None
        now = self._clock()
        if session.expires_at <= now:
            del self._sessions[session_id]
            return None
        if session.user_id != user_id:
            return None
        if touch:
            self._touch(session, now)
        return session

    def add_turn(
        self,
        session: EphemeralSession,
        *,
        role: str,
        text: str,
        risk: int,
        emotion: str | None,
    ) -> EphemeralTurn:
        """Append a turn (and slide the expiry); the oldest turns fall off the front."""
        turn = EphemeralTurn(
            id=uuid.uuid4(),
            role=role,
            text=text,
            risk=risk,
            emotion=emotion,
            created_at=self.to_datetime(self._clock()),
        )
        session.turns.append(turn)
        if len(session.turns) > self._max_turns:
            del session.turns[: len(session.turns) - self._max_turns]
        self._touch(session, self._clock())
        return turn

    def discard(self, session_id: uuid.UUID) -> None:
        """Forget a session now (used on explicit end and in tests)."""
        self._sessions.pop(session_id, None)

    def purge(self) -> int:
        """Drop every expired session; return how many went."""
        now = self._clock()
        expired = [sid for sid, s in self._sessions.items() if s.expires_at <= now]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)

    def _touch(self, session: EphemeralSession, now: float) -> None:
        session.last_active = now
        session.expires_at = now + self._ttl
