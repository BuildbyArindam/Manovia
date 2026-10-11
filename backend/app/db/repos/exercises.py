"""Never-commit repository for owner-scoped exercise completions."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exercise_completion import ExerciseCompletion
from app.services.exercises.engine import ExerciseDefinition


class ExerciseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, completion_id: uuid.UUID) -> ExerciseCompletion | None:
        return await self.session.get(ExerciseCompletion, completion_id)

    async def record(
        self,
        *,
        completion_id: uuid.UUID,
        user_id: uuid.UUID,
        exercise: ExerciseDefinition,
        pre_rating: int | None,
        post_rating: int | None,
    ) -> ExerciseCompletion:
        entry = ExerciseCompletion(
            id=completion_id,
            user_id=user_id,
            exercise_id=exercise.id,
            exercise_title=exercise.title.en,
            definition_sha256=exercise.definition_sha256,
            duration_seconds=exercise.duration_seconds,
            pre_rating=pre_rating,
            post_rating=post_rating,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int = 20, offset: int = 0
    ) -> list[ExerciseCompletion]:
        statement = (
            select(ExerciseCompletion)
            .where(ExerciseCompletion.user_id == user_id)
            .order_by(ExerciseCompletion.created_at.desc(), ExerciseCompletion.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(ExerciseCompletion)
            .where(ExerciseCompletion.user_id == user_id)
        )
        return int((await self.session.execute(statement)).scalar_one())
