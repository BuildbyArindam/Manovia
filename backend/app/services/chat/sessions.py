"""Opening and finding chat sessions — saved or ephemeral — with ownership enforced.

One place decides "which conversation is this, and is it yours?":

* ``create(persistent=False)`` → an in-memory session (30-minute idle TTL).
* ``create(persistent=True)`` → a database session, **only** if the user has
  granted ``store_chat`` at the current document version. Saving history is the
  one optional consent in the product; everything else works without it.
* ``open(user_id, session_id)`` → the conversation, or a 404.

**Ownership is a 404, not a 403.** A session id that belongs to somebody else is
indistinguishable from one that never existed. A 403 would confirm that the id
is real, which is information about another person's activity.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.content import ConsentDocuments, load_consent_documents
from app.core.errors import ApiError
from app.db.repos import ChatRepository, ConsentRepository
from app.models.enums import ConsentKind
from app.services.chat.conversation import (
    Conversation,
    EphemeralConversation,
    PersistentConversation,
    SessionFactory,
)
from app.services.chat.ephemeral import EphemeralSessionStore
from app.services.privacy.store import chat_storage_allowed


@dataclass(frozen=True)
class SessionInfo:
    """What a client may know about a session."""

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    ended_at: datetime | None
    #: True for a saved (database) session.
    persistent: bool
    #: When an ephemeral session lapses if nothing more is said; ``None`` if saved.
    expires_at: datetime | None


@dataclass(frozen=True)
class OpenedSession:
    """A session's public info plus the conversation to run the pipeline on."""

    info: SessionInfo
    conversation: Conversation


@dataclass(frozen=True)
class StoredTurn:
    """A message as the history endpoint returns it (plaintext, for its owner)."""

    id: uuid.UUID
    role: str
    text: str
    risk_level: int
    emotion: str | None
    created_at: datetime


@dataclass(frozen=True)
class ChatMemory:
    """A saved-chat summary returned only to its owning user."""

    session_id: uuid.UUID
    summary: str


def session_not_found() -> ApiError:
    """The one error for \"no such session\" and \"not your session\"."""
    return ApiError(404, "session_not_found", "We couldn't find that conversation.")


