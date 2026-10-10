"""A conversation, as the orchestrator sees it: read a window, store two turns.

The orchestrator does not know whether a session is saved. It talks to a
:class:`Conversation`, and there are two of them:

* :class:`PersistentConversation` — the person opted in to saving history
  (consent ``store_chat``). Turns go to the database through
  :class:`~app.db.repos.chats.ChatRepository`, which encrypts them.
* :class:`EphemeralConversation` — everything else. Turns live in
  :mod:`app.services.chat.ephemeral` and nothing is written to ``chat_sessions``
  or ``messages``.

**Safety events and ephemeral sessions.** A ``safety_events`` row is
metadata-only (a tier, a detector, a timestamp — no text). For a saved session it
carries the user and session ids. For an ephemeral session it is written
*anonymously*, with both ids NULL: the operator can still count how often the
crisis path fires, but nothing links it to a person who chose not to keep a
history. This is the same shape the public ``/crisis/assess`` endpoint has
written since Day 9. See ADR 0011.

Every database write here opens its own short session and commits at once. The
orchestrator never holds a transaction open across an LLM call, and a stream
that outlives its request cannot touch a request-scoped session that has been
closed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import ChatRepository, SafetyEventRepository
from app.models.enums import MessageRole, RiskLevel, SafetyEventSource
from app.services.chat.ephemeral import EphemeralSession, EphemeralSessionStore
from app.services.chat.prompting import HistoryTurn

SessionFactory = Callable[[], AsyncSession]


class Conversation(Protocol):
    """What the orchestrator needs from a session, saved or not."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    #: True for a saved session (as opposed to an in-memory one).
    persistent: bool

    @property
    def stores_turns(self) -> bool:
        """True when *this turn* is written to the database.

        A saved session stops storing if consent to store chats is withdrawn
        mid-conversation; an ephemeral session never stores.
        """
        ...

    async def history(self, limit: int) -> list[HistoryTurn]:
        """The newest ``limit`` turns, oldest first, **before** the current message."""
        ...

    async def store_user_turn(
        self,
        text: str,
        *,
        risk: RiskLevel,
        emotion: str | None,
        safety_event: SafetyEventSource | None,
    ) -> uuid.UUID | None:
        """Store the user's message and, when ``safety_event`` is set, its audit row."""
        ...

    async def store_assistant_turn(self, text: str, *, risk: RiskLevel) -> uuid.UUID | None:
        """Store the assistant's reply."""
        ...


async def _record_event(
    factory: SessionFactory | None,
    *,
    risk: RiskLevel,
    source: SafetyEventSource,
    user_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
) -> None:
    """Write one metadata-only ``safety_events`` row in its own transaction."""
    if factory is None:
        return
    async with factory() as db:
        await SafetyEventRepository(db).record(
            risk_level=risk, source=source, user_id=user_id, session_id=session_id
        )
        await db.commit()


class PersistentConversation:
    """A saved session: encrypted rows in the database."""

    persistent = True

    def __init__(
        self,
        factory: SessionFactory,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        store: bool = True,
    ) -> None:
        self._factory = factory
        self.session_id = session_id
        self.user_id = user_id
        #: False when consent to store chats was withdrawn after the session
        #: opened: the conversation is answered, but this turn writes nothing.
        self._store = store

    @property
    def stores_turns(self) -> bool:
        return self._store

    async def history(self, limit: int) -> list[HistoryTurn]:
        async with self._factory() as db:
            repo = ChatRepository(db)
            rows = await repo.recent_messages(self.session_id, limit=limit)
            return [
                HistoryTurn(
                    role=row.role.value,
                    text=repo.message_text(row),
                    crisis=int(row.risk_level) >= int(RiskLevel.CRISIS),
                )
                for row in rows
            ]

    async def store_user_turn(
        self,
        text: str,
        *,
        risk: RiskLevel,
        emotion: str | None,
        safety_event: SafetyEventSource | None,
    ) -> uuid.UUID | None:
        if not self._store:
            await _record_event_if(self._factory, safety_event, risk=risk)
            return None
        async with self._factory() as db:
            message = await ChatRepository(db).add_message(
                session_id=self.session_id,
                role=MessageRole.USER,
                content=text,
                risk_level=risk,
                emotion=emotion,
            )
            if safety_event is not None:
                await SafetyEventRepository(db).record(
                    risk_level=risk,
                    source=safety_event,
                    user_id=self.user_id,
                    session_id=self.session_id,
                )
            message_id = message.id
            await db.commit()
        return message_id

    async def store_assistant_turn(self, text: str, *, risk: RiskLevel) -> uuid.UUID | None:
        if not self._store:
            return None
        async with self._factory() as db:
            message = await ChatRepository(db).add_message(
                session_id=self.session_id,
                role=MessageRole.ASSISTANT,
                content=text,
                risk_level=risk,
            )
            message_id = message.id
            await db.commit()
        return message_id


async def _record_event_if(
    factory: SessionFactory | None, source: SafetyEventSource | None, *, risk: RiskLevel
) -> None:
    """Anonymous audit row, only when there is something to record."""
    if source is None:
        return
    await _record_event(factory, risk=risk, source=source, user_id=None, session_id=None)


class EphemeralConversation:
    """An unsaved session: process memory, no ``chat_sessions``/``messages`` rows."""

    persistent = False

    def __init__(
        self,
        store: EphemeralSessionStore,
        session: EphemeralSession,
        factory: SessionFactory | None = None,
    ) -> None:
        self._store = store
        self._session = session
        self._factory = factory
        self.session_id = session.id
        self.user_id = session.user_id

    @property
    def stores_turns(self) -> bool:
        return False

    async def history(self, limit: int) -> list[HistoryTurn]:
        if limit <= 0:
            return []
        return [
            HistoryTurn(role=turn.role, text=turn.text, crisis=turn.crisis)
            for turn in self._session.turns[-limit:]
        ]

    async def store_user_turn(
        self,
        text: str,
        *,
        risk: RiskLevel,
        emotion: str | None,
        safety_event: SafetyEventSource | None,
    ) -> uuid.UUID | None:
        turn = self._store.add_turn(
            self._session,
            role="user",
            text=text,
            risk=int(risk),
            emotion=emotion,
        )
        await _record_event_if(self._factory, safety_event, risk=risk)
        return turn.id

    async def store_assistant_turn(self, text: str, *, risk: RiskLevel) -> uuid.UUID | None:
        turn = self._store.add_turn(
            self._session,
            role="assistant",
            text=text,
            risk=int(risk),
            emotion=None,
        )
        return turn.id
