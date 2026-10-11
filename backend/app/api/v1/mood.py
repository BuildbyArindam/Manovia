"""Mood check-ins: record one, read a range, delete one, and read the streak.

Four routes, all authenticated and all scoped to the caller's own rows:

======================================================  =========================
route                                                   what it does
======================================================  =========================
``POST   /api/v1/mood``                                 one check-in; screens the
                                                        optional note and returns
                                                        crisis help if it needs it
``GET    /api/v1/mood?from=&to=&tz=``                   check-ins in a local range
``GET    /api/v1/mood/streak?tz=``                      the forgiving streak, in
                                                        the caller's timezone
``DELETE /api/v1/mood/{id}``                            remove one check-in
======================================================  =========================

**A check-in is never a reason to make somebody feel worse.** Three decisions
follow from that, and all three are visible in this file:

* *Nothing in a check-in is free text except the note.* Valence and energy are
  bounded integers; emotions and factors come from the closed vocabularies in
  :mod:`app.services.mood.taxonomy`. Anything else is a ``422`` naming the field,
  because quietly dropping a tag would store a different mood than the one
  somebody told us.
* *The note is screened before it is stored, and the check-in is saved either
  way.* When the note reads HIGH or worse the response carries the crisis
  metadata — the pre-written message, the region's helplines, the emergency
  number — and a metadata-only ``safety_events`` row, while the check-in itself
  is still recorded. Refusing to save somebody's honest check-in because it was
  frightening would punish them for telling the truth. If the *safety engine
  itself* fails, the request fails closed with ``503`` and nothing is written:
  free text is never stored unscreened.
* *The streak is counted in the caller's timezone* (``tz``, an IANA name).
  Missing one day is bridged; missing two is a reset with "welcome back" numbers
  and no penalty. See :mod:`app.services.mood.streak` for the rules.

**Ownership.** Another person's entry is a ``404 mood_entry_not_found``,
identical to one that does not exist. ``GET /mood`` only ever returns the
caller's rows, and there is no id-addressed read, so an id cannot be probed at
all.

**Consent.** Reading and writing a check-in needs ``terms`` and ``privacy``.
``ai_disclosure`` is deliberately *not* required here: no model is in this path —
the suggested action is a fixed mapping and the safety check is a local rules
engine. Asking somebody to agree about AI before they may say how they feel
would be a lie about what happens to their answer.

**Privacy of the note.** It is returned to its owner (it is their data) and
stored encrypted; it is never logged. The streak query reads timestamps only, so
a note is not even loaded for the calculation that cannot use it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    EscalatorDep,
    MLClassifierDep,
    RuleEngineDep,
    SessionDep,
    require_consent,
)
from app.content.crisis import CrisisResource
from app.content.i18n import RenderedTemplate
from app.core.config import Settings
from app.core.errors import ApiError
from app.db.repos import MoodRepository, SafetyEventRepository
from app.models.enums import ConsentKind, SafetyEventSource
from app.models.mood import MoodEntry
from app.models.user import User
from app.services.mood import (
    EMOTION_TAGS,
    MAX_EMOTIONS,
    MAX_FACTORS,
    MOOD_FACTORS,
    NOTE_MAX_CHARS,
    SCALE_MAX,
    SCALE_MIN,
    StreakState,
    StreakSummary,
    UnknownTimezoneError,
    compute_streak,
    factors_document,
    factors_from_document,
    load_timezone,
    local_date,
    local_today,
    unknown_emotions,
    unknown_factors,
)
from app.services.safety.ensemble import SOURCE_RULES, EnsembleDecision
from app.services.safety.pipeline import run_ensemble

router = APIRouter(prefix="/mood", tags=["mood"])

#: Reading and writing a check-in needs the data agreements, not the AI one.
MoodConsentGate = require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY)
ConsentedUser = Annotated[User, Depends(MoodConsentGate)]

#: How many check-ins one range query may return. A year of daily check-ins is
#: 365 rows, so the default is generous and the ceiling is still bounded.
DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 500

#: Shown when the safety engine cannot run. The same words the chat pipeline uses
#: for the same failure, so a person meets one explanation, not two.
SAFETY_UNAVAILABLE_MESSAGE = (
    "We could not check this safely just now, so nothing was saved. Please try again in a moment."
)

_log = structlog.get_logger()

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


class MoodCheckInIn(BaseModel):
    """One check-in: two scales, up to three emotions, up to five factors, a note."""

    valence: int = Field(
        ge=SCALE_MIN, le=SCALE_MAX, description="How you feel, 1 (very low) to 5 (very good)."
    )
    energy: int = Field(
        ge=SCALE_MIN, le=SCALE_MAX, description="How much energy you have, 1 (empty) to 5 (plenty)."
    )
    emotions: list[str] = Field(
        default_factory=list,
        max_length=MAX_EMOTIONS,
        description=f"Up to {MAX_EMOTIONS} tags from the emotion taxonomy.",
    )
    factors: list[str] = Field(
        default_factory=list,
        max_length=MAX_FACTORS,
        description=f"Up to {MAX_FACTORS} context tags (sleep, work, people, …).",
    )
    note: str | None = Field(default=None, max_length=NOTE_MAX_CHARS)
    #: The phone's own timezone, so "today" means the caller's today.
    tz: str | None = Field(default=None, max_length=64)
    #: UI language for the crisis message, if one is needed (``en``, ``hi``, ``bn``).
    locale: str | None = Field(default=None, max_length=16)

    @field_validator("emotions")
    @classmethod
    def _known_emotions(cls, value: list[str]) -> list[str]:
        cleaned = _dedupe(value)
        unknown = unknown_emotions(cleaned)
        if unknown:
            raise ValueError(
                f"unknown emotion tag(s): {', '.join(unknown)}; allowed: {', '.join(EMOTION_TAGS)}"
            )
        return cleaned

    @field_validator("factors")
    @classmethod
    def _known_factors(cls, value: list[str]) -> list[str]:
        cleaned = _dedupe(value)
        unknown = unknown_factors(cleaned)
        if unknown:
            raise ValueError(
                f"unknown factor tag(s): {', '.join(unknown)}; allowed: {', '.join(MOOD_FACTORS)}"
            )
        return cleaned

    @field_validator("note")
    @classmethod
    def _clean_note(cls, value: str | None) -> str | None:
        """A blank note is no note at all.

        Whitespace is not a thought. Storing ``" "`` would mean encrypting
        nothing, screening nothing, and showing an empty box in the history.
        """
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


def _dedupe(values: list[str]) -> list[str]:
    """Lower-case tags, drop repeats, keep the order the person chose them in.

    Order is kept because the first tag somebody picks is usually the one that
    mattered most, and folding a repeat is kinder than a 422 for a double tap.
    """
    seen: dict[str, None] = {}
    for value in values:
        cleaned = value.strip().casefold()
        if cleaned:
            seen.setdefault(cleaned, None)
    return list(seen)


class MoodEntryOut(BaseModel):
    """One stored check-in, as its owner sees it."""

    id: uuid.UUID
    created_at: datetime
    #: The calendar day this check-in belongs to in the requested timezone.
    local_date: date
    valence: int
    energy: int
    emotions: list[str]
    factors: list[str]
    #: The owner's own note, decrypted for them. Stored encrypted (see the model).
    note: str | None
    timezone: str


class MoodCrisisOut(BaseModel):
    """Help offered alongside a check-in whose note read at HIGH or worse.

    Mirrors the ``POST /crisis/assess`` shape so a client renders one crisis
    contract in two places instead of learning a second one. Everything here is
    pre-written or owned by the helpline content file; nothing is derived from
    the note.
    """

    #: ``high`` or ``imminent``.
    level: str
    #: The four-tier database value (both of the above map to ``crisis``).
    stored_level: str
    #: Which detector earned the level: ``rules``, ``ml`` or ``ensemble.uncertain``.
    source: str
    matched_categories: list[str]
    #: Stable pattern ids — never fragments of the note.
    rationale_codes: list[str]
    #: True when the note was about somebody else, so the card can be framed for
    #: a supporter rather than for the person who wrote it.
    about_someone_else: bool
    locale: str
    region: str
    #: The pre-written, already-substituted message.
    message: RenderedTemplate | None = None
    emergency: CrisisResource | None = None
    resources: list[CrisisResource] = Field(default_factory=list)
    #: Always true, and asserted by the tests: the check-in was saved anyway.
    check_in_saved: bool = True


class StreakOut(BaseModel):
    """The forgiving streak, in the caller's timezone. Numbers, no judgement."""

    state: StreakState
    timezone: str
    timezone_fallback_used: bool
    today: date
    current_streak: int = Field(description="Check-in days in the live run.")
    longest_streak: int
    checked_in_today: bool
    last_check_in: date | None = None
    days_since_last_check_in: int | None = None
    total_check_ins: int
    freezes_used: int = Field(description="Missed days bridged inside the live run.")
    freeze_available: bool = Field(
        description="True when a future missed day would still be bridged."
    )


