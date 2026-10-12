"""Encrypted journal endpoints: CRUD, paginated listing, and localised writing prompts.

Every route under ``/api/v1/journal`` requires a valid access token and current
``TERMS`` + ``PRIVACY`` consent (:func:`app.api.deps.require_consent`).

Privacy and safety guarantees:

1. **Envelope encryption at rest** (:mod:`app.services.journal.crypto`): ``title``
   and ``body`` are encrypted with the user's random 256-bit AES-GCM data key
   wrapped by ``FIELD_ENCRYPTION_KEY``, using ``(user_id, "title")`` and
   ``(user_id, "body")`` as Associated Data (AAD) so ciphertexts cannot be
   swapped across users or columns.
2. **In-memory emotion/sentiment scoring (opt-out)**: on write (`POST` / `PUT`),
   sentiment (`[-1.0, 1.0]`) and emotion scores (`dict[str, float]`) are
   computed in memory from the plaintext and only numeric values are stored in
   the clear. When the user passes ``store_emotions=False``, both columns are
   stored as ``None``.
3. **Mandatory safety screening (AGENTS.md rules 1, 2, 4, 6)**: every write runs
   the safety ensemble on ``title + "\\n" + body`` before encryption. If the
   safety engine fails, the write fails closed with ``503`` (never silently
   bypassed). At crisis risk, a ``SafetyEvent`` row (metadata only, no text) is
   recorded and verified crisis resources from ``helplines.json`` are returned
   in ``metadata.resources`` and ``crisis``.
4. **No server-side search**: because ``title_encrypted`` and ``body_encrypted``
   are semantic AES-256-GCM ciphertexts with random nonces, substring or
   full-text search in SQL is impossible without decrypting every row on the
   server. Search is therefore performed client-side in browser memory after
   fetching the user's decrypted entries.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Literal, cast

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    EmotionAnalyzerDep,
    EscalatorDep,
    MLClassifierDep,
    RuleEngineDep,
    SessionDep,
    require_consent,
)
from app.api.v1.mood import SAFETY_UNAVAILABLE_MESSAGE, MoodCrisisOut
from app.content.crisis import CrisisResource
from app.content.i18n import resolve_locale
from app.core.config import Settings
from app.core.crypto import CipherNotConfiguredError, DecryptError
from app.core.errors import ApiError
from app.db.repos import JournalRepository, SafetyEventRepository
from app.models.base import as_utc
from app.models.enums import ConsentKind, RiskLevel, SafetyEventSource
from app.models.journal import JournalEntry
from app.models.user import User
from app.services.journal.prompts import (
    get_localised_templates,
    get_rotating_prompts,
)
from app.services.nlp.base import EmotionAnalyzer
from app.services.privacy.store import sentiment_storage_allowed
from app.services.safety.ensemble import SOURCE_RULES, EnsembleDecision
from app.services.safety.escalation import Escalator
from app.services.safety.ml_classifier import SafetyClassifier
from app.services.safety.pipeline import run_ensemble
from app.services.safety.rules import RuleEngine

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/journal", tags=["journal"])

JournalUser = Annotated[User, Depends(require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY))]

JournalTemplateId = Literal["free", "gratitude", "worry_dump"]

_WORD_RE = re.compile(r"\S+")


def _count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


# ---------------------------------------------------------------------------
# Wire schemas
# ---------------------------------------------------------------------------


class JournalWriteRequest(BaseModel):
    """Create or update a journal entry."""

    title: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    template: JournalTemplateId | None = "free"
    prompt_id: str | None = Field(default=None, max_length=64)
    store_emotions: bool = True

    @field_validator("title")
    @classmethod
    def _clean_title(cls, value: str) -> str:
        return value.strip()

    @field_validator("body")
    @classmethod
    def _clean_body(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Journal entry body cannot be empty")
        return trimmed

    @field_validator("prompt_id")
    @classmethod
    def _clean_prompt_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class JournalEntryMetadata(BaseModel):
    """Derived non-textual metadata returned with a journal entry."""

    word_count: int
    store_emotions: bool
    primary_emotion: str | None = None
    risk_level: str = "none"
    stored_risk_level: RiskLevel = RiskLevel.NONE
    resources: list[CrisisResource] = Field(default_factory=list)
    crisis: MoodCrisisOut | None = None


class JournalEntryView(BaseModel):
    """One decrypted journal entry returned to its authenticated owner."""

    id: uuid.UUID
    title: str
    body: str
    key_id: str
    sentiment: float | None = None
    emotion_scores: dict[str, float] | None = None
    template: str | None = None
    prompt_id: str | None = None
    word_count: int
    store_emotions: bool
    created_at: datetime
    updated_at: datetime
    metadata: JournalEntryMetadata
    crisis: MoodCrisisOut | None = None


class JournalListResponse(BaseModel):
    """Paginated list of journal entries, newest first."""

    items: list[JournalEntryView]
    count: int
    total: int
    limit: int
    offset: int
    has_more: bool


class JournalPromptView(BaseModel):
    """One localised writing prompt chip."""

    id: str
    category: str
    template: JournalTemplateId
    text: str
    hint: str


class JournalTemplateView(BaseModel):
    """One localised journal template (`free`, `gratitude`, `worry_dump`)."""

    id: JournalTemplateId
    label: str
    description: str
    scaffold_title: str
    scaffold_body: str


class JournalPromptsResponse(BaseModel):
    """Curated, localised writing prompts and templates for the journal editor."""

    locale: str
    for_date: date
    featured: JournalPromptView
    prompts: list[JournalPromptView]
    templates: list[JournalTemplateView]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _event_source(decision: EnsembleDecision) -> SafetyEventSource:
    return SafetyEventSource.RULES if decision.source == SOURCE_RULES else SafetyEventSource.ML


async def _screen_journal_text(
    text: str,
    *,
    request: Request,
    user: User,
    engine: RuleEngine,
    classifier: SafetyClassifier,
    escalator: Escalator,
    session: AsyncSession,
) -> tuple[str, RiskLevel, MoodCrisisOut | None, list[CrisisResource]]:
    """Run the safety ensemble on journal text; fail closed on any engine error."""
    settings: Settings = request.app.state.settings
    try:
        assessment, decision = await run_ensemble(
            text,
            user.language,
            engine,
            classifier,
            min_confidence=settings.safety_ml_min_confidence,
            crisis_mass_floor=settings.safety_ml_crisis_mass_floor,
            suspicion_floor=settings.safety_ml_suspicion_floor,
        )
        plan = escalator.plan(assessment, region=user.region, locale=user.language)
    except Exception as exc:
        log.error(
            "journal.safety_failed",
            user_id=str(user.id),
            error_type=type(exc).__name__,
        )
        raise ApiError(
            503,
            "safety_unavailable",
            SAFETY_UNAVAILABLE_MESSAGE,
        ) from exc

    stored_risk = plan.assessment.to_stored_level()
    if plan.record_event:
        await SafetyEventRepository(session).record(
            risk_level=stored_risk,
            source=_event_source(decision),
            category=None,
            user_id=user.id,
        )

    if not plan.level.is_crisis:
        return plan.level_code, stored_risk, None, []

    resources = list(plan.resources)
    crisis_card = MoodCrisisOut(
        level=plan.level_code,
        stored_level=plan.stored_level_code,
        source=decision.source,
        matched_categories=[cat.value for cat in plan.assessment.matched_categories],
        rationale_codes=list(plan.assessment.rationale_codes),
        about_someone_else=plan.about_someone_else,
        locale=plan.locale,
        region=plan.region,
        message=plan.message,
        emergency=plan.emergency,
        resources=resources,
    )
    return plan.level_code, stored_risk, crisis_card, resources


def _score_plaintext_emotions(
    text: str,
    *,
    analyzer: EmotionAnalyzer,
    lang: str | None,
    store_emotions: bool,
) -> tuple[float | None, dict[str, float] | None, str | None]:
    """Compute sentiment and emotion scores in memory; discard if disabled."""
    if not store_emotions:
        return None, None, None
    result = analyzer.analyze(text, lang=lang)
    sentiment = round(float(result.valence), 4)
    scores = {str(label): round(float(val), 4) for label, val in result.scores.items()}
    return sentiment, scores, str(result.primary)


def _primary_from_scores(scores: dict[str, float] | None) -> str | None:
    if not scores:
        return None
    return max(scores.items(), key=lambda kv: kv[1])[0]


def _to_view(
    entry: JournalEntry,
    *,
    wrapped_dek: bytes | None = None,
    risk_level: str = "none",
    stored_risk_level: RiskLevel = RiskLevel.NONE,
    crisis: MoodCrisisOut | None = None,
    resources: list[CrisisResource] | None = None,
    primary_emotion: str | None = None,
) -> JournalEntryView:
    try:
        title = JournalRepository.title(entry, wrapped_dek=wrapped_dek)
        body = JournalRepository.body(entry, wrapped_dek=wrapped_dek)
    except DecryptError as exc:
        log.error(
            "journal.decrypt_failed",
            entry_id=str(entry.id),
            user_id=str(entry.user_id),
        )
        raise ApiError(
            500,
            "journal_decrypt_failed",
            "Journal entry could not be decrypted.",
        ) from exc

    word_count = _count_words(f"{title} {body}".strip())
    has_emotions = entry.sentiment is not None or entry.emotion_scores is not None
    resolved_primary = primary_emotion or _primary_from_scores(entry.emotion_scores)
    resource_list = list(resources) if resources is not None else []
    meta = JournalEntryMetadata(
        word_count=word_count,
        store_emotions=has_emotions,
        primary_emotion=resolved_primary,
        risk_level=risk_level,
        stored_risk_level=stored_risk_level,
        resources=resource_list,
        crisis=crisis,
    )
    return JournalEntryView(
        id=entry.id,
        title=title,
        body=body,
        key_id=entry.key_id,
        sentiment=entry.sentiment,
        emotion_scores=entry.emotion_scores,
        template=entry.template,
        prompt_id=entry.prompt_id,
        word_count=word_count,
        store_emotions=has_emotions,
        created_at=as_utc(entry.created_at),
        updated_at=as_utc(entry.updated_at),
        metadata=meta,
        crisis=crisis,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/prompts", response_model=JournalPromptsResponse)
async def list_journal_prompts(
    user: JournalUser,
    lang: Annotated[str | None, Query(max_length=35)] = None,
    template: Annotated[JournalTemplateId | None, Query()] = None,
    for_date: Annotated[date | None, Query()] = None,
    count: Annotated[int, Query(ge=1, le=20)] = 4,
) -> JournalPromptsResponse:
    """Return the rotating curated set of localised writing prompts and templates."""
    requested_locale = lang or user.language or "en"
    resolved_locale, _ = resolve_locale(requested_locale)
    active_date = for_date or datetime.now(UTC).date()

    featured, prompts = get_rotating_prompts(
        locale=resolved_locale,
        for_date=active_date,
        template=template,
        count=count,
    )
    templates = get_localised_templates(resolved_locale)

    return JournalPromptsResponse(
        locale=resolved_locale,
        for_date=active_date,
        featured=JournalPromptView(
            id=featured.id,
            category=featured.category,
            template=cast(JournalTemplateId, featured.template),
            text=featured.text,
            hint=featured.hint,
        ),
        prompts=[
            JournalPromptView(
                id=p.id,
                category=p.category,
                template=cast(JournalTemplateId, p.template),
                text=p.text,
                hint=p.hint,
            )
            for p in prompts
        ],
        templates=[
            JournalTemplateView(
                id=cast(JournalTemplateId, t.id),
                label=t.label,
                description=t.description,
                scaffold_title=t.scaffold_title,
                scaffold_body=t.scaffold_body,
            )
            for t in templates
        ],
    )


@router.post("", response_model=JournalEntryView, status_code=status.HTTP_201_CREATED)
async def create_journal_entry(
    request: Request,
    payload: JournalWriteRequest,
    user: JournalUser,
    session: SessionDep,
    analyzer: EmotionAnalyzerDep,
    engine: RuleEngineDep,
    classifier: MLClassifierDep,
    escalator: EscalatorDep,
) -> JournalEntryView:
    """Screen, score in memory, envelope-encrypt, and persist a journal entry."""
    combined_text = f"{payload.title}\n{payload.body}".strip() if payload.title else payload.body
    risk_level, stored_risk_level, crisis_card, resources = await _screen_journal_text(
        combined_text,
        request=request,
        user=user,
        engine=engine,
        classifier=classifier,
        escalator=escalator,
        session=session,
    )
    # Day 22 privacy floor: a person who turned derived scores off in
    # Settings → Privacy gets no new sentiment, whatever the request says.
    # Existing rows are untouched (retention and deletion own removal).
    store_emotions = payload.store_emotions and await sentiment_storage_allowed(session, user.id)
    sentiment, emotion_scores, primary_emotion = _score_plaintext_emotions(
        combined_text,
        analyzer=analyzer,
        lang=user.language,
        store_emotions=store_emotions,
    )

    repo = JournalRepository(session)
    try:
        entry = await repo.create(
            user_id=user.id,
            title=payload.title,
            body=payload.body,
            sentiment=sentiment,
            emotion_scores=emotion_scores,
            template=payload.template,
            prompt_id=payload.prompt_id,
        )
    except CipherNotConfiguredError as exc:
        raise ApiError(
            503,
            "encryption_unavailable",
            "Journal storage is not available right now (encryption key not configured).",
        ) from exc

    await session.commit()

    word_count = _count_words(combined_text)
    # AGENTS.md rule 5: never log raw user text (title or body).
    log.info(
        "journal.created",
        entry_id=str(entry.id),
        user_id=str(user.id),
        template=entry.template,
        store_emotions=store_emotions,
        word_count=word_count,
        risk_level=risk_level,
    )

    return _to_view(
        entry,
        wrapped_dek=user.data_key_encrypted,
        risk_level=risk_level,
        stored_risk_level=stored_risk_level,
        crisis=crisis_card,
        resources=resources,
        primary_emotion=primary_emotion,
    )


@router.get("", response_model=JournalListResponse)
async def list_journal_entries(
    user: JournalUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    template: Annotated[JournalTemplateId | None, Query()] = None,
) -> JournalListResponse:
    """List the signed-in user's decrypted journal entries with pagination."""
    repo = JournalRepository(session)
    total = await repo.count_for_user(
        user.id,
        since=as_utc(since) if since is not None else None,
        until=as_utc(until) if until is not None else None,
        template=template,
    )
    entries = await repo.list_for_user(
        user.id,
        since=as_utc(since) if since is not None else None,
        until=as_utc(until) if until is not None else None,
        template=template,
        limit=limit,
        offset=offset,
    )
    items = [_to_view(entry, wrapped_dek=user.data_key_encrypted) for entry in entries]
    return JournalListResponse(
        items=items,
        count=len(items),
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(items)) < total,
    )


