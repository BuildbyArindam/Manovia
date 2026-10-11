"""Self-help screenings: official PHQ-9 and GAD-7, scored and stored.

Three surfaces, one contract each:

``GET /assessments`` and ``GET /assessments/{instrument}``
    The instruments themselves — official item wording, the four response
    options, the severity bands, the plain-language band copy and the
    ``provenance`` block that records where the wording came from and what its
    terms allow. Public on purpose, like ``/crisis/resources``: the PHQ-9 is a
    published screener anybody may reproduce, it contains no personal data, and
    a clinician who wants to check what a person was asked should not need an
    account. The *results* are not public.

``POST /assessments/{instrument}/submit``
    Score one completed questionnaire and store it. Every item is required;
    a missing answer is a ``422``, never a silently smaller total. When PHQ-9
    item 9 is answered above zero the response carries the crisis flow —
    resources and the pre-written message — **regardless of the total**, and the
    total is still returned so nobody loses the result they just completed.

``GET /assessments/{instrument}/history`` (and ``…/history/{result_id}``)
    The person's own runs, newest first, plus the band cutpoints the trend chart
    needs. Another user's result is a ``404``, never a ``403``.

Two deliberate absences:

* **No ``safety_events`` row for item 9.** Every other crisis surface writes a
  metadata-only audit row because a *detector* fired. Here a person answered a
  questionnaire item about themselves; the answer is already stored, with the
  instrument and the timestamp, in ``assessment_results.answers``. Writing a row
  whose ``source`` claims a rules or ML detection would be inventing evidence.
* **No block on repeat submissions.** Screening more often than every fortnight
  is answered with a sentence, not a refusal — the frequency suggestion is
  advice about how to read a trend, and turning it into a wall would stop
  somebody using the tool when they most want to.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, Field, JsonValue

from app.api.deps import EscalatorDep, SessionDep, require_consent
from app.content.crisis import CrisisResource
from app.content.i18n import RenderedTemplate
from app.core.errors import ApiError
from app.db.repos import AssessmentRepository
from app.models.assessment import AssessmentResult
from app.models.base import as_utc
from app.models.enums import AssessmentBand, AssessmentInstrument, ConsentKind
from app.models.user import User
from app.services.assessments import (
    Band,
    IncompleteAssessmentError,
    InstrumentDefinition,
    InvalidAnswerError,
    UnknownItemError,
    get_instruments,
    score,
)
from app.services.safety.base import AssessmentContext, RiskAssessment, RiskCategory, RiskLevel
from app.services.safety.escalation import EscalationPlan, Escalator

router = APIRouter(prefix="/assessments", tags=["assessments"])
_log = structlog.get_logger(__name__)

#: Assessments contain no free text, but the routes still require the same two
#: agreements every other personal-data surface does (see ADR 0004).
AssessmentsUser = Annotated[User, Depends(require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY))]

#: What the item-9 flow reports as its origin. The escalation policy needs a
#: "who decided" code; nothing detected this, the person reported it themselves,
#: and saying so keeps the field honest (see the module docstring on safety
#: events).
CRISIS_SOURCE_SELF_REPORT = "self_report"


def _now() -> datetime:
    """The clock, in one place, so tests can pin it (the Day 19 convention)."""
    return datetime.now(UTC)


def _definition(instrument: AssessmentInstrument) -> InstrumentDefinition:
    """The shipped definition, or a 404 the client can act on."""
    try:
        return get_instruments()[instrument]
    except KeyError as exc:  # pragma: no cover - the enum makes this unreachable
        raise ApiError(404, "instrument_not_found", "That questionnaire does not exist.") from exc


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #


class BandSummary(BaseModel):
    """A band as the chart and the history list need it."""

    id: AssessmentBand
    label: str
    min: int
    max: int


class BandView(BandSummary):
    """A band plus the sentences the result page shows."""

    explanation: str
    what_helps: list[str]
    when_to_seek_help: str


class ItemView(BaseModel):
    """One question, in official wording."""

    id: str
    position: int
    text: str


class ResponseOptionView(BaseModel):
    value: int
    label: str
    detail: str | None = None


class DefinitionView(BaseModel):
    """One instrument, complete enough for a client to render it unaided."""

    instrument: AssessmentInstrument
    name: str
    short_name: str
    measures: str
    intro: str
    timeframe_days: int
    timeframe_label: str
    response_options: list[ResponseOptionView]
    items: list[ItemView]
    bands: list[BandSummary]
    total_min: int
    total_max: int
    screening_not_diagnosis: str
    how_to_read: str
    #: Present only for instruments with an item-level safety rule (PHQ-9).
    safety: dict[str, str] | None = None
    cadence_notice: str
    recommended_interval_days: int
    provenance: dict[str, Any]


class InstrumentSummary(BaseModel):
    """What the picker shows before anybody answers anything."""

    instrument: AssessmentInstrument
    name: str
    short_name: str
    measures: str
    intro: str
    item_count: int
    timeframe_label: str
    total_max: int


class AnswerView(BaseModel):
    item_id: str
    value: int


class AssessmentResultView(BaseModel):
    """A stored run, with the band copy it was scored against."""

    id: uuid.UUID
    instrument: AssessmentInstrument
    created_at: datetime
    answers: list[AnswerView]
    total: int
    max_total: int
    band: AssessmentBand
    band_label: str
    band_min: int
    band_max: int
    band_explanation: str
    what_helps: list[str]
    when_to_seek_help: str
    screening_not_diagnosis: str
    how_to_read: str


class HistoryEntry(BaseModel):
    """One row of the trend: everything the chart and the table need."""

    id: uuid.UUID
    created_at: datetime
    total: int
    max_total: int
    band: AssessmentBand
    band_label: str


class CadenceView(BaseModel):
    """The gentle re-screening suggestion. Advice, never a block."""

    recommended_interval_days: int
    is_first: bool
    days_since_previous: int | None = None
    #: True when two weeks or more have passed (or this is the first run).
    due: bool
    #: One of the instrument's three pre-written sentences.
    notice: str


class CrisisView(BaseModel):
    """The item-9 flow: pre-written message plus the region's helplines.

    Mirrors the shape the chat, mood and CBT surfaces return, so a client has
    one crisis contract rather than four.
    """

    #: Which question produced this, e.g. ``phq9_9``.
    item_id: str
    #: ``high`` — the same tier a message with suicidal content gets.
    level: str
    #: The four-tier stored value (``crisis``).
    stored_level: str
    #: ``self_report``: nothing detected this, the person answered a question.
    source: str
    matched_categories: list[str]
    #: Stable codes — never a fragment of an answer.
    rationale_codes: list[str]
    locale: str
    region: str
    #: Why this appeared, in plain language, written for this context.
    explanation: str
    encouragement: str
    paired_with_score: str
    message: RenderedTemplate | None = None
    emergency: CrisisResource | None = None
    resources: list[CrisisResource] = Field(default_factory=list)
    #: Always true: the run is stored even when this flow fires.
    result_saved: bool = True


class SubmitResponse(BaseModel):
    """The stored result, the item-9 flow when it applies, and the cadence notice."""

    result: AssessmentResultView
    crisis: CrisisView | None = None
    cadence: CadenceView


class HistoryResponse(BaseModel):
    """A person's own runs for one instrument, newest first."""

    instrument: AssessmentInstrument
    short_name: str
    total_max: int
    bands: list[BandSummary]
    entries: list[HistoryEntry]
    count: int
    limit: int
    offset: int
    cadence: CadenceView


