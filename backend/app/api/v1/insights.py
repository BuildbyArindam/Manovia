"""Insights: what somebody's own check-ins can honestly be said to show.

Two routes, both authenticated, both scoped to the caller's own rows, both
read-only:

=============================================  ================================
route                                          what it does
=============================================  ================================
``GET /api/v1/insights/weekly?tz&locale&weeks`` natural weeks with their means,
                                               a week-over-week trend and a
                                               localised summary
``GET /api/v1/insights/patterns?tz&locale&days`` rolling averages, a trend, tag
                                               associations, time-of-day and
                                               weekday patterns, a localised
                                               summary and the "about" explainer
=============================================  ================================

Three rules shape this file, and they are the reason it is boring:

* **Nothing is decided here.** The arithmetic, the minimum-data rules, the
  resampling and the sentences all live in :mod:`app.services.mood.insights` and
  :mod:`app.content.insights`; this module reads rows, calls pure functions and
  serialises. An endpoint that quietly loosens a threshold would be invisible in
  a diff of the statistics module, so it is not allowed to have an opinion.
* **The note is never loaded.** The projection is ``MoodRepository.insights_rows``
  — timestamp, two scales, two tag lists. No code path here can decrypt a
  private sentence, so none can leak one into a summary, a log line or a CSV.
* **Empty is an answer, not an error.** A person with three check-ins gets
  ``200`` with ``enough_data: false``, per-section holdback codes and a localised
  sentence saying there is not enough data yet. A ``404`` or an empty list would
  both read as "nothing is wrong with you" — a claim this service cannot make.

**Consent.** ``terms`` + ``privacy``, exactly like the check-ins themselves, and
deliberately not ``ai_disclosure``: no model runs in this path. Averages of a
person's own numbers are arithmetic, and describing them as AI would misdescribe
what happens to their data.

**Locale.** ``locale`` selects the sentence templates (``en``, ``hi``, ``bn``);
an unknown tag falls back to English and says so with ``locale_fallback_used``.
The user's account language is *not* the default here on purpose: the insights
surface is read in the UI's language, which the account row does not track.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, require_consent
from app.content.insights import (
    InsightsStrings,
    load_insights,
    resolve_insight_locale,
)
from app.core.errors import ApiError
from app.db.repos import MoodInsightRow, MoodRepository
from app.models.enums import ConsentKind
from app.models.user import User
from app.services.mood import (
    DEFAULT_WEEKS,
    MAX_WEEKS,
    MAX_WINDOW_DAYS,
    MIN_EFFECT_POINTS,
    MIN_ENTRIES_FOR_TREND,
    MIN_GROUP_N,
    SCALE_MAX,
    CheckIn,
    FactorAssociation,
    GroupBucket,
    GroupPattern,
    InsightsReport,
    MeasureTrend,
    RollingAverage,
    Trend,
    TrendWindow,
    UnknownTimezoneError,
    WeekBucket,
    WeekCount,
    WeeklyReport,
    build_insights,
    build_weekly,
    factor_sentence,
    load_timezone,
    pattern_sentence,
    pattern_sentences,
    weekly_sentences,
)

router = APIRouter(prefix="/insights", tags=["insights"])

#: Reading an insight needs the data agreements, not the AI one.
InsightsConsentGate = require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY)
ConsentedUser = Annotated[User, Depends(InsightsConsentGate)]

#: How far back ``/patterns`` looks by default. Long enough for a 30-day rolling
#: average to have a full window and a few weeks of history either side, short
#: enough that the resampling stays inside a request.
DEFAULT_WINDOW_DAYS = 90

#: Digits kept in the payload. Three is more than any sentence uses (one) and
#: less than the seventeen a float division produces, so a client can show the
#: number it was given without re-rounding it into something else.
PAYLOAD_DIGITS = 3

_log = structlog.get_logger()

TZ_QUERY = Query(
    default=None,
    max_length=64,
    description=(
        "IANA timezone name used to decide which local day a check-in belongs to "
        "(e.g. Asia/Kolkata). Omitted or blank means UTC, and the response says so."
    ),
    examples=["Asia/Kolkata"],
)

LOCALE_QUERY = Query(
    default=None,
    max_length=16,
    description="Language for the generated sentences (en, hi, bn). Unknown means English.",
    examples=["en"],
)


# --------------------------------------------------------------------------- #
# Schemas                                                                       #
# --------------------------------------------------------------------------- #


class RollingPointOut(BaseModel):
    """One day of a rolling-average series."""

    day: date
    average: float | None = None
    entries: int
    enough_data: bool


class RollingOut(BaseModel):
    """One rolling average, with the series a chart draws."""

    measure: str
    #: Localised bare-noun label for an axis or a table header.
    label: str
    window_days: int
    start: date
    end: date
    entries: int
    average: float | None = None
    enough_data: bool
    holdback: str | None = None
    series: list[RollingPointOut] = Field(default_factory=list)


class TrendWindowOut(BaseModel):
    """One side of a trend comparison."""

    start: date
    end: date
    entries: int
    valence_mean: float | None = None
    energy_mean: float | None = None


class MeasureTrendOut(BaseModel):
    """The direction of one measure and everything that decision rests on."""

    measure: str
    label: str
    direction: str
    #: Localised short label for the direction, for a badge or a table cell.
    direction_label: str
    change: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    recent_mean: float | None = None
    previous_mean: float | None = None
    recent_entries: int
    previous_entries: int
    holdback: str | None = None


class TrendOut(BaseModel):
    """Both measures over the same pair of windows."""

    window_days: int
    recent: TrendWindowOut
    previous: TrendWindowOut
    measures: list[MeasureTrendOut]
    enough_data: bool
    holdback: str | None = None


class AssociationOut(BaseModel):
    """Days with one tag, compared with days without it."""

    factor: str
    #: Localised tag label.
    label: str
    measure: str
    measure_label: str
    tagged_n: int
    untagged_n: int
    tagged_mean: float | None = None
    untagged_mean: float | None = None
    difference: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    cohens_d: float | None = None
    family_alpha: float
    reported: bool
    holdback: str | None = None
    #: The associative sentence, already localised, or ``None`` when the
    #: comparison is not reported. A client renders this verbatim: it is the
    #: reviewed copy, not a hint.
    sentence: str | None = None


class FactorReportOut(BaseModel):
    """Every tag comparison, plus the correction it was judged under."""

    comparisons: int
    family_alpha: float
    #: The minimum each side needs before a comparison may exist at all.
    min_group_n: int = MIN_GROUP_N
    #: The smallest difference worth reporting, in points on the 1-5 scale.
    min_effect_points: float
    associations: list[AssociationOut]


class GroupBucketOut(BaseModel):
    """One bucket of a grouping."""

    id: str
    #: Localised bare-noun label ("Morning", "Monday").
    label: str
    entries: int
    valence_mean: float | None = None
    energy_mean: float | None = None
    enough_data: bool


class GroupPatternOut(BaseModel):
    """A time-of-day or weekday grouping and what it may be said to show."""

    kind: str
    measure: str
    measure_label: str
    buckets: list[GroupBucketOut]
    spread: float | None = None
    lowest_id: str | None = None
    highest_id: str | None = None
    p_value: float | None = None
    family_alpha: float
    reported: bool
    holdback: str | None = None
    #: The localised sentence, or the localised reason there is not one.
    sentence: str


class AboutOut(BaseModel):
    """The explainer: what was done, what it cannot say, and what it is not."""

    title: str
    #: The sentence that says these are associations and not a cause.
    not_causal: str
    limitations: list[str]
    methods: list[str]
    disclaimer: str
    locale: str
    locale_fallback_used: bool


class WeekCountOut(BaseModel):
    """One tag and how often it appeared in a week."""

    id: str
    label: str
    count: int


class WeekOut(BaseModel):
    """One natural (Monday-Sunday) week."""

    start: date
    end: date
    #: Localised spelling of ``start``, for a heading.
    label: str
    entries: int
    days_with_check_in: int
    valence_mean: float | None = None
    energy_mean: float | None = None
    factors: list[WeekCountOut] = Field(default_factory=list)
    emotions: list[WeekCountOut] = Field(default_factory=list)
    enough_data: bool


class InsightsMeta(BaseModel):
    """What was looked at, so a reader can judge the answer before reading it."""

    generated_at: datetime
    today: date
    timezone: str
    timezone_fallback_used: bool
    locale: str
    locale_fallback_used: bool
    #: Check-ins in the window these numbers were made from.
    considered_check_ins: int
    #: Check-ins this account has in total (the window may be shorter).
    total_check_ins: int
    #: ``considered_check_ins >= MIN_ENTRIES_FOR_TREND``.
    enough_data: bool
    #: The rules that produced this response, echoed rather than assumed.
    min_entries_for_trend: int = MIN_ENTRIES_FOR_TREND
    scale_max: int = SCALE_MAX


class WeeklyInsightsOut(InsightsMeta):
    """``GET /api/v1/insights/weekly``."""

    weeks: int
    weekly: list[WeekOut]
    trend: TrendOut
    rolling: list[RollingOut]
    #: The localised summary, in reading order.
    summary: list[str]
    about: AboutOut


class PatternsOut(InsightsMeta):
    """``GET /api/v1/insights/patterns``."""

    window_days: int
    rolling: list[RollingOut]
    trend: TrendOut
    factors: FactorReportOut
    time_of_day: GroupPatternOut
    weekday: GroupPatternOut
    #: The localised summary, in reading order.
    summary: list[str]
    about: AboutOut


# --------------------------------------------------------------------------- #
# Seams and helpers                                                             #
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    """The clock, in one place (the same seam ``app.api.v1.mood`` uses).

    Pinning it in a test is what makes "today" a fact of the run rather than a
    race with the calendar, and it is why the streak and the insights can never
    disagree about which day it is.
    """
    return datetime.now(UTC)


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


def _round(value: float | None) -> float | None:
    """Trim a float to payload precision; ``None`` stays ``None``."""
    return None if value is None else round(value, PAYLOAD_DIGITS)


def _round_pair(pair: tuple[float, float] | None) -> tuple[float | None, float | None]:
    if pair is None:
        return None, None
    return round(pair[0], PAYLOAD_DIGITS), round(pair[1], PAYLOAD_DIGITS)


def _check_ins(rows: Sequence[MoodInsightRow]) -> list[CheckIn]:
    """Turn the projection into the value objects the statistics take."""
    return [
        CheckIn(
            at=row.created_at,
            valence=row.valence,
            energy=row.energy,
            factors=tuple(row.factors.keys()),
            emotions=tuple(row.emotions),
        )
        for row in rows
    ]


def _about(strings: InsightsStrings, *, fallback_used: bool) -> AboutOut:
    return AboutOut(
        title=strings.string("about.title"),
        not_causal=strings.string("about.not_causal"),
        limitations=list(strings.list_of("about.limitations")),
        methods=list(strings.list_of("about.methods")),
        disclaimer=strings.string("about.disclaimer"),
        locale=strings.locale,
        locale_fallback_used=fallback_used,
    )


def _rolling_out(item: RollingAverage, *, strings: InsightsStrings, series: bool) -> RollingOut:
    return RollingOut(
        measure=item.measure.value,
        label=strings.string(f"label.measure.{item.measure.value}"),
        window_days=item.window_days,
        start=item.start,
        end=item.end,
        entries=item.entries,
        average=_round(item.average),
        enough_data=item.enough_data,
        holdback=None if item.holdback is None else item.holdback.value,
        series=[
            RollingPointOut(
                day=point.day,
                average=_round(point.average),
                entries=point.entries,
                enough_data=point.enough_data,
            )
            for point in (item.series if series else ())
        ],
    )


def _trend_out(trend_value: Trend, *, strings: InsightsStrings) -> TrendOut:
    return TrendOut(
        window_days=trend_value.window_days,
        recent=_trend_window_out(trend_value.recent),
        previous=_trend_window_out(trend_value.previous),
        measures=[_measure_trend_out(item, strings=strings) for item in trend_value.measures],
        enough_data=trend_value.enough_data,
        holdback=None if trend_value.holdback is None else trend_value.holdback.value,
    )


def _trend_window_out(window: TrendWindow) -> TrendWindowOut:
    return TrendWindowOut(
        start=window.start,
        end=window.end,
        entries=window.entries,
        valence_mean=_round(window.valence_mean),
        energy_mean=_round(window.energy_mean),
    )


def _measure_trend_out(item: MeasureTrend, *, strings: InsightsStrings) -> MeasureTrendOut:
    ci_low, ci_high = _round_pair(item.ci)
    return MeasureTrendOut(
        measure=item.measure.value,
        label=strings.string(f"label.measure.{item.measure.value}"),
        direction=item.direction.value,
        direction_label=strings.string(f"label.trend.{item.direction.value}"),
        change=_round(item.change),
        ci_low=ci_low,
        ci_high=ci_high,
        recent_mean=_round(item.recent_mean),
        previous_mean=_round(item.previous_mean),
        recent_entries=item.recent_entries,
        previous_entries=item.previous_entries,
        holdback=None if item.holdback is None else item.holdback.value,
    )


def _association_out(item: FactorAssociation, *, strings: InsightsStrings) -> AssociationOut:
    ci_low, ci_high = _round_pair(item.ci)
    return AssociationOut(
        factor=item.factor,
        label=strings.string(f"factor.{item.factor}"),
        measure=item.measure.value,
        measure_label=strings.string(f"label.measure.{item.measure.value}"),
        tagged_n=item.tagged_n,
        untagged_n=item.untagged_n,
        tagged_mean=_round(item.tagged_mean),
        untagged_mean=_round(item.untagged_mean),
        difference=_round(item.difference),
        ci_low=ci_low,
        ci_high=ci_high,
        p_value=item.p_value,
        cohens_d=_round(item.cohens_d),
        family_alpha=item.family_alpha,
        reported=item.reported,
        holdback=None if item.holdback is None else item.holdback.value,
        # A sentence only exists for a reported association: rendering one for a
        # held-back comparison would hand a client the words to overstate it.
        sentence=factor_sentence(item, strings=strings) if item.reported else None,
    )


def _bucket_out(bucket: GroupBucket, *, kind: str, strings: InsightsStrings) -> GroupBucketOut:
    return GroupBucketOut(
        id=bucket.id,
        label=strings.string(f"label.{kind}.{bucket.id}"),
        entries=bucket.entries,
        valence_mean=_round(bucket.valence_mean),
        energy_mean=_round(bucket.energy_mean),
        enough_data=bucket.enough_data,
    )


def _pattern_out(item: GroupPattern, *, strings: InsightsStrings) -> GroupPatternOut:
    return GroupPatternOut(
        kind=item.kind.value,
        measure=item.measure.value,
        measure_label=strings.string(f"label.measure.{item.measure.value}"),
        buckets=[
            _bucket_out(bucket, kind=item.kind.value, strings=strings) for bucket in item.buckets
        ],
        spread=_round(item.spread),
        lowest_id=item.lowest.id if item.reported and item.lowest else None,
        highest_id=item.highest.id if item.reported and item.highest else None,
        p_value=item.p_value,
        family_alpha=item.family_alpha,
        reported=item.reported,
        holdback=None if item.holdback is None else item.holdback.value,
        sentence=pattern_sentence(item, strings=strings),
    )


def _week_out(bucket: WeekBucket, *, strings: InsightsStrings) -> WeekOut:
    return WeekOut(
        start=bucket.start,
        end=bucket.end,
        label=strings.localise_date(bucket.start),
        entries=bucket.entries,
        days_with_check_in=bucket.days_with_check_in,
        valence_mean=_round(bucket.valence_mean),
        energy_mean=_round(bucket.energy_mean),
        factors=[_count_out(item, prefix="factor", strings=strings) for item in bucket.factors],
        emotions=[_count_out(item, prefix="emotion", strings=strings) for item in bucket.emotions],
        enough_data=bucket.enough_data,
    )


def _count_out(item: WeekCount, *, prefix: str, strings: InsightsStrings) -> WeekCountOut:
    """One tag count. Emotions have no locale entry, so they are returned as ids.

    The nine emotion labels are the analyser's taxonomy; translating them is the
    frontend's job (it already has the vocabulary in ``features/mood/taxonomy``),
    and inventing a second translation here would be a place for the two to
    drift.
    """
    label_id = f"{prefix}.{item.id}"
    known = label_id in strings.strings
    return WeekCountOut(
        id=item.id,
        label=strings.string(label_id) if known else item.id,
        count=item.count,
    )


async def _load(
    session: AsyncSession, user_id: uuid.UUID, *, since: datetime
) -> tuple[list[CheckIn], int]:
    """Read the insight projection and the account's total check-in count."""
    repository = MoodRepository(session)
    rows = await repository.insights_rows(user_id, since=since)
    total = await repository.count_for_user(user_id)
    return _check_ins(rows), total