@router.get("/{entry_id}", response_model=JournalEntryView)
async def get_journal_entry(
    entry_id: uuid.UUID,
    user: JournalUser,
    session: SessionDep,
) -> JournalEntryView:
    """Fetch and decrypt a single journal entry owned by the signed-in user."""
    repo = JournalRepository(session)
    entry = await repo.get(entry_id)
    if entry is None or entry.user_id != user.id:
        raise ApiError(
            404,
            "journal_not_found",
            "Journal entry not found.",
        )
    return _to_view(entry, wrapped_dek=user.data_key_encrypted)


@router.put("/{entry_id}", response_model=JournalEntryView)
async def update_journal_entry(
    entry_id: uuid.UUID,
    request: Request,
    payload: JournalWriteRequest,
    user: JournalUser,
    session: SessionDep,
    analyzer: EmotionAnalyzerDep,
    engine: RuleEngineDep,
    classifier: MLClassifierDep,
    escalator: EscalatorDep,
) -> JournalEntryView:
    """Re-screen, re-score in memory, and re-encrypt an existing journal entry."""
    repo = JournalRepository(session)
    entry = await repo.get(entry_id)
    if entry is None or entry.user_id != user.id:
        raise ApiError(
            404,
            "journal_not_found",
            "Journal entry not found.",
        )

    combined_text = f"{payload.title}\n{payload.body}".strip() if payload.title else payload.body
    risk_level, stored_risk_level, crisis_card, resources = await _screen_journal_text(
        combined_text,
        request=request,
        user=user,
        engine=engine,
        classifier=classifier,
        escalator=escalator,
        session=session,
    )
    store_emotions = payload.store_emotions and await sentiment_storage_allowed(session, user.id)
    sentiment, emotion_scores, primary_emotion = _score_plaintext_emotions(
        combined_text,
        analyzer=analyzer,
        lang=user.language,
        store_emotions=store_emotions,
    )

    try:
        updated = await repo.update(
            entry,
            title=payload.title,
            body=payload.body,
            sentiment=sentiment,
            emotion_scores=emotion_scores,
            template=payload.template,
            prompt_id=payload.prompt_id,
        )
    except CipherNotConfiguredError as exc:
        raise ApiError(
            503,
            "encryption_unavailable",
            "Journal storage is not available right now (encryption key not configured).",
        ) from exc

    await session.commit()

    word_count = _count_words(combined_text)
    log.info(
        "journal.updated",
        entry_id=str(updated.id),
        user_id=str(user.id),
        template=updated.template,
        store_emotions=store_emotions,
        word_count=word_count,
        risk_level=risk_level,
    )

    return _to_view(
        updated,
        wrapped_dek=user.data_key_encrypted,
        risk_level=risk_level,
        stored_risk_level=stored_risk_level,
        crisis=crisis_card,
        resources=resources,
        primary_emotion=primary_emotion,
    )


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_journal_entry(
    entry_id: uuid.UUID,
    user: JournalUser,
    session: SessionDep,
) -> Response:
    """Permanently delete a journal entry owned by the signed-in user."""
    repo = JournalRepository(session)
    entry = await repo.get(entry_id)
    if entry is None or entry.user_id != user.id:
        raise ApiError(
            404,
            "journal_not_found",
            "Journal entry not found.",
        )
    await repo.delete(entry)
    await session.commit()
    log.info("journal.deleted", entry_id=str(entry_id), user_id=str(user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
