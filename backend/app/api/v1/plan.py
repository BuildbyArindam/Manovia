"""Daily plan: ``GET /plan/today`` plus ``done`` / ``skip`` for each item.

The planner itself is pure and lives in :mod:`app.services.plan.daily_plan`;
this module is the thin, consented API around it.

======================================================  =========================
route                                                   what it does
======================================================  =========================
``GET  /api/v1/plan/today?tz=``                         today's plan (at most 3
                                                        suggestions, each with a
                                                        "why"), generated once per
                                                        person per local day
``POST /api/v1/plan/{id}/done``                         mark one suggestion done
``POST /api/v1/plan/{id}/skip``                         skip it; no reason asked
======================================================  =========================

**Generation is idempotent per local day.** The first ``GET`` of a day runs the
rules and stores what they produced; every later ``GET`` of the same local day
returns the same rows with their statuses intact. A plan is something a person
reacts to, not something that reshuffles under them on every refresh.

**Ownership.** Another person's plan item is ``404 plan_item_not_found``,
identical to one that does not exist.

**Consent.** ``terms`` + ``privacy``, exactly like mood: no model is in this
path, so the AI disclosure is not required to see a plan.

**Privacy of inputs.** The context handed to the planner is metadata only:
valence numbers, closed-vocabulary emotion tags, completion days, and the count
of thought records whose ``what_helped`` is empty. Encrypted free text is never
decrypted for planning — the queries project the columns the rules need and
nothing else. The PHQ-9 influence is a single boolean (item 9 positive within
the window), and its only permitted effect is the support-options suggestion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import SessionDep, require_consent
from app.core.errors import ApiError
from app.db.repos import AssessmentRepository, MoodRepository, PlanRepository, SettingsRepository
from app.db.repos.exercises import ExerciseRepository
from app.models.assessment import AssessmentResult
from app.models.base import as_utc
from app.models.cbt import CBTRecord
from app.models.enums import AssessmentInstrument, ConsentKind, PlanItemKind, PlanItemStatus
from app.models.plan import PlanItem
from app.models.user import User
from app.models.user_settings import UserSettings
from app.services.assessments.instruments import get_instruments
from app.services.mood import UnknownTimezoneError, load_timezone
from app.services.plan.daily_plan import (
    LOOKBACK_DAYS,
    MoodSignal,
    PlannerContext,
    PlanPreferences,
    build_daily_plan,
    is_quiet_hours,
    parse_quiet_time,
)
from app.services.plan.daily_plan import (
    InvalidQuietTimeError as PlannerInvalidQuietTimeError,
)

router = APIRouter(prefix="/plan", tags=["plan"])

#: Reading and writing a plan needs the data agreements, not the AI one.
PlanConsentGate = require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY)
PlanUser = Annotated[User, Depends(PlanConsentGate)]

#: How long a positive PHQ-9 item-9 answer keeps shaping the plan. Two weeks
#: matches the instrument's own "last two weeks" timeframe.
ITEM9_WINDOW_DAYS: int = 14

TZ_QUERY = Query(
    default=None,
    max_length=64,
    description=(
        "IANA timezone name used to decide what 'today' means (e.g. Asia/Kolkata). "
        "Omitted or blank means UTC, and the response says so."
    ),
    examples=["Asia/Kolkata"],
)


# --------------------------------------------------------------------------- #
# Schemas                                                                       #
# --------------------------------------------------------------------------- #


class PlanItemOut(BaseModel):
    """One suggestion, as its owner sees it."""

    id: uuid.UUID
    kind: str
    #: Exercise id for exercise suggestions; ``None`` for the other kinds.
    ref: str | None = None
    title: str
    #: The "why this?" explanation. Always present.
    why: str
    status: str
    created_at: datetime


class PlanTodayOut(BaseModel):
    """Today's plan: at most three items, never a nag."""

    #: The local calendar day this plan belongs to.
    date: date
    timezone: str
    timezone_default_used: bool
    #: True while the person's quiet hours cover this request: routine
    #: suggestions are suppressed (and the list is empty because of it).
    quiet_hours_active: bool
    #: True when the plan shows only the support-options suggestion after a
    #: positive PHQ-9 item-9 answer.
    support_only: bool
    items: list[PlanItemOut]
    count: int


