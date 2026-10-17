"""Agent sessions: ephemeral, in-memory, owned.

Mirrors the chat session service's contract: a session
is a dict entry, not a row — agent transcripts hold
bounded, validated tool arguments and the person's own
draft text, and nothing here outlives the process. A
session belongs to the user who opened it; another
user's id gets the same "not found" as a nonexistent
one, so session ids leak nothing.
"""

from __future__ import annotations

import time
import uuid
from typing import Final

from app.services.agent.types import TurnState

#: Minutes of idle time before a session is dropped.
SESSION_IDLE_MINUTES: Final = 30

#: Ceiling on sessions held per process. The oldest idle
#: entry goes first; a new session never fails.
MAX_SESSIONS: Final = 512


class AgentSessionService:
    """Open, find and prune agent sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionRecord] = {}

    def open(self, user_id: str) -> TurnState:
        """Start a session owned by ``user_id``.

        The id is a UUID in its canonical hyphenated
        form — the same string the API's path parameter
        round-trips into.
        """
        self._prune()
        if len(self._sessions) >= MAX_SESSIONS:
            self._evict_oldest()
        state = TurnState(session_id=str(uuid.uuid4()), user_id=user_id)
        self._sessions[state.session_id] = _SessionRecord(
            user_id=user_id, state=state, last_seen=time.monotonic()
        )
        return state

    def get(self, session_id: str, user_id: str) -> TurnState | None:
        """The session's state, if it exists *and* is owned."""
        record = self._sessions.get(session_id)
        if record is None or record.user_id != user_id:
            return None
        record.last_seen = time.monotonic()
        return record.state

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [
            key
            for key, record in self._sessions.items()
            if now - record.last_seen > SESSION_IDLE_MINUTES * 60
        ]
        for key in stale:
            self._sessions.pop(key, None)

    def _evict_oldest(self) -> None:
        oldest = min(self._sessions.items(), key=lambda item: item[1].last_seen)[0]
        self._sessions.pop(oldest, None)


class _SessionRecord:
    __slots__ = ("last_seen", "state", "user_id")

    def __init__(self, user_id: str, state: TurnState, last_seen: float) -> None:
        self.user_id = user_id
        self.state = state
        self.last_seen = last_seen
