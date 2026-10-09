"""Safety events: metadata only.

Nothing in this repository accepts a string that describes what a person said.
The detectors pass the ids they already have (session, user), a risk tier, and
the source that fired — see :mod:`app.models.safety_event` for why there is no
textual column to write to.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import RiskLevel, SafetyEventSource
from app.models.safety_event import SafetyEvent


class SafetyEventRepository:
    """Thin data access for :class:`~app.models.safety_event.SafetyEvent`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        risk_level: RiskLevel | int,
        source: SafetyEventSource,
        user_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
    ) -> SafetyEvent:
        """Append one detection."""
        event = SafetyEvent(
            user_id=user_id,
            session_id=session_id,
            risk_level=int(risk_level),
            source=source,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_for_user(
        self, user_id: uuid.UUID, *, since: datetime | None = None, limit: int = 50
    ) -> Sequence[SafetyEvent]:
        """Detections for a user, newest first."""
        stmt = select(SafetyEvent).where(SafetyEvent.user_id == user_id)
        if since is not None:
            stmt = stmt.where(SafetyEvent.created_at >= since)
        stmt = stmt.order_by(SafetyEvent.created_at.desc(), SafetyEvent.id).limit(limit)
        return (await self._session.execute(stmt)).scalars().all()

    async def count_by_risk_level(self, *, user_id: uuid.UUID | None = None) -> dict[int, int]:
        """Aggregate counts by tier, for the safety dashboard.

        Aggregating in SQL keeps this repository free of any read that could
        surface a person's words.
        """
        stmt = select(SafetyEvent.risk_level, func.count()).group_by(SafetyEvent.risk_level)
        if user_id is not None:
            stmt = stmt.where(SafetyEvent.user_id == user_id)
        rows = (await self._session.execute(stmt)).all()
        return {int(level): int(count) for level, count in rows}