# --------------------------------------------------------------------------- #
# Building views
# --------------------------------------------------------------------------- #


def _provenance_document(definition: InstrumentDefinition) -> dict[str, Any]:
    """The provenance block as JSON, with the availability statement intact."""
    return definition.provenance.model_dump(mode="json")


def _definition_view(definition: InstrumentDefinition) -> DefinitionView:
    return DefinitionView(
        instrument=definition.instrument,
        name=definition.name,
        short_name=definition.short_name,
        measures=definition.measures,
        intro=definition.intro,
        timeframe_days=definition.timeframe_days,
        timeframe_label=definition.timeframe_label,
        response_options=[
            ResponseOptionView(value=option.value, label=option.label, detail=option.detail)
            for option in definition.response_options
        ],
        items=[
            ItemView(id=item.id, position=item.position, text=item.text)
            for item in definition.items
        ],
        bands=[
            BandSummary(id=band.id, label=band.label, min=band.min, max=band.max)
            for band in sorted(definition.scoring.bands, key=lambda band: band.min)
        ],
        total_min=definition.scoring.total_min,
        total_max=definition.scoring.total_max,
        screening_not_diagnosis=definition.disclaimers.screening_not_diagnosis,
        how_to_read=definition.disclaimers.how_to_read,
        safety=(
            {
                "item_id": definition.safety.item_id,
                "rule": definition.safety.rule,
                "level": definition.safety.level,
                "explanation": definition.safety.explanation,
                "encouragement": definition.safety.encouragement,
                "paired_with_score": definition.safety.paired_with_score,
            }
            if definition.safety is not None
            else None
        ),
        cadence_notice=definition.cadence.recent_notice,
        recommended_interval_days=definition.cadence.recommended_interval_days,
        provenance=_provenance_document(definition),
    )