class MoodCheckInOut(BaseModel):
    """The stored check-in, the streak it produced, and any help it needs."""

    entry: MoodEntryOut
    streak: StreakOut
    crisis: MoodCrisisOut | None = None


class MoodListOut(BaseModel):
    """A range of check-ins, newest first, plus the range that was applied."""

    model_config = ConfigDict(populate_by_name=True)

    entries: list[MoodEntryOut]
    count: int
    #: The inclusive start actually queried (``from`` on the wire).
    start: datetime | None = Field(default=None, alias="from")
    #: The exclusive end actually queried (``to`` on the wire, inclusive of the
    #: named day because it is resolved to the next local midnight).
    end: datetime | None = Field(default=None, alias="to")
    timezone: str
    timezone_fallback_used: bool
    limit: int


class MoodDeleteOut(BaseModel):
    """What was removed, plus the streak that is left. Deleting is never punished."""

    deleted: bool
    id: uuid.UUID
    remaining_streak: StreakOut


# --------------------------------------------------------------------------- #
# Helpers                                                                       #
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    """The clock, in one place.

    A single seam so a test (or a future client-supplied "as of") can pin "today"
    without patching ``datetime`` globally, which is the usual way timezone
    arithmetic stops being reproducible.
    """
    return datetime.now(UTC)


