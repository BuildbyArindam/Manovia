"""Chat sessions and messages. Message text is encrypted on the way in.

Writes go through :meth:`ChatRepository.add_message`, which encrypts before the
row reaches the database, so there is no path that stores a message in the
clear. Reads return model rows whose ``content_encrypted`` is still a blob;
:meth:`ChatRepository.message_text` is the explicit, auditable way to decrypt.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
from app.models.base import utcnow
from app.models.chat_session import ChatSession
from app.models.enums import MessageRole, RiskLevel
from app.models.message import Message


class ChatRepository:
    """Thin data access for :class:`ChatSession` and :class:`Message`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, user_id: uuid.UUID) -> ChatSession:
        """Open a session for a user."""
        chat_session = ChatSession(user_id=user_id)
        self._session.add(chat_session)
        await self._session.flush()
        return chat_session

    async def get(self, session_id: uuid.UUID) -> ChatSession | None:
        """Fetch a session by id."""
        return await self._session.get(ChatSession, session_id)

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> Sequence[ChatSession]:
        """Most recent sessions for a user."""
        stmt = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.created_at.desc(), ChatSession.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def end(self, session_id: uuid.UUID) -> ChatSession | None:
        """Stamp ``ended_at``; ``None`` when the session is unknown or already closed.

        The row is written through the ORM rather than a Core ``UPDATE`` so that
        a caller reading the same session back inside the request sees the new
        value instead of a stale identity map.
        """
        chat_session = await self._session.get(ChatSession, session_id)
        if chat_session is None or chat_session.ended_at is not None:
            return None
        chat_session.ended_at = utcnow()
        await self._session.flush()
        return chat_session

    async def add_message(
        self,
        *,
        session_id: uuid.UUID,
        role: MessageRole,
        content: str,
        risk_level: RiskLevel | int = RiskLevel.NONE,
        emotion: str | None = None,
    ) -> Message:
        """Encrypt ``content`` and append the message to a session.

        Risk triage normally fills ``risk_level`` before this call; the default
        is ``NONE`` so a row can never be stored without a tier.
        """
        message = Message(
            session_id=session_id,
            role=role,
            content_encrypted=encrypt(content),
            risk_level=int(risk_level),
            emotion=emotion,
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def list_messages(
        self, session_id: uuid.UUID, *, limit: int = 100, offset: int = 0
    ) -> Sequence[Message]:
        """Messages of one session, oldest first."""
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at, Message.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def recent_messages(self, session_id: uuid.UUID, *, limit: int) -> Sequence[Message]:
        """The newest ``limit`` messages of a session, oldest first.

        This is the conversation window the chat orchestrator feeds the model:
        the *tail* of the thread, in reading order. ``limit <= 0`` is an empty
        window (not "everything"), so a misconfigured window can never widen.
        """
        if limit <= 0:
            return []
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return list(reversed(rows))

    @staticmethod
    def message_text(message: Message) -> str:
        """Decrypt one stored message."""
        return decrypt(message.content_encrypted)