def _result_view(row: AssessmentResult) -> AssessmentResultView:
    """A stored row plus the band copy, from the definition (not from the row).

    The band *label* and its explanation are read from the shipped content, so
    re-reading an old result shows today's reviewed copy rather than a snapshot
    of copy that may since have been corrected. The score and the band id are the
    stored facts and never move.
    """
    definition = _definition(row.instrument)
    band = next(b for b in definition.scoring.bands if b.id == row.band)
    missing = [item.id for item in definition.items if item.id not in row.answers]
    if missing:
        # A row that does not match the instrument it claims to be is a bug, not
        # something to paper over: rendering an unanswered question as "Not at
        # all" would invent an answer (see docs/adr/0020).
        raise ApiError(
            500,
            "stored_result_unreadable",
            "That stored result does not match the questionnaire and cannot be shown.",
        )
    return AssessmentResultView(
        id=row.id,
        instrument=row.instrument,
        created_at=as_utc(row.created_at),
        answers=[
            AnswerView(item_id=item.id, value=int(row.answers[item.id]))
            for item in definition.items
        ],
        total=row.total,
        max_total=definition.scoring.total_max,
        band=row.band,
        band_label=band.label,
        band_min=band.min,
        band_max=band.max,
        band_explanation=band.explanation,
        what_helps=list(band.what_helps),
        when_to_seek_help=band.when_to_seek_help,
        screening_not_diagnosis=definition.disclaimers.screening_not_diagnosis,
        how_to_read=definition.disclaimers.how_to_read,
    )


def _history_entry(row: AssessmentResult) -> HistoryEntry:
    definition = _definition(row.instrument)
    band = next(b for b in definition.scoring.bands if b.id == row.band)
    return HistoryEntry(
        id=row.id,
        created_at=as_utc(row.created_at),
        total=row.total,
        max_total=definition.scoring.total_max,
        band=row.band,
        band_label=band.label,
    )


def _cadence(
    definition: InstrumentDefinition, previous: AssessmentResult | None, now: datetime
) -> CadenceView:
    """How long since the last run, and the sentence that goes with it."""
    interval = definition.cadence.recommended_interval_days
    if previous is None:
        return CadenceView(
            recommended_interval_days=interval,
            is_first=True,
            days_since_previous=None,
            due=True,
            notice=definition.cadence.first_notice,
        )
    elapsed = max(0, (now - as_utc(previous.created_at)).days)
    due = elapsed >= interval
    return CadenceView(
        recommended_interval_days=interval,
        is_first=False,
        days_since_previous=elapsed,
        due=due,
        notice=definition.cadence.due_notice if due else definition.cadence.recent_notice,
    )