class ChatSessionService:
    """Creates sessions and resolves ``(user, session id)`` to a conversation."""

    def __init__(
        self,
        factory: SessionFactory,
        ephemeral: EphemeralSessionStore,
        *,
        documents: ConsentDocuments | None = None,
    ) -> None:
        self._factory = factory
        self._ephemeral = ephemeral
        self._documents = documents or load_consent_documents()

    @property
    def ephemeral(self) -> EphemeralSessionStore:
        return self._ephemeral

    async def _store_chat_granted(self, user_id: uuid.UUID) -> bool:
        async with self._factory() as db:
            return await ConsentRepository(db).is_granted(
                user_id=user_id,
                kind=ConsentKind.STORE_CHAT,
                version=self._documents.version_for(ConsentKind.STORE_CHAT),
            )

    async def _chat_storage_allowed(self, user_id: uuid.UUID) -> bool:
        """The Day 22 privacy floor: "do not save my chats" is a hard veto.

        Consent and this setting are both required, and they are not redundant:
        the consent record is the agreement, the setting is the off switch a
        person can flip in Settings → Privacy without touching their consents.
        """
        async with self._factory() as db:
            return await chat_storage_allowed(db, user_id)

    async def create(self, user_id: uuid.UUID, *, persistent: bool) -> SessionInfo:
        """Open a session. Saving history needs the ``store_chat`` consent."""
        if not persistent:
            session = self._ephemeral.create(user_id)
            return SessionInfo(
                id=session.id,
                user_id=user_id,
                created_at=session.created_at,
                ended_at=None,
                persistent=False,
                expires_at=self._ephemeral.to_datetime(session.expires_at),
            )
        if not await self._store_chat_granted(user_id):
            raise ApiError(
                403,
                "consent_required",
                f"Consent required: {ConsentKind.STORE_CHAT.value}.",
            )
        if not await self._chat_storage_allowed(user_id):
            raise ApiError(
                403,
                "chat_storage_disabled",
                "Saving chats is switched off in your privacy settings.",
            )
        async with self._factory() as db:
            row = await ChatRepository(db).create(user_id=user_id)
            await db.commit()
            return SessionInfo(
                id=row.id,
                user_id=row.user_id,
                created_at=row.created_at,
                ended_at=None,
                persistent=True,
                expires_at=None,
            )

    async def open(
        self, user_id: uuid.UUID, session_id: uuid.UUID, *, for_send: bool = False
    ) -> OpenedSession:
        """Resolve a session the caller owns, or raise the 404.

        ``for_send=True`` is for endpoints that are about to add a message: it
        slides an ephemeral session's expiry, refuses an ended session (409), and
        re-checks ``store_chat`` for a saved one so a withdrawal takes effect on
        the very next message.
        """
        ephemeral = self._ephemeral.get(session_id, user_id, touch=for_send)
        if ephemeral is not None:
            return OpenedSession(
                info=SessionInfo(
                    id=ephemeral.id,
                    user_id=user_id,
                    created_at=ephemeral.created_at,
                    ended_at=None,
                    persistent=False,
                    expires_at=self._ephemeral.to_datetime(ephemeral.expires_at),
                ),
                conversation=EphemeralConversation(self._ephemeral, ephemeral, self._factory),
            )

        async with self._factory() as db:
            row = await ChatRepository(db).get(session_id)
            if row is None or row.user_id != user_id:
                raise session_not_found()
            info = SessionInfo(
                id=row.id,
                user_id=row.user_id,
                created_at=row.created_at,
                ended_at=row.ended_at,
                persistent=True,
                expires_at=None,
            )
        if for_send and info.ended_at is not None:
            raise ApiError(409, "session_ended", "That conversation has ended. Start a new one.")
        if for_send:
            # Both the consent and the privacy setting must allow saving;
            # either one turning off stops new turns from being written.
            store = await self._store_chat_granted(user_id) and await self._chat_storage_allowed(
                user_id
            )
        else:
            store = True
        return OpenedSession(
            info=info,
            conversation=PersistentConversation(
                self._factory, session_id=session_id, user_id=user_id, store=store
            ),
        )

    async def messages(self, opened: OpenedSession, *, limit: int = 100) -> list[StoredTurn]:
        """The session's messages, oldest first, decrypted for their owner."""
        if not opened.info.persistent:
            ephemeral = self._ephemeral.get(opened.info.id, opened.info.user_id)
            if ephemeral is None:
                raise session_not_found()
            return [
                StoredTurn(
                    id=turn.id,
                    role=turn.role,
                    text=turn.text,
                    risk_level=turn.risk,
                    emotion=turn.emotion,
                    created_at=turn.created_at,
                )
                for turn in ephemeral.turns[-limit:]
            ]
        async with self._factory() as db:
            repo = ChatRepository(db)
            rows = await repo.recent_messages(opened.info.id, limit=limit)
            return [
                StoredTurn(
                    id=row.id,
                    role=row.role.value,
                    text=repo.message_text(row),
                    risk_level=int(row.risk_level),
                    emotion=row.emotion,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def memories(self, user_id: uuid.UUID) -> list[ChatMemory]:
        """Decrypted rolling summaries for saved chats the user owns."""
        async with self._factory() as db:
            rows = await ChatRepository(db).memories_for_user(user_id)
        return [ChatMemory(session_id=session_id, summary=summary) for session_id, summary in rows]

    async def delete_memories(self, user_id: uuid.UUID) -> int:
        """Clear every summary and permanently disable memory for existing chats."""
        async with self._factory() as db:
            count = await ChatRepository(db).delete_memories_for_user(user_id)
            await db.commit()
            return count
