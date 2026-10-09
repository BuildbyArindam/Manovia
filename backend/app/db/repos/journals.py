"""Journal entries: title and body are encrypted, sentiment is not.

Keeping ``sentiment`` in the clear is a deliberate trade — it lets the trend
view aggregate in SQL without decrypting anybody's diary. See
:mod:`app.models.journal`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt, encrypt
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
    ) -> JournalEntry:
        """Encrypt and store one entry."""
        entry = JournalEntry(
            user_id=user_id,
            title_encrypted=encrypt(title),
            body_encrypted=encrypt(body),
            sentiment=sentiment,
        )
        self._session.add(entry)
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
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[JournalEntry]:
        """Entries for a user, newest first."""
        stmt = select(JournalEntry).where(JournalEntry.user_id == user_id)
        if since is not None:
            stmt = stmt.where(JournalEntry.created_at >= since)
        stmt = (
            stmt.order_by(JournalEntry.created_at.desc(), JournalEntry.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.execute(stmt)).scalars().all()

    @staticmethod
    def title(entry: JournalEntry) -> str:
        """Decrypt the title of an entry."""
        return decrypt(entry.title_encrypted)

    @staticmethod
    def body(entry: JournalEntry) -> str:
        """Decrypt the body of an entry."""
        return decrypt(entry.body_encrypted)