def _meta(
    *,
    now: datetime,
    today: date,
    zone: ZoneInfo,
    timezone_fallback_used: bool,
    strings: InsightsStrings,
    locale_fallback_used: bool,
    considered: int,
    total: int,
) -> dict[str, Any]:
    """The fields every insights response starts with."""
    return {
        "generated_at": now,
        "today": today,
        "timezone": getattr(zone, "key", None) or str(zone),
        "timezone_fallback_used": timezone_fallback_used,
        "locale": strings.locale,
        "locale_fallback_used": locale_fallback_used,
        "considered_check_ins": considered,
        "total_check_ins": total,
        "enough_data": considered >= MIN_ENTRIES_FOR_TREND,
    }


# --------------------------------------------------------------------------- #
# Routes                                                                        #
# --------------------------------------------------------------------------- #


@router.get("/weekly", response_model=WeeklyInsightsOut)
async def read_weekly(
    user: ConsentedUser,
    session: SessionDep,
    tz: str | None = TZ_QUERY,
    locale: str | None = LOCALE_QUERY,
    weeks: Annotated[int, Query(ge=1, le=MAX_WEEKS)] = DEFAULT_WEEKS,
) -> WeeklyInsightsOut:
    """The last ``weeks`` natural weeks, a week-over-week trend, and a summary.

    The summary is generated from templates in ``app/content/insights/``: no
    model runs, and no free text from a check-in can reach it. With fewer than
    :data:`~app.services.mood.MIN_ENTRIES_FOR_TREND` check-ins in the window the
    response still arrives, with ``enough_data: false`` and one sentence saying
    there is not enough data yet.
    """
    zone, timezone_fallback_used = _resolve_zone(tz)
    resolved_locale, locale_fallback_used = resolve_insight_locale(locale or user.language)
    strings = load_insights(resolved_locale)
    now = _now()
    today = now.astimezone(zone).date()

    since = datetime.combine(
        today - timedelta(days=7 * weeks + 13), datetime.min.time(), tzinfo=zone
    ).astimezone(UTC)
    check_ins, total = await _load(session, user.id, since=since)

    report: WeeklyReport = build_weekly(
        check_ins,
        zone=zone,
        timezone_fallback_used=timezone_fallback_used,
        today=today,
        weeks=weeks,
        window_days=7 * weeks,
        total_check_ins=total,
    )

    _log.info(
        "insights_weekly_read",
        weeks=weeks,
        considered=report.considered_check_ins,
        enough_data=report.enough_data,
        locale=strings.locale,
    )
    return WeeklyInsightsOut(
        **_meta(
            now=now,
            today=today,
            zone=zone,
            timezone_fallback_used=timezone_fallback_used,
            strings=strings,
            locale_fallback_used=locale_fallback_used,
            considered=report.considered_check_ins,
            total=report.total_check_ins,
        ),
        weeks=weeks,
        weekly=[_week_out(bucket, strings=strings) for bucket in report.weekly],
        trend=_trend_out(report.trend, strings=strings),
        # The weekly surface carries the rolling averages without their series:
        # a week panel does not draw a 56-point line, and the payload stays small.
        rolling=[_rolling_out(item, strings=strings, series=False) for item in report.rolling],
        summary=weekly_sentences(report, strings=strings),
        about=_about(strings, fallback_used=locale_fallback_used),
    )


