"""Daily-plan items: one person's suggestions for one local day.

The planner service is pure; this repository is the only place its output
touches the database. Writes go through the unique ``(user_id, plan_date,
kind)`` constraint, so a second generation for the same local day cannot
double-insert a kind — the API treats an existing day as already planned.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PlanItemKind, PlanItemStatus
from app.models.plan import PlanItem


class PlanRepository:
    """Thin data access for :class:`~app.models.plan.PlanItem`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_day(self, user_id: uuid.UUID, plan_date: date) -> Sequence[PlanItem]:
        """All items for one local day, in stable creation order."""
        stmt = (
            select(PlanItem)
            .where(PlanItem.user_id == user_id, PlanItem.plan_date == plan_date)
            .order_by(PlanItem.created_at, PlanItem.id)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get(self, item_id: uuid.UUID) -> PlanItem | None:
        """Fetch one item by id (ownership is checked by the caller)."""
        return await self._session.get(PlanItem, item_id)

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        plan_date: date,
        kind: PlanItemKind,
        title: str,
        why: str,
        ref: str | None = None,
    ) -> PlanItem:
        """Insert one pending suggestion. Never commits; the API owns that."""
        item = PlanItem(
            user_id=user_id,
            plan_date=plan_date,
            kind=kind,
            ref=ref,
            title=title,
            why=why,
            status=PlanItemStatus.PENDING,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def set_status(self, item: PlanItem, status: PlanItemStatus) -> PlanItem:
        """Move one item to ``status``. Changing one's mind is allowed."""
        item.status = status
        await self._session.flush()
        return item
