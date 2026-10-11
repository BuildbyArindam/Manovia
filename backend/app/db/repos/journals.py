"""Journal entries: title and body are envelope-encrypted with AAD; numeric scores are optional.

Every write uses :func:`~app.services.journal.crypto.encrypt_field` with the
owner's wrapped AES-256-GCM data key and Associated Data ``(user_id, "title")``
or ``(user_id, "body")``. Every read verifies the same Associated Data via
:func:`~app.services.journal.crypto.decrypt_field`, so swapping ciphertexts
between users or between columns fails authentication.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_field, encrypt_field
from app.db.repos.users import UserRepository
from app.models.base import utcnow
from app.models.journal import JournalEntry


class JournalRepository:
    """Thin data access for :class:`~app.models.journal.JournalEntry`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        title: str,
        body: str,
        sentiment: float | None = None,
        emotion_scores: dict[str, float] | None = None,
        template: str | None = None,
        prompt_id: str | None = None,
        created_at: datetime | None = None,
    ) -> JournalEntry:
        """Envelope-encrypt and store one journal entry."""
        wrapped_dek, key_id = await UserRepository(self._session).ensure_data_key(user_id)
        now = created_at or utcnow()
        entry = JournalEntry(
            user_id=user_id,
            title_encrypted=encrypt_field(
                title, user_id=user_id, field_name="title", wrapped_dek=wrapped_dek
            ),
            body_encrypted=encrypt_field(
                body, user_id=user_id, field_name="body", wrapped_dek=wrapped_dek
            ),
            key_id=key_id,
            sentiment=sentiment,
            emotion_scores=dict(emotion_scores) if emotion_scores is not None else None,
            template=template,
            prompt_id=prompt_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def update(
        self,
        entry: JournalEntry,
        *,
        title: str,
        body: str,
        sentiment: float | None = None,
        emotion_scores: dict[str, float] | None = None,
        template: str | None = None,
        prompt_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> JournalEntry:
        """Re-encrypt and update an existing journal entry in place."""
        wrapped_dek, key_id = await UserRepository(self._session).ensure_data_key(entry.user_id)
        entry.title_encrypted = encrypt_field(
            title, user_id=entry.user_id, field_name="title", wrapped_dek=wrapped_dek
        )
        entry.body_encrypted = encrypt_field(
            body, user_id=entry.user_id, field_name="body", wrapped_dek=wrapped_dek
        )
        entry.key_id = key_id
        entry.sentiment = sentiment
        entry.emotion_scores = dict(emotion_scores) if emotion_scores is not None else None
        entry.template = template
        entry.prompt_id = prompt_id
        entry.updated_at = updated_at or utcnow()
        await self._session.flush()
        return entry

    async def get(self, entry_id: uuid.UUID) -> JournalEntry | None:
        """Fetch one entry by id."""
        return await self._session.get(JournalEntry, entry_id)

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        template: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[JournalEntry]:
        """Entries for a user, newest first."""
        stmt = select(JournalEntry).where(JournalEntry.user_id == user_id)
        if since is not None:
            stmt = stmt.where(JournalEntry.created_at >= since)
        if until is not None:
            stmt = stmt.where(JournalEntry.created_at < until)
        if template is not None:
            stmt = stmt.where(JournalEntry.template == template)
        stmt = (
            stmt.order_by(JournalEntry.created_at.desc(), JournalEntry.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def count_for_user(
        self,
        user_id: uuid.UUID,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        template: str | None = None,
    ) -> int:
        """Total journal entries matching the filter for pagination."""
        stmt = select(func.count()).select_from(JournalEntry).where(JournalEntry.user_id == user_id)
        if since is not None:
            stmt = stmt.where(JournalEntry.created_at >= since)
        if until is not None:
            stmt = stmt.where(JournalEntry.created_at < until)
        if template is not None:
            stmt = stmt.where(JournalEntry.template == template)
        return int((await self._session.execute(stmt)).scalar_one())

    async def delete(self, entry: JournalEntry) -> None:
        """Delete one journal entry."""
        await self._session.delete(entry)
        await self._session.flush()

    @staticmethod
    def title(entry: JournalEntry, *, wrapped_dek: bytes | None = None) -> str:
        """Decrypt the title of an entry, verifying ``(entry.user_id, 'title')`` AAD."""
        return decrypt_field(
            entry.title_encrypted,
            user_id=entry.user_id,
            field_name="title",
            wrapped_dek=wrapped_dek,
        )

    @staticmethod
    def body(entry: JournalEntry, *, wrapped_dek: bytes | None = None) -> str:
        """Decrypt the body of an entry, verifying ``(entry.user_id, 'body')`` AAD."""
        return decrypt_field(
            entry.body_encrypted,
            user_id=entry.user_id,
            field_name="body",
            wrapped_dek=wrapped_dek,
        )
