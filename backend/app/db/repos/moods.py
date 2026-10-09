"""Mood check-ins. The optional free-text note is encrypted."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_optional, encrypt_optional
from app.models.mood import MoodEntry


class MoodRepository:
    """Thin data access for :class:`~app.models.mood.MoodEntry`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID,
        valence: int,
        energy: int,
        emotions: Sequence[str] = (),
        factors: dict[str, object] | None = None,
        note: str | None = None,
    ) -> MoodEntry:
        """Store one check-in; ``valence``/``energy`` are 1-5 (CHECK-enforced)."""
        entry = MoodEntry(
            user_id=user_id,
            valence=valence,
            energy=energy,
            emotions=list(emotions),
            factors=dict(factors or {}),
            note_encrypted=encrypt_optional(note),
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get(self, entry_id: uuid.UUID) -> MoodEntry | None:
        """Fetch one check-in by id."""
        return await self._session.get(MoodEntry, entry_id)

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        since: datetime | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> Sequence[MoodEntry]:
        """Check-ins for a user, newest first (used by the trend chart)."""
        stmt = select(MoodEntry).where(MoodEntry.user_id == user_id)
        if since is not None:
            stmt = stmt.where(MoodEntry.created_at >= since)
        stmt = stmt.order_by(MoodEntry.created_at.desc(), MoodEntry.id).limit(limit).offset(offset)
        return (await self._session.execute(stmt)).scalars().all()

    @staticmethod
    def note(entry: MoodEntry) -> str | None:
        """Decrypt the optional note of a check-in."""
        return decrypt_optional(entry.note_encrypted)