def _zone_key(zone: ZoneInfo) -> str:
    return getattr(zone, "key", None) or str(zone)


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


def _entry_out(entry: MoodEntry, *, zone: ZoneInfo) -> MoodEntryOut:
    """Serialise a row, decrypting the owner's note for the owner."""
    return MoodEntryOut(
        id=entry.id,
        created_at=entry.created_at,
        local_date=local_date(entry.created_at, zone),
        valence=entry.valence,
        energy=entry.energy,
        emotions=list(entry.emotions),
        factors=factors_from_document(entry.factors),
        note=MoodRepository.note(entry),
        timezone=_zone_key(zone),
    )


def _streak_out(summary: StreakSummary) -> StreakOut:
    return StreakOut(
        state=summary.state,
        timezone=summary.timezone,
        timezone_fallback_used=summary.timezone_fallback_used,
        today=summary.today,
        current_streak=summary.current_streak,
        longest_streak=summary.longest_streak,
        checked_in_today=summary.checked_in_today,
        last_check_in=summary.last_check_in,
        days_since_last_check_in=summary.days_since_last_check_in,
        total_check_ins=summary.total_check_ins,
        freezes_used=summary.freezes_used,
        freeze_available=summary.freeze_available,
    )


async def _streak_for(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    zone: ZoneInfo,
    fallback_used: bool,
    now: datetime,
) -> StreakSummary:
    """Compute the streak from the user's check-in timestamps only."""
    instants: Sequence[datetime] = await MoodRepository(session).list_instants(user_id)
    return compute_streak(
        check_in_instants=instants,
        today=local_today(now, zone),
        timezone=zone,
        timezone_fallback_used=fallback_used,
    )


