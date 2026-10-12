"""Mood check-ins. The optional free-text note is encrypted."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_optional, encrypt_optional
from app.models.mood import MoodEntry


class MoodInsightRow(NamedTuple):
    """The five columns an insight is allowed to look at, and no others.

    Named rather than passed around as a bare tuple so a future column cannot be
    appended to the projection and quietly arrive in somebody's summary. The
    encrypted note is not in this type, which is the point: the insights query
    projects these columns and nothing else, so no insight code path can read a
    private sentence even by accident.
    """

    created_at: datetime
    valence: int
    energy: int
    emotions: list[str]
    factors: dict[str, Any]


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
        client_id: uuid.UUID | None = None,
    ) -> MoodEntry:
        """Store one check-in; ``valence``/``energy`` are 1-5 (CHECK-enforced).

        ``created_at`` may be supplied so the API can stamp a check-in with the
        same clock reading it used to decide what "today" is — otherwise a request
        that crosses local midnight could answer with a streak that does not
        include the entry it just wrote. Left unset, the column default applies.

        ``client_id`` is the idempotency key of a check-in queued offline (Day
        25). ``(user_id, client_id)`` is unique, so a second insert with the same
        key raises ``IntegrityError`` — the caller reads the winner with
        :meth:`get_by_client_id` instead of storing a duplicate.
        """
        entry = MoodEntry(
            user_id=user_id,
            valence=valence,
            energy=energy,
            emotions=list(emotions),
            factors=dict(factors or {}),
            note_encrypted=encrypt_optional(note),
            client_id=client_id,
        )
        if created_at is not None:
            entry.created_at = created_at
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def get(self, entry_id: uuid.UUID) -> MoodEntry | None:
        """Fetch one check-in by id."""
        return await self._session.get(MoodEntry, entry_id)

    async def get_by_client_id(self, user_id: uuid.UUID, client_id: uuid.UUID) -> MoodEntry | None:
        """The check-in this user already stored under ``client_id``, if any.

        Scoped to the user on purpose: a client id is only unique per person, and
        a guessed id belonging to somebody else must read as "not stored yet",
        never as their check-in.
        """
        stmt = select(MoodEntry).where(
            MoodEntry.user_id == user_id, MoodEntry.client_id == client_id
        )
        return (await self._session.execute(stmt)).scalars().first()

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

    async def insights_rows(
        self, user_id: uuid.UUID, *, since: datetime | None = None
    ) -> Sequence[MoodInsightRow]:
        """Everything an insight may read, oldest first, and nothing else.

        Two deliberate choices. The projection is explicit (see
        :class:`MoodInsightRow`), so the encrypted note is never decrypted, never
        loaded and never one refactor away from a summary. And there is no
        ``limit``: an average over a window that has been silently truncated is a
        wrong number presented as a right one, so the caller bounds the query by
        *time* instead — ``since`` — and the insight layer bounds the window.
        """
        stmt = select(
            MoodEntry.created_at,
            MoodEntry.valence,
            MoodEntry.energy,
            MoodEntry.emotions,
            MoodEntry.factors,
        ).where(MoodEntry.user_id == user_id)
        if since is not None:
            stmt = stmt.where(MoodEntry.created_at >= since)
        stmt = stmt.order_by(MoodEntry.created_at)
        rows = (await self._session.execute(stmt)).all()
        return [
            MoodInsightRow(
                created_at=row.created_at,
                valence=row.valence,
                energy=row.energy,
                emotions=list(row.emotions or []),
                factors=dict(row.factors or {}),
            )
            for row in rows
        ]

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        """How many check-ins this person has in total.

        Used so an insight can say "12 of your 40 check-ins" instead of implying
        that the window it looked at is everything the person has ever written.
        """
        stmt = select(func.count()).select_from(MoodEntry).where(MoodEntry.user_id == user_id)
        return int((await self._session.execute(stmt)).scalar_one())

    async def delete(self, entry: MoodEntry) -> None:
        """Remove one check-in. The caller decides whether it may be removed."""
        await self._session.delete(entry)
        await self._session.flush()

    @staticmethod
    def note(entry: MoodEntry) -> str | None:
        """Decrypt the optional note of a check-in."""
        return decrypt_optional(entry.note_encrypted)
