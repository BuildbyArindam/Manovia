"""Public exercise content/rules; private, consented completion history.

No model call, user-written text, planner answer, or inferred emotion is stored.
A completion is a self-report, not proof that the person followed a timer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep, require_consent
from app.core.errors import ApiError
from app.db.repos.exercises import ExerciseRepository
from app.models.base import as_utc
from app.models.enums import ConsentKind
from app.models.exercise_completion import ExerciseCompletion
from app.models.user import User
from app.services.exercises.engine import (
    ExerciseCatalog,
    ExerciseDefinition,
    Intensity,
    Recommendation,
    get_catalog,
    rating_summary,
    recommend,
)
from app.services.nlp.base import EMOTIONS

router = APIRouter(prefix="/exercises", tags=["exercises"])
CatalogDep = Annotated[ExerciseCatalog, Depends(get_catalog)]
ExerciseUser = Annotated[User, Depends(require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY))]
Rating = Annotated[StrictInt, Field(ge=1, le=5)]


class ExerciseListResponse(BaseModel):
    exercises: list[ExerciseDefinition]
    count: int


class RecommendationResponse(BaseModel):
    emotion: str
    intensity: Intensity
    recommendations: list[Recommendation]


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completion_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    pre_rating: Rating | None = None
    post_rating: Rating | None = None


class CompletionView(BaseModel):
    id: uuid.UUID
    exercise_id: str
    title: str
    definition_sha256: str
    duration_seconds: int
    pre_rating: int | None
    post_rating: int | None
    change: int | None
    summary: str
    created_at: datetime


class HistoryResponse(BaseModel):
    items: list[CompletionView]
    count: int
    total: int
    limit: int
    offset: int
    has_more: bool


def _view(entry: ExerciseCompletion) -> CompletionView:
    change, summary = rating_summary(entry.pre_rating, entry.post_rating)
    return CompletionView(
        id=entry.id,
        exercise_id=entry.exercise_id,
        title=entry.exercise_title,
        definition_sha256=entry.definition_sha256,
        duration_seconds=entry.duration_seconds,
        pre_rating=entry.pre_rating,
        post_rating=entry.post_rating,
        change=change,
        summary=summary,
        created_at=as_utc(entry.created_at),
    )


def _replay(
    entry: ExerciseCompletion, user_id: uuid.UUID, exercise_id: str, payload: CompletionRequest
) -> CompletionView:
    if (
        entry.user_id != user_id
        or entry.exercise_id != exercise_id
        or entry.pre_rating != payload.pre_rating
        or entry.post_rating != payload.post_rating
    ):
        raise ApiError(
            409,
            "completion_conflict",
            "This completion ID cannot be reused for a different completion.",
        )
    return _view(entry)


@router.get("", response_model=ExerciseListResponse)
async def list_exercises(catalog: CatalogDep) -> ExerciseListResponse:
    definitions = list(catalog.list())
    return ExerciseListResponse(exercises=definitions, count=len(definitions))


# Static routes must precede /{exercise_id}.
@router.get("/recommendations", response_model=RecommendationResponse)
async def exercise_recommendations(
    catalog: CatalogDep,
    emotion: Annotated[str, Query(max_length=20)],
    intensity: Annotated[Intensity, Query()] = "moderate",
) -> RecommendationResponse:
    if emotion not in EMOTIONS:
        raise ApiError(422, "emotion_invalid", "Choose a supported emotion label.")
    recommendations = recommend(emotion, intensity)
    if any(catalog.get(item.exercise_id) is None for item in recommendations):
        raise ApiError(
            503, "exercises_unavailable", "Exercise suggestions are not available right now."
        )
    return RecommendationResponse(
        emotion=emotion, intensity=intensity, recommendations=recommendations
    )


@router.get("/history", response_model=HistoryResponse)
async def exercise_history(
    user: ExerciseUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HistoryResponse:
    repository = ExerciseRepository(session)
    total = await repository.count_for_user(user.id)
    entries = await repository.list_for_user(user.id, limit=limit, offset=offset)
    return HistoryResponse(
        items=[_view(entry) for entry in entries],
        count=len(entries),
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(entries) < total,
    )


@router.get("/{exercise_id}", response_model=ExerciseDefinition)
async def get_exercise(exercise_id: str, catalog: CatalogDep) -> ExerciseDefinition:
    exercise = catalog.get(exercise_id)
    if exercise is None:
        raise ApiError(404, "exercise_not_found", "Exercise not found.")
    return exercise


@router.post(
    "/{exercise_id}/complete", response_model=CompletionView, status_code=status.HTTP_201_CREATED
)
async def complete_exercise(
    exercise_id: str,
    payload: CompletionRequest,
    response: Response,
    user: ExerciseUser,
    session: SessionDep,
    catalog: CatalogDep,
) -> CompletionView:
    exercise = catalog.get(exercise_id)
    if exercise is None:
        raise ApiError(404, "exercise_not_found", "Exercise not found.")
    # Keep primitive identity before a possible rollback expires ORM objects.
    user_id = user.id
    repository = ExerciseRepository(session)
    existing = await repository.get(payload.completion_id)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return _replay(existing, user_id, exercise_id, payload)
    try:
        entry = await repository.record(
            completion_id=payload.completion_id,
            user_id=user_id,
            exercise=exercise,
            pre_rating=payload.pre_rating,
            post_rating=payload.post_rating,
        )
        await session.commit()
    except IntegrityError:
        # Two retries can reach separate workers together. The PK is the final
        # arbiter; read the winner rather than create two history records.
        await session.rollback()
        winner = await repository.get(payload.completion_id)
        if winner is None:
            raise
        response.status_code = status.HTTP_200_OK
        return _replay(winner, user_id, exercise_id, payload)
    return _view(entry)