@router.get("/patterns", response_model=PatternsOut)
async def read_patterns(
    user: ConsentedUser,
    session: SessionDep,
    tz: str | None = TZ_QUERY,
    locale: str | None = LOCALE_QUERY,
    days: Annotated[int, Query(ge=7, le=MAX_WINDOW_DAYS)] = DEFAULT_WINDOW_DAYS,
) -> PatternsOut:
    """Rolling averages, a trend, tag associations and the shape of the days.

    Every number here is descriptive or associative; the ``about`` block in the
    response says so in the caller's language, and the summary sentences cannot
    contain causal vocabulary because the content loader refuses to start if they
    do. See :mod:`app.services.mood.insights` for the minimum-data rules.
    """
    zone, timezone_fallback_used = _resolve_zone(tz)
    resolved_locale, locale_fallback_used = resolve_insight_locale(locale or user.language)
    strings = load_insights(resolved_locale)
    now = _now()
    today = now.astimezone(zone).date()

    since = datetime.combine(
        today - timedelta(days=days - 1), datetime.min.time(), tzinfo=zone
    ).astimezone(UTC)
    check_ins, total = await _load(session, user.id, since=since)

    report: InsightsReport = build_insights(
        check_ins,
        zone=zone,
        timezone_fallback_used=timezone_fallback_used,
        today=today,
        window_days=days,
        total_check_ins=total,
    )

    _log.info(
        "insights_patterns_read",
        window_days=days,
        considered=report.considered_check_ins,
        enough_data=report.enough_data,
        # Counts and booleans only: never a value somebody recorded (safety rule 5).
        reported_factors=sum(1 for item in report.factors.associations if item.reported),
        time_of_day_reported=report.time_of_day.reported,
        weekday_reported=report.weekday.reported,
        locale=strings.locale,
    )
    return PatternsOut(
        **_meta(
            now=now,
            today=today,
            zone=zone,
            timezone_fallback_used=timezone_fallback_used,
            strings=strings,
            locale_fallback_used=locale_fallback_used,
            considered=report.considered_check_ins,
            total=report.total_check_ins,
        ),
        window_days=days,
        rolling=[_rolling_out(item, strings=strings, series=True) for item in report.rolling],
        trend=_trend_out(report.trend, strings=strings),
        factors=FactorReportOut(
            comparisons=report.factors.comparisons,
            family_alpha=round(report.factors.family_alpha, PAYLOAD_DIGITS + 3),
            min_group_n=MIN_GROUP_N,
            min_effect_points=MIN_EFFECT_POINTS,
            associations=[
                _association_out(item, strings=strings) for item in report.factors.associations
            ],
        ),
        time_of_day=_pattern_out(report.time_of_day, strings=strings),
        weekday=_pattern_out(report.weekday, strings=strings),
        summary=pattern_sentences(report, strings=strings),
        about=_about(strings, fallback_used=locale_fallback_used),
    )


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "AboutOut",
    "AssociationOut",
    "FactorReportOut",
    "GroupBucketOut",
    "GroupPatternOut",
    "InsightsMeta",
    "MeasureTrendOut",
    "PatternsOut",
    "RollingOut",
    "RollingPointOut",
    "TrendOut",
    "TrendWindowOut",
    "WeekCountOut",
    "WeekOut",
    "WeeklyInsightsOut",
    "router",
]