def _item_out(item: PlanItem) -> PlanItemOut:
    return PlanItemOut(
        id=item.id,
        kind=item.kind.value,
        ref=item.ref,
        title=item.title,
        why=item.why,
        status=item.status.value,
        created_at=item.created_at,
    )


def _resolve_zone(name: str | None) -> tuple[ZoneInfo, bool]:
    """Timezone for this request, or a 422 that names what went wrong."""
    try:
        return load_timezone(name)
    except UnknownTimezoneError as exc:
        raise ApiError(
            422,
            "invalid_timezone",
            "That timezone is not one this service knows. Send an IANA name such as "
            "Asia/Kolkata, or omit it for UTC.",
        ) from exc


# --------------------------------------------------------------------------- #
# Signal gathering: metadata only, never decrypted free text                   #
# --------------------------------------------------------------------------- #


def _preferences(row: UserSettings | None) -> PlanPreferences:
    """Map a settings row to planner preferences; ``None`` means defaults."""
    if row is None:
        return PlanPreferences()
    try:
        start = parse_quiet_time(row.quiet_start)
        end = parse_quiet_time(row.quiet_end)
    except PlannerInvalidQuietTimeError:
        # A bound that cannot parse degrades to the planner's defaults rather
        # than failing somebody's plan. Writes are validated, so this is a
        # belt-and-braces path, not a normal one.
        start = None
        end = None
    return PlanPreferences(
        preferred_exercise_tags=tuple(row.preferred_exercise_tags or ()),
        quiet_hours_enabled=row.quiet_hours_enabled,
        quiet_start=start,
        quiet_end=end,
    )


def _item9_flagged(results: list[AssessmentResult], *, cutoff: datetime) -> bool:
    """True when the most recent PHQ-9 inside the window has item 9 above zero.

    The crisis item id comes from the instrument's own ``safety`` block, so the
    planner and the scoring module can never disagree about which item it is.
    """
    if not results:
        return False
    latest = results[0]  # list_for_user orders newest first
    if as_utc(latest.created_at) < cutoff:
        return False
    safety = get_instruments()[AssessmentInstrument.PHQ9].safety
    if safety is None:
        return False
    value = latest.answers.get(safety.item_id, 0)
    return isinstance(value, int) and value > 0


async def _gather_context(
    user: User, session: SessionDep, *, now_utc: datetime, zone: ZoneInfo
) -> PlannerContext:
    """Everything the planner may know about this person, and nothing more."""
    window_start = now_utc - timedelta(days=LOOKBACK_DAYS)
    mood_repo = MoodRepository(session)

    # The newest check-in overall (existence + recency), and the window rows
    # the rules read. Both project numbers and tags, never the encrypted note.
    latest_rows = await mood_repo.list_for_user(user.id, limit=1)
    window_rows = await mood_repo.list_for_user(user.id, since=window_start, limit=1000)
    check_ins = tuple(
        MoodSignal(
            local_date=as_utc(row.created_at).astimezone(zone).date(),
            valence=row.valence,
            emotions=tuple(row.emotions or ()),
        )
        for row in window_rows
    )
    last_check_in_at = as_utc(latest_rows[0].created_at) if latest_rows else None

    completions = await ExerciseRepository(session).list_for_user(user.id, limit=200)
    recent = [entry for entry in completions if as_utc(entry.created_at) >= window_start]
    exercise_days = tuple({as_utc(entry.created_at).astimezone(zone).date() for entry in recent})
    exercise_ids = tuple(entry.exercise_id for entry in recent)

    # Thought records: created_at and what_helped only. No encrypted field is
    # loaded, so an "unfinished" record is visible without ever being read.
    cbt_rows = (
        await session.execute(
            select(CBTRecord.created_at, CBTRecord.what_helped)
            .where(CBTRecord.user_id == user.id, CBTRecord.created_at >= window_start)
            .order_by(CBTRecord.created_at)
        )
    ).all()
    open_thought_records = sum(1 for row in cbt_rows if not (row.what_helped or []))

    phq9 = list(
        await AssessmentRepository(session).list_for_user(
            user.id, instrument=AssessmentInstrument.PHQ9, limit=1
        )
    )
    settings_row = await SettingsRepository(session).get(user.id)

    return PlannerContext(
        now=now_utc.astimezone(zone),
        check_ins=check_ins,
        last_check_in_at=last_check_in_at,
        exercise_days=exercise_days,
        exercise_ids_recent=exercise_ids,
        open_thought_records=open_thought_records,
        phq9_item9_flagged=_item9_flagged(phq9, cutoff=now_utc - timedelta(days=ITEM9_WINDOW_DAYS)),
        preferences=_preferences(settings_row),
    )