def _event_source(decision: EnsembleDecision) -> SafetyEventSource:
    """Map an ensemble decision onto the audit vocabulary (as chat does)."""
    return SafetyEventSource.RULES if decision.source == SOURCE_RULES else SafetyEventSource.ML


def _parse_bound(raw: str, *, zone: ZoneInfo, end: bool) -> datetime:
    """Parse one ``from``/``to`` bound into a UTC instant.

    Two accepted forms, and only two, because ambiguity here silently changes
    which check-ins a person sees:

    * ``2026-03-08`` — a whole local day. ``end=False`` is that day's local
      midnight, ``end=True`` the next day's local midnight, resolved with the
      zone's DST rules (that day may be 23 or 25 hours long).
    * an ISO-8601 timestamp, with or without an offset. A naive timestamp is read
      as UTC, which is what the rest of the API stores.
    """
    text = raw.strip()
    day: date | None
    try:
        day = date.fromisoformat(text)
    except ValueError:
        day = None
    if day is not None:
        start = datetime.combine(day, time.min, tzinfo=zone)
        instant = start + timedelta(days=1) if end else start
        return instant.astimezone(UTC)

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError(
            422,
            "invalid_range",
            "Ranges must be ISO dates (2026-03-08) or ISO timestamps (2026-03-08T00:00:00Z).",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# --------------------------------------------------------------------------- #
# Routes                                                                        #
# --------------------------------------------------------------------------- #


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MoodCheckInOut)
async def create_check_in(
    request: Request,
    body: MoodCheckInIn,
    user: ConsentedUser,
    session: SessionDep,
    engine: RuleEngineDep,
    classifier: MLClassifierDep,
    escalator: EscalatorDep,
) -> MoodCheckInOut:
    """Record one check-in; screen the optional note before storing it.

    The order is deliberate: **screen, then store**. A note that needs help is
    still stored, together with a metadata-only audit row. A note we could not
    screen is not stored at all, because the alternative is silently keeping
    somebody's worst sentence with nobody told to look at it.
    """
    settings: Settings = request.app.state.settings
    zone, fallback_used = _resolve_zone(body.tz)
    now = _now()
    session_repo = MoodRepository(session)

    crisis: MoodCrisisOut | None = None
    if body.note is not None:
        try:
            assessment, decision = await run_ensemble(
                body.note,
                user.language,
                engine,
                classifier,
                min_confidence=settings.safety_ml_min_confidence,
                crisis_mass_floor=settings.safety_ml_crisis_mass_floor,
                suspicion_floor=settings.safety_ml_suspicion_floor,
            )
        except Exception as exc:
            # Fail closed: no verdict means nothing about this note is stored.
            _log.error("mood_safety_unavailable", error_type=type(exc).__name__)
            raise ApiError(503, "safety_unavailable", SAFETY_UNAVAILABLE_MESSAGE) from exc

        plan = escalator.plan(assessment, region=user.region, locale=body.locale)
        if plan.level.is_crisis:
            crisis = MoodCrisisOut(
                level=plan.level_code,
                stored_level=plan.stored_level_code,
                source=decision.source,
                matched_categories=[
                    category.value for category in plan.assessment.matched_categories
                ],
                rationale_codes=list(plan.assessment.rationale_codes),
                about_someone_else=plan.about_someone_else,
                locale=plan.locale,
                region=plan.region,
                message=plan.message,
                emergency=plan.emergency,
                resources=list(plan.resources),
            )
            if plan.record_event:
                await SafetyEventRepository(session).record(
                    risk_level=plan.assessment.to_stored_level(),
                    source=_event_source(decision),
                    category=None,
                    user_id=user.id,
                )

    stored = await session_repo.record(
        user_id=user.id,
        valence=body.valence,
        energy=body.energy,
        emotions=body.emotions,
        factors=factors_document(body.factors),
        note=body.note,
        # The same clock reading that decided "today" a moment ago, so the streak
        # returned here always includes the check-in it just wrote.
        created_at=now,
    )
    await session.commit()

    streak = await _streak_for(session, user.id, zone=zone, fallback_used=fallback_used, now=now)
    _log.info(
        "mood_check_in_recorded",
        valence=body.valence,
        energy=body.energy,
        emotions=len(body.emotions),
        factors=len(body.factors),
        # A boolean and a length: never the note itself (AGENTS.md safety rule 5).
        has_note=body.note is not None,
        note_chars=len(body.note) if body.note else 0,
        crisis=bool(crisis),
    )
    return MoodCheckInOut(
        entry=_entry_out(stored, zone=zone), streak=_streak_out(streak), crisis=crisis
    )


