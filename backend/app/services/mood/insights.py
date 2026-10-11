"""Pure statistical insights over mood check-ins.

Everything in this module is a deterministic function over the data it receives — no
HTTP, no database, no randomness except where explicitly seeded for bootstrap/permutation
tests. The rules are strict about data minimums: fewer than 7 points in a window means
"not enough data yet", and every association requires at least 5 points per group.

The public functions are:

* :func:`rolling_average` — 7- and 30-day windows for valence and energy, with a
  trend direction that only claims direction when the window has >= 7 entries and
  the slope is meaningfully non-zero.
* :func:`factor_association` — compares average valence on days with vs without each
  factor tag; reports effect size (Cohen's d), group sizes, and a bootstrap 95% CI.
  Wording is associative, never causal.
* :func:`time_patterns` — average valence/energy by hour-of-day and by weekday,
  with the same minimum-data rules.
* :func:`weekly_summary` — a localised, template-based text summary of the last
  7 days (no LLM involved).

All functions accept an optional ``min_entries`` override for testing; the
production defaults are the contracted minimums.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import mean, stdev
from typing import TYPE_CHECKING, Final, Literal

from app.services.mood.taxonomy import MOOD_FACTORS

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

#: Minimum number of entries in a rolling window before a trend is reported.
MIN_ENTRIES_FOR_TREND: Final = 7

#: Minimum number of entries per group (with/without factor) for an association.
MIN_ENTRIES_PER_GROUP: Final = 5

#: Number of bootstrap resamples for confidence intervals.
BOOTSTRAP_RESAMPLES: Final = 2000

#: Number of permutations for the permutation test.
PERMUTATION_RESAMPLES: Final = 2000

#: The seed every resampling run uses unless a caller passes its own.
#:
#: A count-based statistic would be reproducible by construction; bootstrap and
#: permutation are Monte Carlo, so without a fixed default the same data returns
#: "p = 0.001" on one page load and "p = 0.002" on the next. Nobody can
#: investigate a number that re-rolls itself, and a person reading two screens
#: should not see two answers. With a fixed seed the answer depends on the data
#: and nothing else, and the sampling error (well inside the one decimal place
#: the copy shows) is a property of the method, not of the moment.
DEFAULT_SEED: Final = 20261011

#: The four directions a trend can be reported in. ``not_enough_data`` is a
#: *value*, not a missing one: "we do not know yet" is an answer this feature
#: gives on purpose, and the type says so.
Trend = Literal["up", "down", "flat", "not_enough_data"]

#: One check-in, as every statistic in this module sees it: an instant, two
#: scales, the emotion tags and the factor document.
#:
#: There is deliberately **no note field**. The type is the privacy guarantee —
#: a function that cannot be handed somebody's words cannot read them, however a
#: future caller would like it to. See :data:`ExportRow` for the one shape that
#: does carry a note, and why.
Entry = tuple[datetime, int, int, list[str], dict[str, object]]

#: One check-in, as the CSV export sees it: an :data:`Entry` plus the owner's own
#: note. The export is the person's own copy of their own data, so their note
#: belongs in it; nothing in this module computes anything from this shape.
ExportRow = tuple[datetime, int, int, list[str], dict[str, object], str | None]


@dataclass(frozen=True)
class RollingPoint:
    """One point in a rolling average series."""

    day: date
    valence_avg: float | None
    energy_avg: float | None
    count: int


@dataclass(frozen=True)
class RollingWindowResult:
    """A rolling average window (7 or 30 days) with trend."""

    window_days: int
    points: list[RollingPoint]
    trend: Trend
    trend_note: str


@dataclass(frozen=True)
class FactorAssociation:
    """Association between a factor and average valence."""

    factor: str
    with_factor_avg: float | None
    without_factor_avg: float | None
    with_factor_count: int
    without_factor_count: int
    effect_size: float | None  # Cohen's d
    ci_lower: float | None  # 95% bootstrap CI for the difference
    ci_upper: float | None
    p_value: float | None  # permutation test
    note: str


@dataclass(frozen=True)
class TimePattern:
    """Average valence/energy for a time bucket (hour or weekday)."""

    bucket: str  # "00"-"23" for hours, "Mon"-"Sun" for weekdays
    valence_avg: float | None
    energy_avg: float | None
    count: int
    note: str


@dataclass(frozen=True)
class WeeklySummary:
    """A localised, template-based weekly summary."""

    locale: str
    period_start: date
    period_end: date
    total_check_ins: int
    valence_avg: float | None
    energy_avg: float | None
    trend: Trend
    top_factors: list[tuple[str, int]]  # (factor, count)
    text: str


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _to_local_days(
    entries: Iterable[Entry],
    zone: str = "UTC",
) -> dict[date, list[tuple[int, int, list[str], dict[str, object]]]]:
    """Group entries by local date in the given timezone.

    Returns a mapping from local date to a list of (valence, energy, emotions, factors).
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(zone)
    by_day: dict[date, list[tuple[int, int, list[str], dict[str, object]]]] = defaultdict(list)
    for created_at, valence, energy, emotions, factors in entries:
        local_day = created_at.astimezone(tz).date()
        by_day[local_day].append((valence, energy, emotions, factors))
    return by_day


