"""Opt-in, numeric-only mood Outlook built from a person's own recent history.

This deliberately does not import or call any learned model. It describes a
small, transparent personal-history baseline; it is not a clinical instrument,
diagnosis, risk classifier, or promise about the next few days.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import fmean
from typing import Literal
from zoneinfo import ZoneInfo

from app.content.insights import InsightsStrings
from app.content.outlook import OutlookLocaleCopy
from app.services.mood.streak import local_date
from app.services.mood.taxonomy import MOOD_FACTORS

OUTLOOK_LOOKBACK_DAYS = 28
MIN_OUTLOOK_CHECKIN_DAYS = 14
MAX_CHECKIN_AGE_DAYS = 7
OUTLOOK_HORIZON_DAYS = 3
MIN_FACTOR_DAYS_PER_GROUP = 5
MIN_FACTOR_DIFFERENCE_POINTS = 0.5
MIN_INTERVAL_HALF_WIDTH = 0.75


@dataclass(frozen=True)
class OutlookObservation:
    """Only allowed server-side inputs: timestamp, two scales and closed tags."""

    at: datetime
    valence: int
    energy: int
    factors: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutlookPoint:
    """One next-day estimate with an explicitly uncertain personal range."""

    day: date
    estimate: float
    lower: float
    upper: float


@dataclass(frozen=True)
class OutlookFactor:
    """A conservative, descriptive difference between two sets of tagged days."""

    tag: str
    label: str
    tagged_days: int
    other_days: int
    tagged_mean: float
    other_mean: float
    difference: float
    sentence: str


@dataclass(frozen=True)
class MoodOutlook:
    """Complete user-facing payload before HTTP serialisation."""

    status: Literal["ready", "not_enough_data", "stale"]
    available: bool
    today: date
    timezone: str
    locale: str
    locale_fallback_used: bool
    lookback_days: int
    minimum_checkin_days: int
    check_ins_used: int
    distinct_checkin_days: int
    latest_check_in: date | None
    summary: str
    uncertainty_note: str
    not_health_advice: str
    forecast: tuple[OutlookPoint, ...]
    top_factors: tuple[OutlookFactor, ...]
    no_factors_note: str | None
    small_action: str | None
    optional_support: str | None


@dataclass(frozen=True)
class _DailyMood:
    day: date
    valence: float
    factors: frozenset[str]


def _round(value: float) -> float:
    return round(float(value), 3)


def _local_number(value: float, strings: InsightsStrings) -> str:
    return strings.localise_number(value, digits=1)


def build_mood_outlook(
    observations: Sequence[OutlookObservation],
    *,
    now: datetime,
    zone: ZoneInfo,
    strings: InsightsStrings,
    copy: OutlookLocaleCopy,
    locale_fallback_used: bool,
) -> MoodOutlook:
    """Build three gently worded estimates, withholding below a 14-day gate.

    The gate is **14 distinct local calendar days with numeric check-ins in the
    latest 28 days**, and a newest check-in no more than seven days old. Multiple
    entries on one day count as one day for eligibility. Missing days are not
    imputed. The spread is a robust empirical description of personal
    variability, not a calibrated confidence interval or a probability claim.
    """
    today = now.astimezone(zone).date()
    start_day = today - timedelta(days=OUTLOOK_LOOKBACK_DAYS - 1)
    by_day: dict[date, list[OutlookObservation]] = defaultdict(list)
    for row in observations:
        local_day = local_date(row.at, zone)
        if start_day <= local_day <= today:
            by_day[local_day].append(row)
    daily = tuple(
        _DailyMood(
            day=day,
            valence=fmean(row.valence for row in rows),
            factors=frozenset(
                factor for row in rows for factor in row.factors if factor in MOOD_FACTORS
            ),
        )
        for day, rows in sorted(by_day.items())
    )
    latest_day = daily[-1].day if daily else None
    entry_count = sum(len(rows) for rows in by_day.values())
    too_few = len(daily) < MIN_OUTLOOK_CHECKIN_DAYS
    stale = latest_day is None or (today - latest_day).days > MAX_CHECKIN_AGE_DAYS
    no_forecast = too_few or stale

    if no_forecast:
        status: Literal["not_enough_data", "stale"] = "not_enough_data" if too_few else "stale"
        message = (
            copy.render(
                "not_enough_data",
                minimum=str(MIN_OUTLOOK_CHECKIN_DAYS),
                window=str(OUTLOOK_LOOKBACK_DAYS),
            )
            if too_few
            else copy.render("stale_data")
        )
        return MoodOutlook(
            status=status,
            available=False,
            today=today,
            timezone=getattr(zone, "key", None) or str(zone),
            locale=strings.locale,
            locale_fallback_used=locale_fallback_used,
            lookback_days=OUTLOOK_LOOKBACK_DAYS,
            minimum_checkin_days=MIN_OUTLOOK_CHECKIN_DAYS,
            check_ins_used=entry_count,
            distinct_checkin_days=len(daily),
            latest_check_in=latest_day,
            summary=message,
            uncertainty_note=copy.render("uncertainty_note"),
            not_health_advice=copy.render("not_health_advice"),
            forecast=(),
            top_factors=(),
            no_factors_note=None,
            small_action=None,
            optional_support=None,
        )

    all_values = [item.valence for item in daily]
    recent_start = today - timedelta(days=6)
    recent_values = [item.valence for item in daily if item.day >= recent_start]
    overall_mean = fmean(all_values)
    recent_mean = fmean(recent_values) if recent_values else overall_mean
    centre = min(5.0, max(1.0, 0.65 * recent_mean + 0.35 * overall_mean))
    residuals = sorted(abs(value - centre) for value in all_values)
    empirical_spread = residuals[max(0, math.ceil(0.8 * len(residuals)) - 1)]
    half_width = max(MIN_INTERVAL_HALF_WIDTH, empirical_spread)

    forecast = tuple(
        OutlookPoint(
            day=today + timedelta(days=horizon),
            estimate=_round(centre),
            lower=_round(max(1.0, centre - half_width * math.sqrt(horizon))),
            upper=_round(min(5.0, centre + half_width * math.sqrt(horizon))),
        )
        for horizon in range(1, OUTLOOK_HORIZON_DAYS + 1)
    )
    summary = copy.render(
        "summary",
        mean=_local_number(centre, strings),
        low=_local_number(min(point.lower for point in forecast), strings),
        high=_local_number(max(point.upper for point in forecast), strings),
    )

    factor_rows: list[tuple[float, OutlookFactor]] = []
    for factor in MOOD_FACTORS:
        tagged = [item.valence for item in daily if factor in item.factors]
        other = [item.valence for item in daily if factor not in item.factors]
        if len(tagged) < MIN_FACTOR_DAYS_PER_GROUP or len(other) < MIN_FACTOR_DAYS_PER_GROUP:
            continue
        tagged_mean = fmean(tagged)
        other_mean = fmean(other)
        difference = tagged_mean - other_mean
        if abs(difference) < MIN_FACTOR_DIFFERENCE_POINTS:
            continue
        label_key = f"factor.{factor}"
        label = strings.strings.get(label_key, factor)
        sentence = copy.render(
            "factor_line",
            factor=label,
            tagged_mean=_local_number(tagged_mean, strings),
            other_mean=_local_number(other_mean, strings),
            tagged_n=strings.localise_number(float(len(tagged)), digits=0),
            untagged_n=strings.localise_number(float(len(other)), digits=0),
        )
        factor_rows.append(
            (
                abs(difference),
                OutlookFactor(
                    tag=factor,
                    label=label,
                    tagged_days=len(tagged),
                    other_days=len(other),
                    tagged_mean=_round(tagged_mean),
                    other_mean=_round(other_mean),
                    difference=_round(difference),
                    sentence=sentence,
                ),
            )
        )
    top_factors = tuple(
        item for _magnitude, item in sorted(factor_rows, key=lambda row: (-row[0], row[1].tag))[:2]
    )

    previous_values = [item.valence for item in daily if item.day < recent_start]
    meaningful_drop = bool(previous_values) and recent_mean <= fmean(previous_values) - 0.75
    offer_support = centre <= 2.5 or meaningful_drop
    return MoodOutlook(
        status="ready",
        available=True,
        today=today,
        timezone=getattr(zone, "key", None) or str(zone),
        locale=strings.locale,
        locale_fallback_used=locale_fallback_used,
        lookback_days=OUTLOOK_LOOKBACK_DAYS,
        minimum_checkin_days=MIN_OUTLOOK_CHECKIN_DAYS,
        check_ins_used=entry_count,
        distinct_checkin_days=len(daily),
        latest_check_in=latest_day,
        summary=summary,
        uncertainty_note=copy.render("uncertainty_note"),
        not_health_advice=copy.render("not_health_advice"),
        forecast=forecast,
        top_factors=top_factors,
        no_factors_note=copy.render("no_factors") if not top_factors else None,
        small_action=copy.render("action"),
        optional_support=copy.render("support") if offer_support else None,
    )


__all__ = [
    "MAX_CHECKIN_AGE_DAYS",
    "MIN_FACTOR_DAYS_PER_GROUP",
    "MIN_FACTOR_DIFFERENCE_POINTS",
    "MIN_INTERVAL_HALF_WIDTH",
    "MIN_OUTLOOK_CHECKIN_DAYS",
    "OUTLOOK_HORIZON_DAYS",
    "OUTLOOK_LOOKBACK_DAYS",
    "MoodOutlook",
    "OutlookFactor",
    "OutlookObservation",
    "OutlookPoint",
    "build_mood_outlook",
]
