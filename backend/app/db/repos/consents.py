"""Consent decisions: append a record, read the effective state."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consent import Consent
from app.models.enums import ConsentKind


class ConsentRepository:
    """Thin data access for :class:`~app.models.consent.Consent`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID,
        kind: ConsentKind,
        version: str,
        granted: bool = True,
    ) -> Consent:
        """Append one decision. Re-consenting never rewrites history."""
        consent = Consent(user_id=user_id, kind=kind, version=version, granted=granted)
        self._session.add(consent)
        await self._session.flush()
        return consent

    async def latest(self, *, user_id: uuid.UUID, kind: ConsentKind) -> Consent | None:
        """The user's most recent decision of this kind, if any."""
        stmt = (
            select(Consent)
            .where(Consent.user_id == user_id, Consent.kind == kind)
            .order_by(Consent.created_at.desc(), Consent.id)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalars().one_or_none()

    async def is_granted(self, *, user_id: uuid.UUID, kind: ConsentKind, version: str) -> bool:
        """True when the newest row for that kind and version is a grant.

        Guards against "accepted version 1, then withdrew version 1": the latest
        row wins, so a withdrawal is honoured.
        """
        stmt = (
            select(Consent)
            .where(Consent.user_id == user_id, Consent.kind == kind, Consent.version == version)
            .order_by(Consent.created_at.desc(), Consent.id)
            .limit(1)
        )
        consent = (await self._session.execute(stmt)).scalars().one_or_none()
        return bool(consent is not None and consent.granted)

    async def history(self, user_id: uuid.UUID) -> Sequence[Consent]:
        """Every decision for a user, oldest first."""
        stmt = (
            select(Consent)
            .where(Consent.user_id == user_id)
            .order_by(Consent.created_at, Consent.id)
        )
        return (await self._session.execute(stmt)).scalars().all()