def _crisis_view(definition: InstrumentDefinition, plan: EscalationPlan) -> CrisisView:
    """The item-9 flow, assembled from the escalation plan and content copy."""
    assert definition.safety is not None  # guarded by the caller
    return CrisisView(
        item_id=definition.safety.item_id,
        level=plan.level_code,
        stored_level=plan.stored_level_code,
        source=CRISIS_SOURCE_SELF_REPORT,
        matched_categories=[category.value for category in plan.assessment.matched_categories],
        rationale_codes=list(plan.assessment.rationale_codes),
        locale=plan.locale,
        region=plan.region,
        explanation=definition.safety.explanation,
        encouragement=definition.safety.encouragement,
        paired_with_score=definition.safety.paired_with_score,
        message=plan.message,
        emergency=plan.emergency,
        resources=list(plan.resources),
    )


def _plan_for_item9(
    definition: InstrumentDefinition, *, user: User, escalator: Escalator
) -> EscalationPlan:
    """Build the HIGH plan the scored answers have earned.

    The assessment is constructed rather than detected, and it says so: the
    rationale code names the questionnaire item, and the context records that
    this is first-person and self-reported. No text ever enters this path — an
    answer is an integer.
    """
    assert definition.safety is not None
    assessment = RiskAssessment(
        level=RiskLevel.HIGH,
        matched_categories=(RiskCategory.SUICIDAL_IDEATION,),
        rationale_codes=(definition.safety.rationale_code,),
        context=AssessmentContext(first_person=True, language=user.language),
    )
    return escalator.plan(assessment, region=user.region, locale=user.language)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("", response_model=list[InstrumentSummary])
async def list_assessments() -> list[InstrumentSummary]:
    """The instruments this deployment ships, in a stable order."""
    definitions = get_instruments()
    return [
        InstrumentSummary(
            instrument=definition.instrument,
            name=definition.name,
            short_name=definition.short_name,
            measures=definition.measures,
            intro=definition.intro,
            item_count=len(definition.items),
            timeframe_label=definition.timeframe_label,
            total_max=definition.scoring.total_max,
        )
        for _, definition in sorted(definitions.items(), key=lambda pair: pair[0].value)
    ]


@router.get("/{instrument}", response_model=DefinitionView)
async def get_assessment(
    instrument: Annotated[AssessmentInstrument, Path(description="phq9 or gad7")],
) -> DefinitionView:
    """One instrument: official wording, options, bands and provenance."""
    return _definition_view(_definition(instrument))


class SubmitRequest(BaseModel):
    """A completed questionnaire: one answer for every item, nothing else."""

    #: ``{"phq9_1": 0, …, "phq9_9": 1}``. Typed as JSON rather than ``int`` so a
    #: client sending ``true`` or ``"2"`` gets the scorer's explicit refusal
    #: ("Answer for phq9_1 must be one of 0, 1, 2 or 3") instead of pydantic
    #: quietly coercing it to 1 — see the scorer's docstring on ``bool``. The item
    #: ids and the completeness of the set are checked there too.
    answers: dict[str, JsonValue] = Field(min_length=1, description="item id → answer")
    #: UI language for the crisis message when item 9 fires (``en``, ``hi``, ``bn``).
    locale: str | None = Field(default=None, max_length=16)


