"""Assessment runs (PHQ-9, GAD-7). Scoring lives elsewhere; this stores it.

The repository takes the total and the band it was mapped to, so the
instrument logic (which must be reviewable and tested on its own) stays out of
the data layer.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentResult
from app.models.enums import AssessmentBand, AssessmentInstrument


class AssessmentRepository:
    """Thin data access for :class:`~app.models.assessment.AssessmentResult`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        user_id: uuid.UUID,
        instrument: AssessmentInstrument,
        answers: Mapping[str, int],
        total: int,
        band: AssessmentBand,
    ) -> AssessmentResult:
        """Store one completed questionnaire run."""
        result = AssessmentResult(
            user_id=user_id,
            instrument=instrument,
            answers=dict(answers),
            total=total,
            band=band,
        )
        self._session.add(result)
        await self._session.flush()
        return result

    async def get(self, result_id: uuid.UUID) -> AssessmentResult | None:
        """Fetch one run by id."""
        return await self._session.get(AssessmentResult, result_id)

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        instrument: AssessmentInstrument | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[AssessmentResult]:
        """Runs for a user, newest first, optionally filtered by instrument."""
        stmt = select(AssessmentResult).where(AssessmentResult.user_id == user_id)
        if instrument is not None:
            stmt = stmt.where(AssessmentResult.instrument == instrument)
        stmt = stmt.order_by(AssessmentResult.created_at.desc(), AssessmentResult.id).limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        return (await self._session.execute(stmt)).scalars().all()
