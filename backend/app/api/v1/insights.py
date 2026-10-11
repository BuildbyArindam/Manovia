"""Insights endpoints: weekly summary and pattern analysis.

Two routes, both authenticated and scoped to the caller's own mood data:

===============================  =============================
route                            what it does
===============================  =============================
``GET /api/v1/insights/weekly``  Localised weekly summary for the
                                 last 7 days (template-based, no LLM)
``GET /api/v1/insights/patterns`` Rolling averages (7/30 day), factor
                                 associations, time-of-day/weekday
                                 patterns — all with minimum-data
                                 rules and associative wording
===============================  =============================

**Consent.** Reading insights needs ``terms`` and ``privacy`` (same as mood
check-ins). ``ai_disclosure`` is not required: no model is involved, the
summary is built from deterministic templates and the statistical functions in
``app.services.mood.insights``.

**Minimum-data rules.** Every computed pattern respects the data minimums
defined in ``app.services.mood.insights`` (7 days for a trend, 5 per group for
associations). When data is insufficient the response says so explicitly — it
does not invent a pattern.

**Wording.** Factor associations are always associative, never causal:
"on days you tagged poor sleep, your mood was about 0.8 lower on average
(n=9 vs 21)". The weekly summary ends with a standard disclaimer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, require_consent
from app.core.errors import ApiError
from app.db.repos import MoodRepository
from app.models.enums import ConsentKind
from app.models.user import User
from app.services.mood import (
    FactorAssociation,
    RollingWindowResult,
    TimePattern,
    WeeklySummary,
    factor_association,
    factor_associations_to_csv,
    mood_entries_to_csv,
    rolling_average,
    time_patterns,
    time_patterns_to_csv,
    weekly_summary,
)

router = APIRouter(prefix="/insights", tags=["insights"])

InsightsConsentGate = require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY)
ConsentedUser = Annotated[User, Depends(InsightsConsentGate)]

TZ_QUERY = Query(
    default=None,
    max_length=64,
    description=(
        "IANA timezone name used to decide what 'today' means (e.g. Asia/Kolkata). "
        "Omitted or blank means UTC, and the response says so."
    ),
    examples=["Asia/Kolkata"],
)

LOCALE_QUERY = Query(
    default="en",
    max_length=16,
    pattern="^(en|hi|bn)$",
    description="Locale for the weekly summary text (en, hi, bn).",
    examples=["en"],
)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class RollingPointOut(BaseModel):
    day: str  # YYYY-MM-DD
    valence_avg: float | None
    energy_avg: float | None
    count: int


class RollingWindowOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    window_days: int
    points: list[RollingPointOut]
    trend: str
    trend_note: str


class FactorAssociationOut(BaseModel):
    factor: str
    with_factor_avg: float | None
    without_factor_avg: float | None
    with_factor_count: int
    without_factor_count: int
    effect_size: float | None
    ci_lower: float | None
    ci_upper: float | None
    p_value: float | None
    note: str


class TimePatternOut(BaseModel):
    bucket: str
    valence_avg: float | None
    energy_avg: float | None
    count: int
    note: str


class WeeklySummaryOut(BaseModel):
    locale: str
    period_start: str  # YYYY-MM-DD
    period_end: str  # YYYY-MM-DD
    total_check_ins: int
    valence_avg: float | None
    energy_avg: float | None
    trend: str
    top_factors: list[tuple[str, int]]
    text: str


class InsightsPatternsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rolling_7: RollingWindowOut
    rolling_30: RollingWindowOut
    factor_associations: list[FactorAssociationOut]
    hourly_patterns: list[TimePatternOut]
    weekday_patterns: list[TimePatternOut]
    timezone: str
    timezone_fallback_used: bool
    # Minimum-data constants echoed for the client
    min_entries_for_trend: int = 7
    min_entries_per_group: int = 5


class ExportOut(BaseModel):
    """A CSV export bundle."""

    entries_csv: str
    factor_associations_csv: str
    time_patterns_csv: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    """The clock, in one place.

    A single seam so a test can pin "today" without patching ``datetime``
    globally — the same reason :func:`app.api.v1.mood._now` exists. Insights are
    relative to *now*, so a test that seeded a fixed calendar window would be
    testing a different 90 days than the one it wrote.
    """
    return datetime.now(UTC)


def _resolve_zone(name: str | None) -> tuple[str, bool]:
    """Resolve timezone, returning (zone_key, fallback_used)."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if name is None or not name.strip():
        return "UTC", True
    try:
        zone = ZoneInfo(name.strip())
        return getattr(zone, "key", None) or str(zone), False
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ApiError(
            422,
            "invalid_timezone",
            "That timezone is not one this service knows. Send an IANA name such as "
            "Asia/Kolkata, or omit it for UTC.",
        ) from exc