@router.get("", response_model=MoodListOut)
async def list_check_ins(
    user: ConsentedUser,
    session: SessionDep,
    from_: Annotated[str | None, Query(alias="from", max_length=40)] = None,
    to: Annotated[str | None, Query(max_length=40)] = None,
    tz: str | None = TZ_QUERY,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
) -> MoodListOut:
    """The caller's check-ins, newest first, optionally bounded by a local range.

    With no ``from``/``to`` the newest ``limit`` check-ins come back. A date-only
    bound is read as a whole local day in ``tz`` (and ``to`` includes that day); a
    timestamp is read as an exact instant.
    """
    zone, fallback_used = _resolve_zone(tz)
    since = until = None
    if from_ is not None:
        since = _parse_bound(from_, zone=zone, end=False)
    if to is not None:
        until = _parse_bound(to, zone=zone, end=True)
    if since is not None and until is not None and since > until:
        raise ApiError(422, "invalid_range", "The start of the range is after its end.")

    entries = await MoodRepository(session).list_for_user(
        user.id, since=since, until=until, limit=limit
    )
    return MoodListOut(
        entries=[_entry_out(entry, zone=zone) for entry in entries],
        count=len(entries),
        start=since,
        end=until,
        timezone=_zone_key(zone),
        timezone_fallback_used=fallback_used,
        limit=limit,
    )


@router.get("/streak", response_model=StreakOut)
async def read_streak(
    user: ConsentedUser,
    session: SessionDep,
    tz: str | None = TZ_QUERY,
) -> StreakOut:
    """The streak, counted in the caller's timezone with forgiving rules.

    Read the module docstring of :mod:`app.services.mood.streak` before changing
    anything here. The short version: today is still open, one missed day in
    seven is bridged, and coming back after a gap is "welcome back" — never a
    number that was taken away.
    """
    zone, fallback_used = _resolve_zone(tz)
    summary = await _streak_for(
        session, user.id, zone=zone, fallback_used=fallback_used, now=_now()
    )
    return _streak_out(summary)


@router.delete("/{entry_id}", response_model=MoodDeleteOut)
async def delete_check_in(
    entry_id: uuid.UUID,
    user: ConsentedUser,
    session: SessionDep,
    tz: str | None = TZ_QUERY,
) -> MoodDeleteOut:
    """Delete one of the caller's check-ins.

    Somebody else's id and a made-up id get the same 404, so this route cannot be
    used to discover whether an entry exists.
    """
    repo = MoodRepository(session)
    entry = await repo.get(entry_id)
    if entry is None or entry.user_id != user.id:
        raise ApiError(404, "mood_entry_not_found", "That check-in was not found.")

    zone, fallback_used = _resolve_zone(tz)
    now = _now()
    await repo.delete(entry)
    await session.commit()

    streak = await _streak_for(session, user.id, zone=zone, fallback_used=fallback_used, now=now)
    _log.info("mood_check_in_deleted")
    return MoodDeleteOut(deleted=True, id=entry_id, remaining_streak=_streak_out(streak))
