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
        created_at: datetime | None = None,
    ) -> MoodEntry:
        """Store one check-in; ``valence``/``energy`` are 1-5 (CHECK-enforced).

        ``created_at`` may be supplied so the API can stamp a check-in with the
        same clock reading it used to decide what "today" is — otherwise a request
        that crosses local midnight could answer with a streak that does not
        include the entry it just wrote. Left unset, the column default applies.
        """
        entry = MoodEntry(
            user_id=user_id,
            valence=valence,
            energy=energy,
            emotions=list(emotions),
            factors=dict(factors or {}),
            note_encrypted=encrypt_optional(note),
        )
        if created_at is not None:
            entry.created_at = created_at
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
        until: datetime | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> Sequence[MoodEntry]:
        """Check-ins for a user, newest first (used by the trend chart).

        ``since`` is inclusive and ``until`` exclusive: a caller asking for one
        local day passes that day's start and the next day's start, which is the
        only spelling that is correct in a timezone with a DST jump in it (that
        day has 23 or 25 hours, so "start + 24h" is not the same thing).
        """
        stmt = select(MoodEntry).where(MoodEntry.user_id == user_id)
        if since is not None:
            stmt = stmt.where(MoodEntry.created_at >= since)
        if until is not None:
            stmt = stmt.where(MoodEntry.created_at < until)
        stmt = stmt.order_by(MoodEntry.created_at.desc(), MoodEntry.id).limit(limit).offset(offset)
        return (await self._session.execute(stmt)).scalars().all()

    async def list_instants(self, user_id: uuid.UUID) -> Sequence[datetime]:
        """Every ``created_at`` for a user, newest first, and nothing else.

        The streak needs the days somebody checked in — not what they wrote. This
        query projects one column so the notes never leave the database for a
        calculation that cannot use them.
        """
        stmt = (
            select(MoodEntry.created_at)
            .where(MoodEntry.user_id == user_id)
            .order_by(MoodEntry.created_at.desc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def delete(self, entry: MoodEntry) -> None:
        """Remove one check-in. The caller decides whether it may be removed."""
        await self._session.delete(entry)
        await self._session.flush()

    @staticmethod
    def note(entry: MoodEntry) -> str | None:
        """Decrypt the optional note of a check-in."""
        return decrypt_optional(entry.note_encrypted)