# --------------------------------------------------------------------------- #
# Routes                                                                        #
# --------------------------------------------------------------------------- #


@router.get("/today", response_model=PlanTodayOut)
async def plan_today(
    user: PlanUser, session: SessionDep, tz: str | None = TZ_QUERY
) -> PlanTodayOut:
    """Today's plan. Generated once per person per local day."""
    zone, tz_default_used = _resolve_zone(tz)
    now_utc = datetime.now(UTC)
    now_local = now_utc.astimezone(zone)
    today = now_local.date()
    repository = PlanRepository(session)

    existing = await repository.list_for_day(user.id, today)
    if existing:
        # Already planned today: report the rows and the current quiet state.
        settings_row = await SettingsRepository(session).get(user.id)
        quiet_hours_active = is_quiet_hours(now_local, _preferences(settings_row))
        support_only = any(item.kind == PlanItemKind.SUPPORT for item in existing)
    else:
        context = await _gather_context(user, session, now_utc=now_utc, zone=zone)
        plan = build_daily_plan(context)
        quiet_hours_active = plan.quiet_hours_active
        support_only = plan.support_only
        for suggestion in plan.items:
            await repository.create(
                user_id=user.id,
                plan_date=today,
                kind=suggestion.kind,
                title=suggestion.title,
                why=suggestion.why,
                ref=suggestion.ref,
            )
        await session.commit()
        existing = await repository.list_for_day(user.id, today)

    return PlanTodayOut(
        date=today,
        timezone=getattr(zone, "key", None) or str(zone),
        timezone_default_used=tz_default_used,
        quiet_hours_active=quiet_hours_active,
        support_only=support_only,
        items=[_item_out(item) for item in existing],
        count=len(existing),
    )


async def _load_owned_item(item_id: uuid.UUID, user: User, session: SessionDep) -> PlanItem:
    item = await PlanRepository(session).get(item_id)
    if item is None or item.user_id != user.id:
        raise ApiError(404, "plan_item_not_found", "Plan item not found.")
    return item


async def _set_status(
    item_id: uuid.UUID, user: User, session: SessionDep, status: PlanItemStatus
) -> PlanItemOut:
    item = await _load_owned_item(item_id, user, session)
    item = await PlanRepository(session).set_status(item, status)
    await session.commit()
    await session.refresh(item)
    return _item_out(item)


@router.post("/{item_id}/done", response_model=PlanItemOut)
async def plan_item_done(item_id: uuid.UUID, user: PlanUser, session: SessionDep) -> PlanItemOut:
    """Mark one suggestion done."""
    return await _set_status(item_id, user, session, PlanItemStatus.DONE)


@router.post("/{item_id}/skip", response_model=PlanItemOut)
async def plan_item_skip(item_id: uuid.UUID, user: PlanUser, session: SessionDep) -> PlanItemOut:
    """Skip one suggestion. No reason is asked for, and none is stored."""
    return await _set_status(item_id, user, session, PlanItemStatus.SKIPPED)