@router.post(
    "/{instrument}/submit", response_model=SubmitResponse, status_code=status.HTTP_201_CREATED
)
async def submit_assessment(
    payload: SubmitRequest,
    instrument: Annotated[AssessmentInstrument, Path(description="phq9 or gad7")],
    request: Request,
    user: AssessmentsUser,
    session: SessionDep,
    escalator: EscalatorDep,
) -> SubmitResponse:
    """Score, store, and — if item 9 was answered — offer help immediately."""
    definition = _definition(instrument)
    try:
        scored = score(instrument, payload.answers)
    except UnknownItemError as exc:
        raise ApiError(
            422,
            "unknown_answers",
            "That submission includes answers that do not belong to this questionnaire.",
        ) from exc
    except InvalidAnswerError as exc:
        raise ApiError(
            422,
            "invalid_answer",
            f"Answer for {exc.item_id} must be one of 0, 1, 2 or 3.",
        ) from exc
    except IncompleteAssessmentError as exc:
        raise ApiError(
            422,
            "incomplete_assessment",
            "Please answer every question before submitting. Missing: "
            + ", ".join(exc.missing)
            + ".",
        ) from exc

    now = _now()
    repo = AssessmentRepository(session)
    previous_rows = await repo.list_for_user(user.id, instrument=instrument, limit=1)
    previous = previous_rows[0] if previous_rows else None

    crisis: CrisisView | None = None
    if scored.flagged_for_crisis and definition.safety is not None:
        crisis = _crisis_view(
            definition, _plan_for_item9(definition, user=user, escalator=escalator)
        )

    row = await repo.record(
        user_id=user.id,
        instrument=instrument,
        answers=scored.answers_document,
        total=scored.total,
        band=scored.band_id,
    )
    # The API clock decides the ordering the history reads, not the database's
    # default, so a pinned clock in a test is the clock the rows carry.
    row.created_at = now
    await session.commit()
    await session.refresh(row)

    _log.info(
        "assessment_submitted",
        instrument=instrument.value,
        total=scored.total,
        band=scored.band_id.value,
        # A boolean and an id: never an answer value, never any text.
        item9_flagged=scored.flagged_for_crisis,
        crisis=bool(crisis),
    )

    return SubmitResponse(
        result=_result_view(row),
        crisis=crisis,
        cadence=_cadence(definition, previous, now),
    )


@router.get("/{instrument}/history", response_model=HistoryResponse)
async def assessment_history(
    user: AssessmentsUser,
    session: SessionDep,
    instrument: Annotated[AssessmentInstrument, Path(description="phq9 or gad7")],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> HistoryResponse:
    """The caller's own runs for one instrument, newest first."""
    definition = _definition(instrument)
    repo = AssessmentRepository(session)
    rows = await repo.list_for_user(user.id, instrument=instrument, limit=limit, offset=offset)
    previous = rows[0] if rows else None
    return HistoryResponse(
        instrument=instrument,
        short_name=definition.short_name,
        total_max=definition.scoring.total_max,
        bands=[
            BandSummary(id=band.id, label=band.label, min=band.min, max=band.max)
            for band in sorted(definition.scoring.bands, key=lambda band: band.min)
        ],
        entries=[_history_entry(row) for row in rows],
        count=len(rows),
        limit=limit,
        offset=offset,
        cadence=_cadence(definition, previous, _now()),
    )


@router.get("/{instrument}/history/{result_id}", response_model=AssessmentResultView)
async def assessment_result(
    user: AssessmentsUser,
    session: SessionDep,
    instrument: Annotated[AssessmentInstrument, Path(description="phq9 or gad7")],
    result_id: uuid.UUID,
) -> AssessmentResultView:
    """One stored run, for the printable summary of an older result."""
    row = await AssessmentRepository(session).get(result_id)
    if row is None or row.user_id != user.id or row.instrument != instrument:
        raise ApiError(404, "assessment_not_found", "That screening result was not found.")
    return _result_view(row)


def band_copy(instrument: AssessmentInstrument, band: AssessmentBand) -> Band:
    """The reviewed copy for one band. Used by tests and any future export."""
    definition = _definition(instrument)
    return next(b for b in definition.scoring.bands if b.id == band)