async def _fetch_user_entries(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    days_back: int = 90,  # enough for the 30-day window plus a comfortable buffer
    now: datetime | None = None,
) -> list[tuple[datetime, int, int, list[str], dict[str, object]]]:
    """Fetch the user's mood entries as tuples for the insights functions."""
    since = (now or _now()) - timedelta(days=days_back)
    entries = await MoodRepository(session).list_for_user(user_id, since=since, limit=1000)
    return [
        (
            entry.created_at,
            entry.valence,
            entry.energy,
            list(entry.emotions),
            dict(entry.factors),
        )
        for entry in entries
    ]


def _rolling_to_out(result: RollingWindowResult, zone: str, fallback: bool) -> RollingWindowOut:
    return RollingWindowOut(
        window_days=result.window_days,
        points=[
            RollingPointOut(
                day=p.day.isoformat(),
                valence_avg=p.valence_avg,
                energy_avg=p.energy_avg,
                count=p.count,
            )
            for p in result.points
        ],
        trend=result.trend,
        trend_note=result.trend_note,
    )


def _factor_to_out(f: FactorAssociation) -> FactorAssociationOut:
    return FactorAssociationOut(
        factor=f.factor,
        with_factor_avg=f.with_factor_avg,
        without_factor_avg=f.without_factor_avg,
        with_factor_count=f.with_factor_count,
        without_factor_count=f.without_factor_count,
        effect_size=f.effect_size,
        ci_lower=f.ci_lower,
        ci_upper=f.ci_upper,
        p_value=f.p_value,
        note=f.note,
    )


def _time_to_out(p: TimePattern) -> TimePatternOut:
    return TimePatternOut(
        bucket=p.bucket,
        valence_avg=p.valence_avg,
        energy_avg=p.energy_avg,
        count=p.count,
        note=p.note,
    )


def _weekly_to_out(w: WeeklySummary) -> WeeklySummaryOut:
    return WeeklySummaryOut(
        locale=w.locale,
        period_start=w.period_start.isoformat(),
        period_end=w.period_end.isoformat(),
        total_check_ins=w.total_check_ins,
        valence_avg=w.valence_avg,
        energy_avg=w.energy_avg,
        trend=w.trend,
        top_factors=w.top_factors,
        text=w.text,
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/weekly", response_model=WeeklySummaryOut)
async def get_weekly_summary(
    user: ConsentedUser,
    session: SessionDep,
    tz: str | None = TZ_QUERY,
    locale: str = LOCALE_QUERY,
) -> WeeklySummaryOut:
    """Localised weekly summary for the last 7 days (template-based, no LLM)."""
    zone_key, _ = _resolve_zone(tz)
    now = _now()
    entries = await _fetch_user_entries(session, user.id, now=now)
    summary = weekly_summary(entries, zone=zone_key, locale=locale, now=now)
    return _weekly_to_out(summary)


@router.get("/patterns", response_model=InsightsPatternsOut)
async def get_patterns(
    user: ConsentedUser,
    session: SessionDep,
    tz: str | None = TZ_QUERY,
) -> InsightsPatternsOut:
    """Rolling averages, factor associations, and time patterns with minimum-data rules."""
    zone_key, fallback = _resolve_zone(tz)
    entries = await _fetch_user_entries(session, user.id, now=_now())

    rolling_7 = rolling_average(entries, window_days=7, zone=zone_key)
    rolling_30 = rolling_average(entries, window_days=30, zone=zone_key)
    associations = factor_association(entries, zone=zone_key)
    hourly, weekday = time_patterns(entries, zone=zone_key)

    return InsightsPatternsOut(
        rolling_7=_rolling_to_out(rolling_7, zone_key, fallback),
        rolling_30=_rolling_to_out(rolling_30, zone_key, fallback),
        factor_associations=[_factor_to_out(a) for a in associations],
        hourly_patterns=[_time_to_out(p) for p in hourly],
        weekday_patterns=[_time_to_out(p) for p in weekday],
        timezone=zone_key,
        timezone_fallback_used=fallback,
    )


@router.get("/export", response_model=ExportOut)
async def export_insights(
    user: ConsentedUser,
    session: SessionDep,
    tz: str | None = TZ_QUERY,
) -> ExportOut:
    """Export all insights data as CSV (entries, factor associations, time patterns).

    The entries bundle includes the owner's own note: this is their data leaving
    with them. The two statistical bundles are computed from the same rows *minus*
    the notes — see the comment above ``mood_entries_to_csv``.
    """
    zone_key, _ = _resolve_zone(tz)
    entries = await _fetch_user_entries(session, user.id, now=_now())

    rows = await MoodRepository(session).list_for_user(user.id, limit=1000)
    entries_csv = mood_entries_to_csv(
        [
            (
                row.created_at,
                row.valence,
                row.energy,
                list(row.emotions),
                dict(row.factors),
                MoodRepository.note(row),
            )
            for row in rows
        ],
        zone=zone_key,
    )
    associations = factor_association(entries, zone=zone_key)
    associations_csv = factor_associations_to_csv(associations)
    hourly, weekday = time_patterns(entries, zone=zone_key)
    patterns_csv = time_patterns_to_csv(hourly, weekday)

    return ExportOut(
        entries_csv=entries_csv,
        factor_associations_csv=associations_csv,
        time_patterns_csv=patterns_csv,
    )
