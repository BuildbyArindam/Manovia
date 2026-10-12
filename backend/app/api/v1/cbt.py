"""CBT reflection: screen each step, then encrypt every written field."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.deps import EscalatorDep, MLClassifierDep, RuleEngineDep, SessionDep, require_consent
from app.api.v1.mood import SAFETY_UNAVAILABLE_MESSAGE
from app.core.crypto import CipherNotConfiguredError, DecryptError, decrypt_field, encrypt_field
from app.core.errors import ApiError
from app.db.repos.users import UserRepository
from app.models.cbt import CBTRecord, cbt_aad
from app.models.enums import ConsentKind
from app.models.user import User
from app.services.cbt import QUESTION, TRAPS, suggest
from app.services.llm import build_llm_chain
from app.services.llm.base import LLMProvider
from app.services.safety.base import RiskLevel
from app.services.safety.escalation import Escalator
from app.services.safety.ml_classifier import SafetyClassifier
from app.services.safety.pipeline import run_ensemble
from app.services.safety.rules import RuleEngine

router = APIRouter(prefix="/cbt", tags=["cbt"])
CBTUser = Annotated[User, Depends(require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY))]
Emotion = Literal["anxiety", "sadness", "anger", "fear", "shame", "guilt", "frustration", "other"]
HelpTag = Literal[
    "looking at evidence",
    "another perspective",
    "taking a pause",
    "talking to someone",
    "writing it down",
]
TextField = Literal[
    "situation", "automatic_thought", "evidence_for", "evidence_against", "balanced_thought"
]
FIELDS: tuple[TextField, ...] = (
    "situation",
    "automatic_thought",
    "evidence_for",
    "evidence_against",
    "balanced_thought",
)


class ScreenInput(BaseModel):
    field: TextField
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Please enter some text")
        return value.strip()


class RecordInput(BaseModel):
    situation: str = Field(min_length=1, max_length=4000)
    automatic_thought: str = Field(min_length=1, max_length=4000)
    evidence_for: str = Field(min_length=1, max_length=4000)
    evidence_against: str = Field(min_length=1, max_length=4000)
    balanced_thought: str = Field(min_length=1, max_length=4000)
    emotion: Emotion
    intensity_before: int = Field(ge=0, le=100)
    intensity_after: int = Field(ge=0, le=100)
    what_helped: list[HelpTag] = Field(default_factory=list, max_length=5)

    @field_validator(*FIELDS)
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Please enter some text")
        return value.strip()


class RecordView(RecordInput):
    id: uuid.UUID
    created_at: datetime


async def screen(
    text: str,
    *,
    request: Request,
    user: User,
    engine: RuleEngine,
    classifier: SafetyClassifier,
    escalator: Escalator,
) -> dict[str, object] | None:
    """Fail closed on detector errors; never send high-risk content to a model."""
    try:
        settings = request.app.state.settings
        assessment, _decision = await run_ensemble(
            text,
            user.language,
            engine,
            classifier,
            min_confidence=settings.safety_ml_min_confidence,
            crisis_mass_floor=settings.safety_ml_crisis_mass_floor,
            suspicion_floor=settings.safety_ml_suspicion_floor,
        )
        if assessment.level < RiskLevel.HIGH:
            return None
        plan = escalator.plan(assessment, region=user.region, locale=user.language)
        return {
            "level": plan.level_code,
            "message": plan.message,
            "emergency": plan.emergency,
            "resources": list(plan.resources),
        }
    except Exception as exc:
        raise ApiError(503, "safety_unavailable", SAFETY_UNAVAILABLE_MESSAGE) from exc


@router.post("/screen")
async def screen_step(
    payload: ScreenInput,
    request: Request,
    user: CBTUser,
    engine: RuleEngineDep,
    classifier: MLClassifierDep,
    escalator: EscalatorDep,
) -> dict[str, object]:
    crisis = await screen(
        payload.text,
        request=request,
        user=user,
        engine=engine,
        classifier=classifier,
        escalator=escalator,
    )
    return {"interrupted": crisis is not None, "crisis": crisis}


@router.post("/traps")
async def thinking_traps(
    payload: ScreenInput,
    request: Request,
    user: CBTUser,
    engine: RuleEngineDep,
    classifier: MLClassifierDep,
    escalator: EscalatorDep,
) -> dict[str, object]:
    if payload.field != "automatic_thought":
        raise ApiError(
            422, "invalid_step", "Suggestions are only available for the automatic thought."
        )
    crisis = await screen(
        payload.text,
        request=request,
        user=user,
        engine=engine,
        classifier=classifier,
        escalator=escalator,
    )
    if crisis is not None:
        return {
            "interrupted": True,
            "crisis": crisis,
            "candidates": [],
            "question": QUESTION,
            "source": "none",
        }
    # Provider can be overridden in tests. No LLM call on a high-risk input.
    injected: LLMProvider | None = getattr(request.app.state, "cbt_llm", None)
    provider = injected or build_llm_chain(request.app.state.settings)
    try:
        ids, source = await suggest(
            payload.text, provider, redact_names=request.app.state.settings.llm_redact_names
        )
    finally:
        if injected is None:
            await provider.aclose()
    return {
        "interrupted": False,
        "crisis": None,
        "candidates": [{"id": name, "explanation": TRAPS[name]} for name in ids],
        "question": QUESTION,
        "source": source,
    }


def view(row: CBTRecord, wrapped: bytes | None) -> RecordView:
    try:
        texts = {
            field: decrypt_field(
                getattr(row, f"{field}_encrypted"),
                user_id=row.user_id,
                field_name=cbt_aad(field),
                wrapped_dek=wrapped,
            )
            for field in FIELDS
        }
    except (CipherNotConfiguredError, DecryptError) as exc:
        raise ApiError(
            503, "encryption_unavailable", "Thought records are unavailable right now."
        ) from exc
    return RecordView.model_validate(
        {
            "id": row.id,
            "created_at": row.created_at,
            "emotion": row.emotion,
            "intensity_before": row.intensity_before,
            "intensity_after": row.intensity_after,
            "what_helped": row.what_helped,
            **texts,
        }
    )


@router.post("/records")
async def create_record(
    payload: RecordInput,
    request: Request,
    user: CBTUser,
    session: SessionDep,
    engine: RuleEngineDep,
    classifier: MLClassifierDep,
    escalator: EscalatorDep,
) -> dict[str, object]:
    # Recheck ALL fields on write: clients cannot bypass step screening.
    for field in FIELDS:
        crisis = await screen(
            getattr(payload, field),
            request=request,
            user=user,
            engine=engine,
            classifier=classifier,
            escalator=escalator,
        )
        if crisis is not None:
            return {"interrupted": True, "crisis": crisis, "record": None}
    try:
        wrapped, key_id = await UserRepository(session).ensure_data_key(user.id)
        encrypted = {
            f"{field}_encrypted": encrypt_field(
                getattr(payload, field),
                user_id=user.id,
                field_name=cbt_aad(field),
                wrapped_dek=wrapped,
            )
            for field in FIELDS
        }
    except CipherNotConfiguredError as exc:
        raise ApiError(
            503, "encryption_unavailable", "Thought records cannot be saved right now."
        ) from exc
    row = CBTRecord(
        user_id=user.id,
        key_id=key_id,
        emotion=payload.emotion,
        intensity_before=payload.intensity_before,
        intensity_after=payload.intensity_after,
        what_helped=payload.what_helped,
        **encrypted,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"interrupted": False, "crisis": None, "record": view(row, wrapped)}


@router.get("/records")
async def list_records(user: CBTUser, session: SessionDep) -> list[RecordView]:
    rows = (
        (
            await session.execute(
                select(CBTRecord)
                .where(CBTRecord.user_id == user.id)
                .order_by(CBTRecord.created_at.desc(), CBTRecord.id.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [view(row, user.data_key_encrypted) for row in rows]


@router.get("/records/{record_id}")
async def get_record(record_id: uuid.UUID, user: CBTUser, session: SessionDep) -> RecordView:
    row = await session.get(CBTRecord, record_id)
    if row is None or row.user_id != user.id:
        raise ApiError(404, "cbt_not_found", "Thought record not found.")
    return view(row, user.data_key_encrypted)


@router.delete("/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(record_id: uuid.UUID, user: CBTUser, session: SessionDep) -> Response:
    row = await session.get(CBTRecord, record_id)
    if row is None or row.user_id != user.id:
        raise ApiError(404, "cbt_not_found", "Thought record not found.")
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