def _linear_trend(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """Simple linear regression slope and intercept.

    Returns (slope, intercept). Assumes len(xs) == len(ys) >= 2.
    """
    x_mean = mean(xs)
    y_mean = mean(ys)
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return 0.0, y_mean
    slope = num / den
    intercept = y_mean - slope * x_mean
    return slope, intercept


def _cohens_d(group1: list[float], group2: list[float]) -> float | None:
    """Cohen's d for two independent groups.

    Returns None if either group has < 2 entries or pooled SD is 0.
    """
    if len(group1) < 2 or len(group2) < 2:
        return None
    m1, m2 = mean(group1), mean(group2)
    s1, s2 = stdev(group1), stdev(group2)
    n1, n2 = len(group1), len(group2)
    pooled_var = ((n1 - 1) * s1**2 + (n2 - 1) * s2**2) / (n1 + n2 - 2)
    if pooled_var <= 0:
        return None
    pooled_sd = math.sqrt(pooled_var)
    return (m1 - m2) / pooled_sd


def _bootstrap_ci(
    group1: list[float],
    group2: list[float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    rng: random.Random | None = None,
) -> tuple[float | None, float | None]:
    """95% bootstrap CI for the difference in means (group1 - group2)."""
    if len(group1) < 2 or len(group2) < 2:
        return None, None
    rng = rng or random.Random(DEFAULT_SEED)
    diffs: list[float] = []
    for _ in range(resamples):
        s1 = [rng.choice(group1) for _ in group1]
        s2 = [rng.choice(group2) for _ in group2]
        diffs.append(mean(s1) - mean(s2))
    diffs.sort()
    lo = diffs[int(0.025 * resamples)]
    hi = diffs[int(0.975 * resamples)]
    return lo, hi


def _permutation_p_value(
    group1: list[float],
    group2: list[float],
    *,
    resamples: int = PERMUTATION_RESAMPLES,
    rng: random.Random | None = None,
) -> float | None:
    """Two-sided permutation test p-value for difference in means."""
    if len(group1) < 2 or len(group2) < 2:
        return None
    rng = rng or random.Random(DEFAULT_SEED)
    observed_diff = mean(group1) - mean(group2)
    combined = group1 + group2
    n1 = len(group1)
    extreme = 0
    for _ in range(resamples):
        rng.shuffle(combined)
        perm_diff = mean(combined[:n1]) - mean(combined[n1:])
        if abs(perm_diff) >= abs(observed_diff):
            extreme += 1
    return (extreme + 1) / (resamples + 1)  # +1 for observed permutation


# --------------------------------------------------------------------------- #
# Public functions
# --------------------------------------------------------------------------- #


def rolling_average(
    entries: Iterable[Entry],
    *,
    window_days: int = 7,
    zone: str = "UTC",
    min_entries: int = MIN_ENTRIES_FOR_TREND,
    rng: random.Random | None = None,
) -> RollingWindowResult:
    """Compute a rolling average of valence and energy over ``window_days``.

    ``entries`` is an iterable of (created_at, valence, energy, emotions, factors).
    The result includes one point per local day that has at least one entry, with
    the average valence and energy for that day. A linear trend is fitted over the
    window; the direction is only reported when the window has at least
    ``min_entries`` distinct days with data.
    """
    by_day = _to_local_days(entries, zone=zone)
    if not by_day:
        return RollingWindowResult(
            window_days=window_days,
            points=[],
            trend="not_enough_data",
            trend_note=f"No check-ins in the last {window_days} days.",
        )

    # Build daily averages
    daily: list[tuple[date, float, float, int]] = []
    for day in sorted(by_day.keys()):
        vals = by_day[day]
        valence_avg = mean(v for v, _, _, _ in vals)
        energy_avg = mean(e for _, e, _, _ in vals)
        daily.append((day, valence_avg, energy_avg, len(vals)))

    # Keep only the last ``window_days`` days
    cutoff = max(d[0] for d in daily) - timedelta(days=window_days - 1)
    window = [d for d in daily if d[0] >= cutoff]

    points = [
        RollingPoint(day=day, valence_avg=va, energy_avg=ea, count=cnt)
        for day, va, ea, cnt in window
    ]

    # Trend requires at least min_entries distinct days in the window
    if len(window) < min_entries:
        return RollingWindowResult(
            window_days=window_days,
            points=points,
            trend="not_enough_data",
            trend_note=f"Need at least {min_entries} days with check-ins to show a trend.",
        )

    # Fit linear trend on valence (primary) over the window
    xs = list(range(len(window)))
    ys_valence = [va for _, va, _, _ in window]
    slope, _ = _linear_trend(xs, ys_valence)

    # A "meaningful" slope threshold: ~0.1 valence points per day over the window
    threshold = 0.1
    trend: Trend = "flat"
    if slope > threshold:
        trend = "up"
        trend_note = "Your mood has been trending up over this period."
    elif slope < -threshold:
        trend = "down"
        trend_note = "Your mood has been trending down over this period."
    else:
        trend_note = "Your mood has been stable over this period."

    return RollingWindowResult(
        window_days=window_days,
        points=points,
        trend=trend,
        trend_note=trend_note,
    )


def factor_association(
    entries: Iterable[Entry],
    *,
    zone: str = "UTC",
    min_per_group: int = MIN_ENTRIES_PER_GROUP,
    rng: random.Random | None = None,
) -> list[FactorAssociation]:
    """Compare average valence on days with vs without each factor tag.

    For each factor in :data:`MOOD_FACTORS`, groups check-ins by whether that
    factor was present on that day (a day counts as "with factor" if any check-in
    that day has the factor). Reports effect size (Cohen's d), bootstrap 95% CI,
    and permutation test p-value. Wording is associative, never causal.
    """
    by_day = _to_local_days(entries, zone=zone)

    # Per day: average valence, and which factors were present
    day_data: list[tuple[date, float, set[str]]] = []
    for day, vals in by_day.items():
        valence_avg = mean(v for v, _, _, _ in vals)
        factors_present: set[str] = set()
        for _, _, _, factors in vals:
            factors_present.update(factors.keys())
        day_data.append((day, valence_avg, factors_present))

    results: list[FactorAssociation] = []
    for factor in MOOD_FACTORS:
        with_factor = [va for _, va, facs in day_data if factor in facs]
        without_factor = [va for _, va, facs in day_data if factor not in facs]

        with_count = len(with_factor)
        without_count = len(without_factor)

        if with_count < min_per_group or without_count < min_per_group:
            note = (
                f"Not enough data to compare days with vs without {factor} "
                f"(need at least {min_per_group} days per group; "
                f"have {with_count} with, {without_count} without)."
            )
            results.append(
                FactorAssociation(
                    factor=factor,
                    with_factor_avg=mean(with_factor) if with_factor else None,
                    without_factor_avg=mean(without_factor) if without_factor else None,
                    with_factor_count=with_count,
                    without_factor_count=without_count,
                    effect_size=None,
                    ci_lower=None,
                    ci_upper=None,
                    p_value=None,
                    note=note,
                )
            )
            continue

        with_avg = mean(with_factor)
        without_avg = mean(without_factor)
        diff = with_avg - without_avg
        effect = _cohens_d(with_factor, without_factor)
        ci_lo, ci_hi = _bootstrap_ci(with_factor, without_factor, rng=rng)
        p_val = _permutation_p_value(with_factor, without_factor, rng=rng)

        # Associative wording only
        if diff > 0:
            direction = "higher"
        elif diff < 0:
            direction = "lower"
        else:
            direction = "about the same"

        note = (
            f"On days you tagged {factor}, your mood was {abs(diff):.1f} points "
            f"{direction} on average (n={with_count} vs {without_count}). "
            f"This is an association, not a causal link."
        )

        results.append(
            FactorAssociation(
                factor=factor,
                with_factor_avg=with_avg,
                without_factor_avg=without_avg,
                with_factor_count=with_count,
                without_factor_count=without_count,
                effect_size=effect,
                ci_lower=ci_lo,
                ci_upper=ci_hi,
                p_value=p_val,
                note=note,
            )
        )

    return results


def time_patterns(
    entries: Iterable[Entry],
    *,
    zone: str = "UTC",
    min_per_bucket: int = MIN_ENTRIES_PER_GROUP,
    rng: random.Random | None = None,
) -> tuple[list[TimePattern], list[TimePattern]]:
    """Average valence/energy by hour-of-day and by weekday.

    Returns (hourly_patterns, weekday_patterns). Each pattern has the same
    minimum-data rule: a bucket is reported only when it has at least
    ``min_per_bucket`` entries.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(zone)

    hourly: dict[int, list[tuple[int, int]]] = defaultdict(list)  # hour -> [(valence, energy)]
    weekday: dict[int, list[tuple[int, int]]] = defaultdict(list)  # 0=Mon -> [(valence, energy)]

    for created_at, valence, energy, _, _ in entries:
        local = created_at.astimezone(tz)
        hourly[local.hour].append((valence, energy))
        weekday[local.weekday()].append((valence, energy))

    # Hourly patterns (0-23)
    hour_patterns: list[TimePattern] = []
    for h in range(24):
        vals = hourly.get(h, [])
        count = len(vals)
        if count >= min_per_bucket:
            va = mean(v for v, _ in vals)
            ea = mean(e for _, e in vals)
            note = f"{count} check-in(s) in this hour."
        else:
            va = None
            ea = None
            note = (
                f"Only {count} check-in(s) in this hour — need {min_per_bucket} to show a pattern."
            )
        hour_patterns.append(
            TimePattern(bucket=f"{h:02d}", valence_avg=va, energy_avg=ea, count=count, note=note)
        )

    # Weekday patterns (Mon-Sun)
    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weekday_patterns: list[TimePattern] = []
    for wd in range(7):
        vals = weekday.get(wd, [])
        count = len(vals)
        if count >= min_per_bucket:
            va = mean(v for v, _ in vals)
            ea = mean(e for _, e in vals)
            note = f"{count} check-in(s) on this weekday."
        else:
            va = None
            ea = None
            note = (
                f"Only {count} check-in(s) on {weekday_names[wd]} — "
                f"need {min_per_bucket} to show a pattern."
            )
        weekday_patterns.append(
            TimePattern(
                bucket=weekday_names[wd], valence_avg=va, energy_avg=ea, count=count, note=note
            )
        )

    return hour_patterns, weekday_patterns


# --------------------------------------------------------------------------- #
# Localised weekly summary templates
# --------------------------------------------------------------------------- #

_WEEKLY_TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "heading": "Your week in review",
        "no_data": "No check-ins this week. Whenever you start, that's day one.",
        "trend_up": "Your mood trended up this week.",
        "trend_down": "Your mood trended down this week.",
        "trend_flat": "Your mood was steady this week.",
        "trend_unknown": (
            "Not enough check-ins this week to show a trend (need 7 days with data)."
        ),
        "valence_energy": (
            "Average mood: {valence:.1f}/5, energy: {energy:.1f}/5 over {count} check-in(s)."
        ),
        "top_factors": "Most common tags: {factors}.",
        "top_factors_none": "No context tags this week.",
        "closing": (
            "This is a pattern summary, not a diagnosis. A person you trust or a "
            "professional can help you make sense of it."
        ),
    },
    "hi": {
        "heading": "आपके सप्ताह की समीक्षा",
        "no_data": "इस सप्ताह कोई चेक-इन नहीं। जब भी आप शुरू करें, वही पहला दिन है।",
        "trend_up": "इस सप्ताह आपका मूड बेहतर हुआ।",
        "trend_down": "इस सप्ताह आपका मूड नीचे गया।",
        "trend_flat": "इस सप्ताह आपका मूड स्थिर रहा।",
        "trend_unknown": "रुझान दिखाने के लिए पर्याप्त चेक-इन नहीं (7 दिनों के डेटा की जरूरत है)।",
        "valence_energy": "औसत मूड: {valence:.1f}/5, ऊर्जा: {energy:.1f}/5, {count} चेक-इन पर।",
        "top_factors": "सबसे आम टैग: {factors}।",
        "top_factors_none": "इस सप्ताह कोई संदर्भ टैग नहीं।",
        "closing": (
            "यह एक पैटर्न सारांश है, निदान नहीं। कोई भरोसेमंद व्यक्ति या पेशेवर इसे समझने में मदद कर सकता है।"
        ),
    },
    "bn": {
        "heading": "আপনার সপ্তাহের পর্যালোচনা",
        "no_data": "এই সপ্তাহে কোনো চেক-ইন নেই। আপনি যখন শুরু করবেন, সেটাই দিন এক।",
        "trend_up": "এই সপ্তাহে আপনার মিজাজ উন্নত হয়েছে।",
        "trend_down": "এই সপ্তাহে আপনার মিজাজ নেমে গেছে।",
        "trend_flat": "এই সপ্তাহে আপনার মিজাজ স্থির ছিল।",
        "trend_unknown": ("প্রবণতা দেখানোর জন্য যথেষ্ট চেক-ইন নেই (7 দিনের ডেটা দরকার)।"),
        "valence_energy": (
            "গড় মিজাজ: {valence:.1f}/5, শক্তি: {energy:.1f}/5, {count}টি চেক-ইন থেকে।"
        ),
        "top_factors": "সবচেয়ে সাধারণ ট্যাগ: {factors}।",
        "top_factors_none": "এই সপ্তাহে কোনো প্রসঙ্গ ট্যাগ নেই।",
        "closing": (
            "এটি একটি প্যাটার্ন সারাংশ, রোগ নির্ণয় নয়। আপনি যাকে বিশ্বাস করেন বা "
            "একজন পেশাদার আপনাকে এটি বুঝতে সাহায্য করতে পারেন।"
        ),
    },
}


def weekly_summary(
    entries: Iterable[Entry],
    *,
    zone: str = "UTC",
    locale: str = "en",
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> WeeklySummary:
    """Generate a localised weekly summary for the last 7 days.

    ``now`` controls the clock (for tests). The summary covers the 7-day period
    ending at the local day *before* ``now`` (so if ``now`` is today, the window
    is the past 7 complete days). Uses templates — no LLM.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(zone)
    now = now or datetime.now(UTC)
    # Period end is the local day *before* now's local day (yesterday).
    # This makes "this week" mean the 7 complete days ending yesterday.
    period_end = now.astimezone(tz).date() - timedelta(days=1)
    period_start = period_end - timedelta(days=6)  # inclusive 7-day window

    by_day = _to_local_days(entries, zone=zone)

    # Filter to the 7-day window
    window_days = {day: vals for day, vals in by_day.items() if period_start <= day <= period_end}

    all_vals = [(v, e) for vals in window_days.values() for v, e, _, _ in vals]
    total_check_ins = len(all_vals)

    if total_check_ins == 0:
        templates = _WEEKLY_TEMPLATES.get(locale, _WEEKLY_TEMPLATES["en"])
        return WeeklySummary(
            locale=locale,
            period_start=period_start,
            period_end=period_end,
            total_check_ins=0,
            valence_avg=None,
            energy_avg=None,
            trend="not_enough_data",
            top_factors=[],
            text=templates["no_data"],
        )

    valence_avg = mean(v for v, _ in all_vals)
    energy_avg = mean(e for _, e in all_vals)

    # Factor counts across the week
    factor_counts: dict[str, int] = defaultdict(int)
    for vals in window_days.values():
        for _, _, _, factors in vals:
            for f in factors:
                factor_counts[f] += 1
    top_factors = sorted(factor_counts.items(), key=lambda x: -x[1])[:3]

    # Trend over the 7 days (daily valence averages). Typed explicitly so the
    # literal union survives every branch: the compiler is what stops a future
    # edit from inventing a fifth direction the UI has no copy for.
    daily_avgs = [
        (day, mean(v for v, _, _, _ in vals)) for day, vals in sorted(window_days.items())
    ]
    trend: Trend
    if len(daily_avgs) >= MIN_ENTRIES_FOR_TREND:
        xs = list(range(len(daily_avgs)))
        ys = [va for _, va in daily_avgs]
        slope, _ = _linear_trend(xs, ys)
        threshold = 0.1
        if slope > threshold:
            trend = "up"
        elif slope < -threshold:
            trend = "down"
        else:
            trend = "flat"
    else:
        trend = "not_enough_data"

    # Build text from template
    templates = _WEEKLY_TEMPLATES.get(locale, _WEEKLY_TEMPLATES["en"])
    trend_text = {
        "up": templates["trend_up"],
        "down": templates["trend_down"],
        "flat": templates["trend_flat"],
        "not_enough_data": templates["trend_unknown"],
    }[trend]

    valence_energy_text = templates["valence_energy"].format(
        valence=valence_avg, energy=energy_avg, count=total_check_ins
    )

    if top_factors:
        factors_str = ", ".join(f"{f} ({c})" for f, c in top_factors)
        factors_text = templates["top_factors"].format(factors=factors_str)
    else:
        factors_text = templates["top_factors_none"]

    text = " ".join(
        [
            templates["heading"],
            trend_text,
            valence_energy_text,
            factors_text,
            templates["closing"],
        ]
    )

    return WeeklySummary(
        locale=locale,
        period_start=period_start,
        period_end=period_end,
        total_check_ins=total_check_ins,
        valence_avg=valence_avg,
        energy_avg=energy_avg,
        trend=trend,
        top_factors=top_factors,
        text=text,
    )


# --------------------------------------------------------------------------- #
# CSV export helpers
# --------------------------------------------------------------------------- #
#
# Two input shapes, on purpose, and the difference is the privacy guarantee:
#
# * every **statistic** above takes ``(created_at, valence, energy, emotions,
#   factors)`` — a note has no place to go, so no statistics function *can* read
#   one, whatever a future caller would like;
# * the **export** takes ``(created_at, valence, energy, emotions, factors,
#   note)``, because the export is the person's own copy of their own data and a
#   note they wrote is theirs to download.
#
# Everything is written with :mod:`csv`, not string formatting: a note contains
# commas, quotes and newlines, and a hand-rolled writer would corrupt the file
# (or, worse, shift a value into the wrong column) the first time somebody wrote
# one containing a comma.


def _blank(value: object) -> object:
    """``None`` becomes an empty cell; every other value is written as it is."""
    return "" if value is None else value


def mood_entries_to_csv(
    rows: Iterable[ExportRow],
    *,
    zone: str = "UTC",
) -> str:
    """The person's own check-ins as CSV, including the notes they wrote.

    Rows are ``(created_at, valence, energy, emotions, factors, note)``. The date
    columns are the *local* day in ``zone``, so the file lines up with what the
    app showed them, and the raw instant is kept beside it so nothing is lost.
    """
    import csv
    import io
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(zone)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["local_date", "created_at_utc", "valence", "energy", "emotions", "factors", "note"]
    )
    for created_at, valence, energy, emotions, factors, note in rows:
        writer.writerow(
            [
                created_at.astimezone(tz).date().isoformat(),
                created_at.isoformat(),
                valence,
                energy,
                ";".join(emotions),
                ";".join(str(key) for key in factors),
                note if note is not None else "",
            ]
        )
    return buffer.getvalue().rstrip("\n")


def factor_associations_to_csv(associations: list[FactorAssociation]) -> str:
    """The factor comparisons as CSV — one row per tag, blanks where undefined."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "factor",
            "with_factor_avg",
            "without_factor_avg",
            "with_factor_count",
            "without_factor_count",
            "effect_size",
            "ci_lower",
            "ci_upper",
            "p_value",
            "note",
        ]
    )
    for a in associations:
        writer.writerow(
            [
                a.factor,
                _blank(a.with_factor_avg),
                _blank(a.without_factor_avg),
                a.with_factor_count,
                a.without_factor_count,
                _blank(a.effect_size),
                _blank(a.ci_lower),
                _blank(a.ci_upper),
                _blank(a.p_value),
                a.note,
            ]
        )
    return buffer.getvalue().rstrip("\n")


def time_patterns_to_csv(hourly: list[TimePattern], weekday: list[TimePattern]) -> str:
    """The hour-of-day and day-of-week buckets as CSV, in that order."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["type", "bucket", "valence_avg", "energy_avg", "count", "note"])
    for kind, patterns in (("hour", hourly), ("weekday", weekday)):
        for p in patterns:
            writer.writerow(
                [
                    kind,
                    p.bucket,
                    _blank(p.valence_avg),
                    _blank(p.energy_avg),
                    p.count,
                    p.note,
                ]
            )
    return buffer.getvalue().rstrip("\n")
