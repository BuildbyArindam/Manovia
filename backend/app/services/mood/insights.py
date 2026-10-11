"""Insights: what somebody's own check-ins can honestly be said to show.

Everything here is a pure function over numbers the person already gave us. No
model, no LLM, no network, no database — and, more importantly, no claim the
data cannot support. The module is written around one rule that shapes every
function in it:

    **An insight that cannot be supported must not be produced.**

Not "produced with a caveat". Not produced. On a mental-health product a
pattern somebody believes about themselves becomes a fact about themselves, so
a false positive is not a cosmetic bug — it is a sentence a person will carry
around ("I am worse on Mondays") that we invented. Three mechanisms enforce
the rule, and each one is a number a test can check:

1. **Minimum-data rules.** A trend needs at least :data:`MIN_ENTRIES_FOR_TREND`
   check-ins in *each* of the two windows it compares; a group comparison needs
   :data:`MIN_GROUP_N` in each group it names. Below that the function returns
   a holdback reason and no direction, no difference, no sentence.
2. **An effect-size floor.** A difference smaller than :data:`MIN_EFFECT_POINTS`
   on a 1-5 scale is not reported even when it is statistically tidy. Half a
   point is roughly the distance between "okay" and "a bit low"; anything
   smaller is not worth anybody's attention, and reporting it would be the
   classic sin of a small-n dashboard.
3. **Multiplicity correction inside each family.** Eight factors times two measures
   is sixteen tests; at alpha=0.05 pure noise produces a "finding" in about half of
   all people. Each family therefore tests at alpha/m (:data:`ALPHA` divided by the
   number of comparisons actually run in that family), and the corrected alpha is
   published in the response so the number is inspectable rather than baked in.

**Associative, never causal.** A factor tag says a thing was on somebody's
mind when they checked in; it does not say the thing did anything to them, and
the vocabulary cannot even tell us whether sleep was *bad* — only that "sleep"
was tagged. So every comparison is phrased as a difference between two sets of
days ("on days you tagged sleep, your mood was about 0.8 lower on average"),
never as an influence, and the wording lives in reviewed template files rather
than in f-strings next to the arithmetic. A test and the Day 16 verification
script both scan the generated sentences for causal vocabulary.

**Determinism is a feature.** The permutation and bootstrap draws use a fixed
seed derived from the comparison's name (:func:`_generator`), so the same
check-ins always produce the same p-value, the same interval and the same
sentence. A number that changes when somebody refreshes the page is a number
nobody can trust, and a person re-reading their own insights next week must
find the same thing they read today.

**Higher and lower, never better and worse.** Valence is the person's own
rating, not a measurement of how well they are doing. Every direction word this
module can emit is "higher" or "lower"; "better"/"worse" would turn a
description of their numbers into a verdict on their week.
"""

from __future__ import annotations

import random
import zlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from statistics import fmean, stdev
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from app.services.mood.streak import local_date, local_today
from app.services.mood.taxonomy import MOOD_FACTORS, SCALE_MAX

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.content.insights import InsightsStrings

__all__ = [
    "ALPHA",
    "BOOTSTRAP_DRAWS",
    "MIN_EFFECT_POINTS",
    "MIN_ENTRIES_FOR_TREND",
    "MIN_ENTRIES_TO_PLOT",
    "MIN_GROUP_N",
    "PERMUTATION_DRAWS",
    "TIME_OF_DAY_BUCKETS",
    "WEEKDAY_IDS",
    "CheckIn",
    "FactorAssociation",
    "FactorReport",
    "GroupBucket",
    "GroupPattern",
    "Holdback",
    "InsightsReport",
    "LocalCheckIn",
    "Measure",
    "PatternKind",
    "RollingAverage",
    "RollingPoint",
    "Trend",
    "TrendDirection",
    "TrendWindow",
    "WeekBucket",
    "bootstrap_ci",
    "build_insights",
    "factor_associations",
    "group_pattern",
    "pattern_sentences",
    "permutation_p_value",
    "prepare",
    "rolling_average",
    "rolling_series",
    "summary_sentences",
    "time_of_day_bucket",
    "trend",
    "weekly_buckets",
    "weekly_sentences",
]


# --------------------------------------------------------------------------- #
# The rules, as numbers                                                         #
# --------------------------------------------------------------------------- #

#: How many check-ins a window needs before this module will say anything about
#: a direction. Seven is a week of daily check-ins: fewer than that and a
#: "trend" is one bad Tuesday.
MIN_ENTRIES_FOR_TREND: Final = 7

#: How many check-ins each side of a comparison needs before the comparison is
#: allowed to exist at all — not before it is allowed to be *significant*,
#: before it is allowed to be computed and shown.
MIN_GROUP_N: Final = 5

#: The smallest difference in points (on the 1-5 scales) worth telling anybody
#: about. Below this the answer is "about the same", whatever the p-value says.
MIN_EFFECT_POINTS: Final = 0.4

#: The smallest change between two trend windows worth calling a direction.
#: Tighter than :data:`MIN_EFFECT_POINTS` because a change is a difference
#: between two averages of the same person, not between two kinds of day.
MIN_TREND_CHANGE: Final = 0.3

#: Per-family significance level (the Greek letter in every textbook, spelled
#: out here because the linter treats the glyph as an ambiguous character),
#: before the multiplicity correction.
ALPHA: Final = 0.05

#: Permutation and bootstrap draws. Enough for a p-value resolution of about
#: 1/2000 (which is below the corrected alpha for every family this module
#: runs),
#: small enough that sixteen comparisons stay inside a request budget.
PERMUTATION_DRAWS: Final = 2000
BOOTSTRAP_DRAWS: Final = 2000

#: The percentile interval reported for a difference in means.
CI_LEVEL: Final = 0.95

#: A rolling average is drawn from this many points onwards. One check-in in a
#: seven-day window is not an average, it is that check-in with a line through
#: it; the point is still returned (with its ``entries`` count) so the chart can
#: be honest about the thin part of the series instead of hiding it.
MIN_ENTRIES_TO_PLOT: Final = 3

#: How far back the rolling-average series reaches, in local days.
ROLLING_SERIES_DAYS: Final = 56

#: The windows a rolling average is reported for.
ROLLING_WINDOWS: Final[tuple[int, ...]] = (7, 30)

#: How many natural weeks the weekly summary covers by default, and at most.
DEFAULT_WEEKS: Final = 4
MAX_WEEKS: Final = 12

#: The longest stretch of check-ins one insights call reads. A year of daily
#: check-ins is 365; the ceiling keeps the payload and the arithmetic bounded.
MAX_WINDOW_DAYS: Final = 365

#: Fixed seed for every resampling draw, so a result is reproducible.
_RESAMPLE_SEED: Final = 20_261_016


class Measure(StrEnum):
    """Which of the two scales a number describes.

    Named explicitly rather than passed around as ``"valence"`` strings, because
    the whole point of a two-measure report is that no sentence may quietly
    describe energy while saying "mood".
    """

    VALENCE = "valence"
    ENERGY = "energy"


class TrendDirection(StrEnum):
    """The three things a trend may be, plus the honest fourth.

    ``STEADY`` is a finding, not a failure: "about the same as before" is a true
    and useful sentence. ``NOT_ENOUGH_DATA`` is separate from ``STEADY`` on
    purpose — conflating them would tell somebody with three check-ins that
    their mood is stable, which is the one thing we cannot know.
    """

    RISING = "rising"
    FALLING = "falling"
    STEADY = "steady"
    NOT_ENOUGH_DATA = "not_enough_data"


class Holdback(StrEnum):
    """Why a comparison produced no sentence.

    These codes reach the client, so the UI can say *why* a panel is empty
    instead of showing a blank space that reads as "everything is fine".
    """

    #: Fewer check-ins in the window than any comparison needs.
    NOT_ENOUGH_DATA = "not_enough_data"
    #: One side of the comparison has fewer than :data:`MIN_GROUP_N` check-ins.
    GROUP_TOO_SMALL = "group_too_small"
    #: The difference is below :data:`MIN_EFFECT_POINTS`.
    SMALL_DIFFERENCE = "small_difference"
    #: The difference is inside the resampling noise (p above the corrected
    #: alpha, or
    #: the interval spans zero).
    WITHIN_NOISE = "within_noise"
    #: Every check-in landed in the same bucket, so there is nothing to compare.
    SINGLE_GROUP = "single_group"


class PatternKind(StrEnum):
    """Which grouping a :class:`GroupPattern` describes."""

    TIME_OF_DAY = "time_of_day"
    WEEKDAY = "weekday"


#: The four parts of a day, by *local* hour. Four is a deliberate choice: seven
#: buckets of a 60-day history cannot reach :data:`MIN_GROUP_N` each, and a
#: pattern that only ever reports "insufficient data" is not a feature.
TIME_OF_DAY_BUCKETS: Final[tuple[str, ...]] = ("morning", "afternoon", "evening", "night")

#: Hour → bucket. 05-11 morning, 12-16 afternoon, 17-21 evening, 22-04 night.
_HOUR_BUCKET: Final[dict[int, str]] = {
    **{hour: "night" for hour in (22, 23, 0, 1, 2, 3, 4)},
    **{hour: "morning" for hour in range(5, 12)},
    **{hour: "afternoon" for hour in range(12, 17)},
    **{hour: "evening" for hour in range(17, 22)},
}

#: Weekday ids, Monday first (Python's ``datetime.weekday()`` order).
WEEKDAY_IDS: Final[tuple[str, ...]] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def time_of_day_bucket(hour: int) -> str:
    """The part of a day a local hour falls in."""
    return _HOUR_BUCKET[hour % 24]


# --------------------------------------------------------------------------- #
# Input                                                                         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CheckIn:
    """One stored check-in, reduced to everything an insight is allowed to see.

    There is deliberately no note field. Insights are computed from the two
    scales, the closed tag vocabularies and the timestamp; the note is never
    loaded, so no code path here can put somebody's private sentence into a
    summary, a log line or a chart tooltip.
    """

    at: datetime
    valence: int
    energy: int
    factors: tuple[str, ...] = ()
    emotions: tuple[str, ...] = ()

    def value(self, measure: Measure) -> float:
        """This check-in's number for ``measure``."""
        return float(self.valence if measure is Measure.VALENCE else self.energy)


@dataclass(frozen=True)
class LocalCheckIn:
    """A :class:`CheckIn` with its local calendar facts attached.

    The conversion happens once, here, because the day an insight groups by has
    to be the same day the streak counts — two modules deciding separately where
    midnight falls is how a chart ends up disagreeing with the number next to it.
    """

    at: datetime
    day: date
    hour: int
    weekday: int
    valence: int
    energy: int
    factors: tuple[str, ...]
    emotions: tuple[str, ...]

    def value(self, measure: Measure) -> float:
        return float(self.valence if measure is Measure.VALENCE else self.energy)


def prepare(check_ins: Iterable[CheckIn], *, zone: ZoneInfo) -> tuple[LocalCheckIn, ...]:
    """Attach local day/hour/weekday to each check-in, oldest first.

    A naive timestamp is read as UTC (every timestamp this application writes is
    UTC-aware, and SQLite hands some of them back naive — see
    :func:`app.services.mood.streak.local_date`).
    """
    prepared: list[LocalCheckIn] = []
    for entry in check_ins:
        aware = entry.at if entry.at.tzinfo is not None else entry.at.replace(tzinfo=UTC)
        local = aware.astimezone(zone)
        prepared.append(
            LocalCheckIn(
                at=aware,
                day=local.date(),
                hour=local.hour,
                weekday=local.weekday(),
                valence=entry.valence,
                energy=entry.energy,
                factors=tuple(entry.factors),
                emotions=tuple(entry.emotions),
            )
        )
    prepared.sort(key=lambda item: (item.at, item.day))
    return tuple(prepared)


def _in_window(points: Sequence[LocalCheckIn], *, start: date, end: date) -> list[LocalCheckIn]:
    """The check-ins whose local day is in ``[start, end]``, inclusive."""
    return [point for point in points if start <= point.day <= end]


def _mean(values: Sequence[float]) -> float | None:
    return fmean(values) if values else None


# --------------------------------------------------------------------------- #
# Resampling: a permutation test and a bootstrap interval                       #
# --------------------------------------------------------------------------- #


def _generator(*key: str) -> random.Random:
    """A deterministic RNG for one named comparison.

    Seeded from the comparison's own name so two different factors do not share
    a draw stream, and from a fixed constant so the same data always produces
    the same p-value and the same sentence.
    """
    digest = zlib.crc32(":".join(key).encode("utf-8"))
    return random.Random(_RESAMPLE_SEED * 1_000_003 + digest)


def permutation_p_value(
    group: Sequence[float],
    rest: Sequence[float],
    *,
    key: str,
    draws: int = PERMUTATION_DRAWS,
) -> float:
    """Two-sided permutation p-value for ``mean(group) - mean(rest)``.

    The statistic is the absolute difference in means; the null distribution is
    built by re-dealing the same pooled values into the same group sizes. That
    is the whole test — no normality assumption, no t-table, and it is exactly
    right for a sample of thirty integers between 1 and 5.

    The reported p-value is ``(1 + #{draws ≥ observed}) / (draws + 1)``, so it
    can never be zero: with a finite number of draws "we never saw this" is not
    the same claim as "this cannot happen", and printing ``p = 0`` next to
    somebody's sleep would say the second thing.
    """
    if not group or not rest:
        return 1.0
    pooled = [*group, *rest]
    size = len(group)
    total = sum(pooled)
    observed = abs(fmean(group) - fmean(rest))
    rng = _generator("permutation", key)

    at_least = 0
    for _ in range(draws):
        drawn = sum(rng.sample(pooled, size))
        candidate = abs(drawn / size - (total - drawn) / (len(pooled) - size))
        if candidate >= observed:
            at_least += 1
    return (1 + at_least) / (draws + 1)


def permutation_f_p_value(
    values: Sequence[float],
    labels: Sequence[str],
    *,
    key: str,
    draws: int = PERMUTATION_DRAWS,
) -> float | None:
    """One-way omnibus permutation test over several groups.

    Used for the time-of-day and weekday patterns, where the question is "do
    these groups differ *at all*" rather than "which pair differs". Testing the
    omnibus and only then naming the extremes keeps a seven-group weekday panel
    to a single comparison instead of twenty-one pairwise ones — the correction
    stays small, and no sentence is generated about a pair that was never
    tested.

    Returns ``None`` when the test cannot be run (fewer than two non-empty
    groups, or not enough values to have a within-group variance).
    """
    if len(values) != len(labels):
        raise ValueError("values and labels must be the same length")
    observed = _f_statistic(values, labels)
    if observed is None:
        return None
    rng = _generator("permutation-f", key)
    shuffled = list(labels)

    at_least = 0
    for _ in range(draws):
        rng.shuffle(shuffled)
        candidate = _f_statistic(values, shuffled)
        if candidate is not None and candidate >= observed:
            at_least += 1
    return (1 + at_least) / (draws + 1)


def _f_statistic(values: Sequence[float], labels: Sequence[str]) -> float | None:
    """The one-way ANOVA F ratio, or ``None`` when it is undefined.

    Accumulated in one pass over ``(sum, sum of squares, count)`` per group
    rather than by materialising the groups, because this runs once per
    permutation draw: two thousand draws of a seven-group weekday test is
    fourteen thousand group means, and an insights request should not spend its
    budget building dictionaries to throw away.
    """
    sums: dict[str, float] = defaultdict(float)
    squares: dict[str, float] = defaultdict(float)
    counts: Counter[str] = Counter()
    for value, label in zip(values, labels, strict=True):
        sums[label] += value
        squares[label] += value * value
        counts[label] += 1

    groups = len(counts)
    total = len(values)
    if groups < 2 or total <= groups:
        return None

    grand_total = sum(sums.values())
    # Between-group SS and within-group SS, both from the running totals.
    fitted = sum(total_sum * total_sum / counts[label] for label, total_sum in sums.items())
    between = fitted - grand_total * grand_total / total
    within = sum(squares.values()) - fitted
    if within <= 0.0:
        # Every value inside every group is identical. If the groups also agree
        # with each other there is no signal at all; if they disagree the ratio
        # is unbounded, and ``inf`` compares correctly against every draw.
        return float("inf") if between > 0.0 else 0.0
    return (between / (groups - 1)) / (within / (total - groups))


def bootstrap_ci(
    group: Sequence[float],
    rest: Sequence[float],
    *,
    key: str,
    level: float = CI_LEVEL,
    draws: int = BOOTSTRAP_DRAWS,
) -> tuple[float, float] | None:
    """A percentile bootstrap interval for ``mean(group) - mean(rest)``.

    The interval is the honest half of an association: it says how much of the
    difference could be the shape of this particular fortnight. When it spans
    zero the difference is not reported, whatever the point estimate looks like.
    """
    if len(group) < 2 or len(rest) < 2:
        return None
    rng = _generator("bootstrap", key)
    size_a, size_b = len(group), len(rest)

    differences: list[float] = []
    for _ in range(draws):
        mean_a = fmean(rng.choices(group, k=size_a))
        mean_b = fmean(rng.choices(rest, k=size_b))
        differences.append(mean_a - mean_b)
    differences.sort()

    tail = (1.0 - level) / 2.0
    low_index = min(max(int(tail * draws), 0), draws - 1)
    high_index = min(max(int((1.0 - tail) * draws) - 1, 0), draws - 1)
    return differences[low_index], differences[high_index]


def _cohens_d(group: Sequence[float], rest: Sequence[float]) -> float | None:
    """The standardised difference, or ``None`` when it is not defined.

    ``None`` happens when the pooled spread is zero — every check-in in both
    groups identical. The honest answer then is "no spread to standardise
    against", not ``inf``: JSON has no infinity, and a chart bar of infinite
    height would be the least useful thing this module could return.
    """
    if len(group) < 2 or len(rest) < 2:
        return None
    size_a, size_b = len(group), len(rest)
    # ``float()`` around each call: typeshed's overloads make the return type of
    # ``stdev`` depend on the element type it infers, and a strict check would
    # otherwise see an ``Any`` escaping a function declared to return a float.
    variance_a, variance_b = float(stdev(group)) ** 2, float(stdev(rest)) ** 2
    pooled = ((size_a - 1) * variance_a + (size_b - 1) * variance_b) / (size_a + size_b - 2)
    if pooled <= 0.0:
        return None
    return float((fmean(group) - fmean(rest)) / (pooled**0.5))


# --------------------------------------------------------------------------- #
# Rolling averages and trend                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RollingPoint:
    """One day of a rolling-average series."""

    day: date
    #: Trailing mean over the window ending on ``day``, or ``None`` when empty.
    average: float | None
    #: Check-ins inside that window — published so a thin stretch of the chart
    #: can be shown as thin rather than smoothed into confidence.
    entries: int
    enough_data: bool


@dataclass(frozen=True)
class RollingAverage:
    """The current rolling average for one measure and one window length."""

    measure: Measure
    window_days: int
    start: date
    end: date
    entries: int
    average: float | None
    #: ``entries >= MIN_ENTRIES_FOR_TREND``: enough to describe, not to trend.
    enough_data: bool
    holdback: Holdback | None
    series: tuple[RollingPoint, ...] = ()


def rolling_series(
    points: Sequence[LocalCheckIn],
    *,
    measure: Measure,
    window_days: int,
    today: date,
    days: int = ROLLING_SERIES_DAYS,
) -> tuple[RollingPoint, ...]:
    """The trailing ``window_days`` mean for each of the last ``days`` local days.

    Days with no check-ins in their window are returned with ``average=None``
    rather than dropped: a chart that silently joins two points across a
    fortnight of silence draws a line through a gap, and the gap is the truth.
    """
    series: list[RollingPoint] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        window = _in_window(points, start=day - timedelta(days=window_days - 1), end=day)
        values = [item.value(measure) for item in window]
        series.append(
            RollingPoint(
                day=day,
                average=_mean(values),
                entries=len(values),
                enough_data=len(values) >= MIN_ENTRIES_TO_PLOT,
            )
        )
    return tuple(series)


def rolling_average(
    points: Sequence[LocalCheckIn],
    *,
    measure: Measure,
    window_days: int,
    today: date,
    series_days: int = ROLLING_SERIES_DAYS,
) -> RollingAverage:
    """The rolling average over the ``window_days`` local days ending ``today``."""
    start = today - timedelta(days=window_days - 1)
    window = _in_window(points, start=start, end=today)
    values = [item.value(measure) for item in window]
    enough = len(values) >= MIN_ENTRIES_FOR_TREND
    return RollingAverage(
        measure=measure,
        window_days=window_days,
        start=start,
        end=today,
        entries=len(values),
        average=_mean(values),
        enough_data=enough,
        holdback=None if enough else Holdback.NOT_ENOUGH_DATA,
        series=rolling_series(
            points,
            measure=measure,
            window_days=window_days,
            today=today,
            days=series_days,
        ),
    )


@dataclass(frozen=True)
class TrendWindow:
    """One side of a trend comparison."""

    start: date
    end: date
    entries: int
    valence_mean: float | None
    energy_mean: float | None


@dataclass(frozen=True)
class MeasureTrend:
    """The direction of one measure, and everything that decision rests on."""

    measure: Measure
    direction: TrendDirection
    #: ``recent_mean - previous_mean``, in points; ``None`` when not computable.
    change: float | None
    #: Bootstrap interval for that change, when both windows have data.
    ci: tuple[float, float] | None
    recent_mean: float | None
    previous_mean: float | None
    recent_entries: int
    previous_entries: int
    holdback: Holdback | None


@dataclass(frozen=True)
class Trend:
    """Both measures' direction over the same pair of windows."""

    window_days: int
    recent: TrendWindow
    previous: TrendWindow
    measures: tuple[MeasureTrend, ...]
    enough_data: bool
    holdback: Holdback | None

    def of(self, measure: Measure) -> MeasureTrend:
        """One measure's trend. Raises ``KeyError`` only on a programming error."""
        for item in self.measures:
            if item.measure is measure:
                return item
        raise KeyError(measure)


def trend(
    points: Sequence[LocalCheckIn],
    *,
    today: date,
    window_days: int = ROLLING_WINDOWS[0],
) -> Trend:
    """Compare the last ``window_days`` local days with the ones before them.

    Both windows need :data:`MIN_ENTRIES_FOR_TREND` check-ins. That is stricter
    than it looks — fourteen check-ins is two full weeks — and it is the point:
    a direction claimed on six numbers is a coin toss wearing a lab coat.
    """
    recent_start = today - timedelta(days=window_days - 1)
    previous_start = recent_start - timedelta(days=window_days)
    previous_end = recent_start - timedelta(days=1)

    recent = _in_window(points, start=recent_start, end=today)
    previous = _in_window(points, start=previous_start, end=previous_end)

    recent_window = TrendWindow(
        start=recent_start,
        end=today,
        entries=len(recent),
        valence_mean=_mean([item.valence for item in recent]),
        energy_mean=_mean([item.energy for item in recent]),
    )
    previous_window = TrendWindow(
        start=previous_start,
        end=previous_end,
        entries=len(previous),
        valence_mean=_mean([item.valence for item in previous]),
        energy_mean=_mean([item.energy for item in previous]),
    )

    enough = len(recent) >= MIN_ENTRIES_FOR_TREND and len(previous) >= MIN_ENTRIES_FOR_TREND
    measures = tuple(
        _measure_trend(
            measure,
            recent=recent,
            previous=previous,
            enough=enough,
            window_days=window_days,
        )
        for measure in (Measure.VALENCE, Measure.ENERGY)
    )
    return Trend(
        window_days=window_days,
        recent=recent_window,
        previous=previous_window,
        measures=measures,
        enough_data=enough,
        holdback=None if enough else Holdback.NOT_ENOUGH_DATA,
    )


def _measure_trend(
    measure: Measure,
    *,
    recent: Sequence[LocalCheckIn],
    previous: Sequence[LocalCheckIn],
    enough: bool,
    window_days: int,
) -> MeasureTrend:
    """The direction rule for one measure, in one place."""
    recent_values = [item.value(measure) for item in recent]
    previous_values = [item.value(measure) for item in previous]
    recent_mean = _mean(recent_values)
    previous_mean = _mean(previous_values)

    if not enough or recent_mean is None or previous_mean is None:
        return MeasureTrend(
            measure=measure,
            direction=TrendDirection.NOT_ENOUGH_DATA,
            change=None,
            ci=None,
            recent_mean=recent_mean,
            previous_mean=previous_mean,
            recent_entries=len(recent_values),
            previous_entries=len(previous_values),
            holdback=Holdback.NOT_ENOUGH_DATA,
        )

    change = recent_mean - previous_mean
    interval = bootstrap_ci(
        recent_values,
        previous_values,
        key=f"trend:{measure.value}:{window_days}",
    )
    spans_zero = interval is not None and interval[0] <= 0.0 <= interval[1]

    if abs(change) < MIN_TREND_CHANGE or spans_zero:
        direction = TrendDirection.STEADY
        holdback: Holdback | None = (
            Holdback.SMALL_DIFFERENCE if abs(change) < MIN_TREND_CHANGE else Holdback.WITHIN_NOISE
        )
    else:
        direction = TrendDirection.RISING if change > 0 else TrendDirection.FALLING
        holdback = None

    return MeasureTrend(
        measure=measure,
        direction=direction,
        change=change,
        ci=interval,
        recent_mean=recent_mean,
        previous_mean=previous_mean,
        recent_entries=len(recent_values),
        previous_entries=len(previous_values),
        holdback=holdback,
    )


# --------------------------------------------------------------------------- #
# Factor associations                                                           #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FactorAssociation:
    """Days tagged with one factor, compared with days that were not.

    ``difference`` is ``tagged - untagged`` in points on the measure's own 1-5
    scale, which is the number a person can actually use. ``cohens_d`` is the
    same difference in pooled standard deviations, for the reader who wants to
    know whether 0.6 points is a lot; both are ``None`` when the comparison was
    not allowed to run.
    """

    factor: str
    measure: Measure
    tagged_n: int
    untagged_n: int
    tagged_mean: float | None
    untagged_mean: float | None
    difference: float | None
    ci: tuple[float, float] | None
    p_value: float | None
    cohens_d: float | None
    #: The alpha this comparison was judged at, already divided by the number of
    #: comparisons in its family.
    family_alpha: float
    reported: bool
    holdback: Holdback | None


@dataclass(frozen=True)
class FactorReport:
    """Every factor comparison, plus the correction they were judged under."""

    comparisons: int
    family_alpha: float
    associations: tuple[FactorAssociation, ...]

    def reported(self) -> tuple[FactorAssociation, ...]:
        """Only the associations this module is willing to put in a sentence.

        Ordered by the size of the difference, largest first, so a summary shows
        the strongest pattern first and a UI does not have to sort anything.
        """
        found = [item for item in self.associations if item.reported]
        found.sort(key=lambda item: abs(item.difference or 0.0), reverse=True)
        return tuple(found)


def factor_associations(
    points: Sequence[LocalCheckIn],
    *,
    factors: Sequence[str] = MOOD_FACTORS,
) -> FactorReport:
    """Compare tagged days with untagged days, for every factor and measure.

    The comparisons that *could* be run define the multiplicity correction, not
    the comparisons that turned out interesting — counting only the winners is
    how a noise-mining dashboard ends up certain of nothing. A factor nobody
    ever tagged still counts against the family size, because dropping it would
    make alpha depend on the answer.
    """
    comparisons = len(factors) * 2
    family_alpha = ALPHA / comparisons if comparisons else ALPHA

    associations: list[FactorAssociation] = []
    for factor in factors:
        tagged_points = [point for point in points if factor in point.factors]
        untagged_points = [point for point in points if factor not in point.factors]
        for measure in (Measure.VALENCE, Measure.ENERGY):
            associations.append(
                _one_association(
                    factor=factor,
                    measure=measure,
                    tagged=[point.value(measure) for point in tagged_points],
                    untagged=[point.value(measure) for point in untagged_points],
                    family_alpha=family_alpha,
                )
            )

    return FactorReport(
        comparisons=comparisons,
        family_alpha=family_alpha,
        associations=tuple(associations),
    )


def _one_association(
    *,
    factor: str,
    measure: Measure,
    tagged: Sequence[float],
    untagged: Sequence[float],
    family_alpha: float,
) -> FactorAssociation:
    """One comparison, with every gate applied in the order that costs least.

    The order matters for more than speed: the resampling only runs once the
    sample sizes and the effect size already justify a sentence, so a dashboard
    of pure noise never asks the permutation test sixteen questions.
    """
    key = f"factor:{factor}:{measure.value}"
    tagged_n, untagged_n = len(tagged), len(untagged)
    tagged_mean, untagged_mean = _mean(list(tagged)), _mean(list(untagged))

    def build(
        *,
        difference: float | None = None,
        ci: tuple[float, float] | None = None,
        p_value: float | None = None,
        d: float | None = None,
        reported: bool = False,
        holdback: Holdback | None = None,
    ) -> FactorAssociation:
        return FactorAssociation(
            factor=factor,
            measure=measure,
            tagged_n=tagged_n,
            untagged_n=untagged_n,
            tagged_mean=tagged_mean,
            untagged_mean=untagged_mean,
            difference=difference,
            ci=ci,
            p_value=p_value,
            cohens_d=d,
            family_alpha=family_alpha,
            reported=reported,
            holdback=holdback,
        )

    if tagged_n == 0 and untagged_n == 0:
        return build(holdback=Holdback.NOT_ENOUGH_DATA)
    if tagged_n < MIN_GROUP_N or untagged_n < MIN_GROUP_N:
        return build(holdback=Holdback.GROUP_TOO_SMALL)
    if tagged_mean is None or untagged_mean is None:
        return build(holdback=Holdback.NOT_ENOUGH_DATA)

    difference = tagged_mean - untagged_mean
    if abs(difference) < MIN_EFFECT_POINTS:
        return build(difference=difference, holdback=Holdback.SMALL_DIFFERENCE)

    p_value = permutation_p_value(list(tagged), list(untagged), key=key)
    if p_value > family_alpha:
        return build(difference=difference, p_value=p_value, holdback=Holdback.WITHIN_NOISE)

    interval = bootstrap_ci(list(tagged), list(untagged), key=key)
    if interval is not None and interval[0] <= 0.0 <= interval[1]:
        return build(
            difference=difference,
            ci=interval,
            p_value=p_value,
            d=_cohens_d(list(tagged), list(untagged)),
            holdback=Holdback.WITHIN_NOISE,
        )

    return build(
        difference=difference,
        ci=interval,
        p_value=p_value,
        d=_cohens_d(list(tagged), list(untagged)),
        reported=True,
    )


# --------------------------------------------------------------------------- #
# Time-of-day and weekday patterns                                              #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GroupBucket:
    """One bucket of a grouping, with both measures' means for the data table."""

    id: str
    entries: int
    valence_mean: float | None
    energy_mean: float | None
    #: ``entries >= MIN_GROUP_N``: only these buckets may be named in a sentence.
    enough_data: bool


@dataclass(frozen=True)
class GroupPattern:
    """A grouping (part of day, or weekday) and what it may be said to show."""

    kind: PatternKind
    measure: Measure
    buckets: tuple[GroupBucket, ...]
    #: ``highest_mean - lowest_mean`` over the buckets that may be named.
    spread: float | None
    lowest: GroupBucket | None
    highest: GroupBucket | None
    p_value: float | None
    family_alpha: float
    reported: bool
    holdback: Holdback | None


def group_pattern(
    points: Sequence[LocalCheckIn],
    *,
    kind: PatternKind,
    measure: Measure = Measure.VALENCE,
) -> GroupPattern:
    """Bucket the check-ins and test whether the buckets differ at all.

    One omnibus permutation test per grouping, at alpha/2 (the two families that
    exist), and only then are the extreme buckets named — and only if each of
    them has :data:`MIN_GROUP_N` check-ins and the spread between them clears
    :data:`MIN_EFFECT_POINTS`. The mean of a bucket that is too small is still
    returned for the data table, because a table of somebody's own numbers is
    not a claim; naming it in a sentence is.
    """
    ids = TIME_OF_DAY_BUCKETS if kind is PatternKind.TIME_OF_DAY else WEEKDAY_IDS
    grouped: dict[str, list[LocalCheckIn]] = {identifier: [] for identifier in ids}
    for point in points:
        key = (
            time_of_day_bucket(point.hour)
            if kind is PatternKind.TIME_OF_DAY
            else ids[point.weekday]
        )
        grouped[key].append(point)

    buckets = tuple(
        GroupBucket(
            id=identifier,
            entries=len(members),
            valence_mean=_mean([float(item.valence) for item in members]),
            energy_mean=_mean([float(item.energy) for item in members]),
            enough_data=len(members) >= MIN_GROUP_N,
        )
        for identifier, members in grouped.items()
    )

    named = [bucket for bucket in buckets if bucket.enough_data]
    values = [point.value(measure) for point in points]
    labels = [
        (time_of_day_bucket(point.hour) if kind is PatternKind.TIME_OF_DAY else ids[point.weekday])
        for point in points
    ]
    family_alpha = ALPHA / 2
    p_value = permutation_f_p_value(values, labels, key=f"{kind.value}:{measure.value}")

    if len(named) < 2:
        # Not enough buckets to compare at all — a different question from "the
        # buckets do not differ", which is what the rest of the function answers.
        too_thin = Holdback.NOT_ENOUGH_DATA if len(points) == 0 else Holdback.GROUP_TOO_SMALL
        return GroupPattern(
            kind=kind,
            measure=measure,
            buckets=buckets,
            spread=None,
            lowest=None,
            highest=None,
            p_value=p_value,
            family_alpha=family_alpha,
            reported=False,
            holdback=too_thin,
        )

    lowest = min(named, key=lambda bucket: _bucket_value(bucket, measure))
    highest = max(named, key=lambda bucket: _bucket_value(bucket, measure))
    spread = _bucket_value(highest, measure) - _bucket_value(lowest, measure)

    holdback: Holdback | None
    reported: bool
    if p_value is None or p_value > family_alpha:
        holdback = Holdback.WITHIN_NOISE
        reported = False
    elif spread < MIN_EFFECT_POINTS:
        holdback = Holdback.SMALL_DIFFERENCE
        reported = False
    else:
        holdback = None
        reported = True

    return GroupPattern(
        kind=kind,
        measure=measure,
        buckets=buckets,
        # The extremes and the spread are withheld with the claim, exactly as a
        # held-back association withholds its difference. The buckets keep their
        # own means: a table of somebody's numbers is not a pattern, and a chart
        # of them is honest as long as no sentence names a winner.
        spread=spread if reported else None,
        lowest=lowest if reported else None,
        highest=highest if reported else None,
        p_value=p_value,
        family_alpha=family_alpha,
        reported=reported,
        holdback=holdback,
    )


def _bucket_value(bucket: GroupBucket, measure: Measure) -> float:
    """The bucket's mean for ``measure``; a missing mean sorts as the scale's middle.

    Sorting on ``None`` would raise, and dropping the bucket would hide a real
    (if unmeasurable) group from the extremes search. The midpoint is neutral:
    it can only lose a comparison against a bucket that has a number.
    """
    value = bucket.valence_mean if measure is Measure.VALENCE else bucket.energy_mean
    return value if value is not None else 3.0


# --------------------------------------------------------------------------- #
# Weeks                                                                         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WeekCount:
    """One factor or emotion tag, and how often it appeared in the week."""

    id: str
    count: int


@dataclass(frozen=True)
class WeekBucket:
    """One natural (Monday-Sunday) week of check-ins.

    Natural weeks rather than "the last seven days": a summary that says "this
    week" must mean the week the person lived through, and a rolling seven-day
    window would move while they read it.
    """

    start: date
    end: date
    entries: int
    days_with_check_in: int
    valence_mean: float | None
    energy_mean: float | None
    factors: tuple[WeekCount, ...]
    emotions: tuple[WeekCount, ...]
    #: ``entries >= MIN_ENTRIES_FOR_TREND`` — enough to describe the week.
    enough_data: bool


def week_start(day: date) -> date:
    """The Monday of ``day``'s week."""
    return day - timedelta(days=day.weekday())


def weekly_buckets(
    points: Sequence[LocalCheckIn],
    *,
    today: date,
    weeks: int = DEFAULT_WEEKS,
) -> tuple[WeekBucket, ...]:
    """The last ``weeks`` natural weeks, oldest first, including the current one.

    A week with no check-ins is still returned. Silently omitting an empty week
    would turn "you checked in twice in six weeks" into "your last two weeks",
    which is a different and less kind claim.
    """
    this_monday = week_start(today)
    buckets: list[WeekBucket] = []
    for offset in range(weeks - 1, -1, -1):
        start = this_monday - timedelta(days=7 * offset)
        end = start + timedelta(days=6)
        members = _in_window(points, start=start, end=end)
        buckets.append(
            WeekBucket(
                start=start,
                end=end,
                entries=len(members),
                days_with_check_in=len({member.day for member in members}),
                valence_mean=_mean([float(member.valence) for member in members]),
                energy_mean=_mean([float(member.energy) for member in members]),
                factors=_counts(Counter(tag for member in members for tag in member.factors)),
                emotions=_counts(Counter(tag for member in members for tag in member.emotions)),
                enough_data=len(members) >= MIN_ENTRIES_FOR_TREND,
            )
        )
    return tuple(buckets)


def _counts(counter: Counter[str]) -> tuple[WeekCount, ...]:
    """Most frequent first, ties broken by name so a table does not reshuffle."""
    return tuple(
        WeekCount(id=identifier, count=count)
        for identifier, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    )


# --------------------------------------------------------------------------- #
# The report                                                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InsightsReport:
    """Everything one ``/insights/patterns`` call can say, in one object.

    ``enough_data`` is the surface-level answer to "is there anything here at
    all"; every section also carries its own, because a person with a month of
    check-ins and two tags deserves the trend and the honest "not enough data"
    for the factors — not one flag that hides both.
    """

    today: date
    timezone: str
    timezone_fallback_used: bool
    window_days: int
    total_check_ins: int
    considered_check_ins: int
    enough_data: bool
    rolling: tuple[RollingAverage, ...]
    trend: Trend
    factors: FactorReport
    time_of_day: GroupPattern
    weekday: GroupPattern


def build_insights(
    check_ins: Iterable[CheckIn],
    *,
    zone: ZoneInfo,
    timezone_fallback_used: bool = False,
    today: date,
    window_days: int = 60,
    total_check_ins: int | None = None,
) -> InsightsReport:
    """Build the whole report from raw check-ins.

    ``window_days`` bounds how far back the associations and patterns look; the
    rolling averages and the trend use their own window lengths inside it.
    ``total_check_ins`` lets the API report how many check-ins exist in all,
    which is how the UI can say "3 of your 3 check-ins" instead of implying
    that three is everything the person has ever written.
    """
    if window_days < 1 or window_days > MAX_WINDOW_DAYS:
        raise ValueError(f"window_days must be between 1 and {MAX_WINDOW_DAYS}")

    prepared = prepare(check_ins, zone=zone)
    start = today - timedelta(days=window_days - 1)
    considered = [point for point in prepared if point.day >= start]

    return InsightsReport(
        today=today,
        timezone=getattr(zone, "key", None) or str(zone),
        timezone_fallback_used=timezone_fallback_used,
        window_days=window_days,
        total_check_ins=len(prepared) if total_check_ins is None else total_check_ins,
        considered_check_ins=len(considered),
        enough_data=len(considered) >= MIN_ENTRIES_FOR_TREND,
        rolling=tuple(
            rolling_average(
                considered,
                measure=measure,
                window_days=length,
                today=today,
                series_days=min(window_days, ROLLING_SERIES_DAYS),
            )
            for length in ROLLING_WINDOWS
            for measure in (Measure.VALENCE, Measure.ENERGY)
        ),
        trend=trend(considered, today=today),
        factors=factor_associations(considered),
        time_of_day=group_pattern(considered, kind=PatternKind.TIME_OF_DAY),
        weekday=group_pattern(considered, kind=PatternKind.WEEKDAY),
    )


def build_weekly(
    check_ins: Iterable[CheckIn],
    *,
    zone: ZoneInfo,
    timezone_fallback_used: bool = False,
    today: date,
    weeks: int = DEFAULT_WEEKS,
    window_days: int = 60,
    total_check_ins: int | None = None,
) -> WeeklyReport:
    """Build the weekly summary: natural weeks, plus the trend across them."""
    if weeks < 1 or weeks > MAX_WEEKS:
        raise ValueError(f"weeks must be between 1 and {MAX_WEEKS}")

    prepared = prepare(check_ins, zone=zone)
    start = week_start(today) - timedelta(days=7 * (weeks - 1))
    considered = [point for point in prepared if point.day >= start]

    return WeeklyReport(
        today=today,
        timezone=getattr(zone, "key", None) or str(zone),
        timezone_fallback_used=timezone_fallback_used,
        weeks=weeks,
        window_days=window_days,
        total_check_ins=len(prepared) if total_check_ins is None else total_check_ins,
        considered_check_ins=len(considered),
        enough_data=len(considered) >= MIN_ENTRIES_FOR_TREND,
        weekly=weekly_buckets(considered, today=today, weeks=weeks),
        trend=trend(considered, today=today, window_days=7),
        rolling=tuple(
            rolling_average(
                considered,
                measure=measure,
                window_days=length,
                today=today,
                series_days=min(7 * weeks, ROLLING_SERIES_DAYS),
            )
            for length in ROLLING_WINDOWS
            for measure in (Measure.VALENCE, Measure.ENERGY)
        ),
    )


@dataclass(frozen=True)
class WeeklyReport:
    """The weekly surface: natural weeks, a 7-day trend, and the rolling means."""

    today: date
    timezone: str
    timezone_fallback_used: bool
    weeks: int
    window_days: int
    total_check_ins: int
    considered_check_ins: int
    enough_data: bool
    weekly: tuple[WeekBucket, ...]
    trend: Trend
    rolling: tuple[RollingAverage, ...] = field(default=())


# --------------------------------------------------------------------------- #
# Sentences                                                                     #
# --------------------------------------------------------------------------- #
#
# The words come from ``app/content/insights/*.json`` — reviewed, translated,
# and scanned for causal vocabulary by a test. Nothing in this section builds a
# sentence out of an f-string, and no sentence may mention a cause: the
# templates only ever describe a difference between two sets of the person's own
# days. Placeholders are numbers and bucket ids; the ids are looked up in the
# locale file so "morning" is never shown to somebody reading Bengali.


def _points(value: float | None, strings: InsightsStrings) -> str:
    """A difference or mean in points, one decimal, in the locale's own digits."""
    if value is None:
        return strings.localise_number(0.0, digits=1)
    return strings.localise_number(abs(value), digits=1)


def _direction_word(change: float, strings: InsightsStrings) -> str:
    """``higher``/``lower`` — never ``better``/``worse`` (see module docstring)."""
    return strings.string("word.higher") if change >= 0 else strings.string("word.lower")


def trend_sentence(item: MeasureTrend, *, window_days: int, strings: InsightsStrings) -> str:
    """One sentence about one measure's direction, or the honest "not yet"."""
    if item.direction is TrendDirection.NOT_ENOUGH_DATA:
        return strings.render(
            "trend.not_enough_data",
            {
                "measure": strings.string(f"measure.{item.measure.value}"),
                "window_days": strings.localise_number(float(window_days), digits=0),
                "needed": strings.localise_number(float(MIN_ENTRIES_FOR_TREND), digits=0),
            },
        )
    change = item.change or 0.0
    template = {
        TrendDirection.RISING: "trend.rising",
        TrendDirection.FALLING: "trend.falling",
        TrendDirection.STEADY: "trend.steady",
        TrendDirection.NOT_ENOUGH_DATA: "trend.not_enough_data",
    }[item.direction]
    if template == "trend.steady":
        return strings.render(
            template,
            {
                "measure": strings.string(f"measure.{item.measure.value}"),
                "window_days": strings.localise_number(float(window_days), digits=0),
            },
        )
    return strings.render(
        template,
        {
            "measure": strings.string(f"measure.{item.measure.value}"),
            "window_days": strings.localise_number(float(window_days), digits=0),
            "change": _points(change, strings),
            "direction": _direction_word(change, strings),
        },
    )


def factor_sentence(item: FactorAssociation, *, strings: InsightsStrings) -> str:
    """The associative sentence the brief asks for, with both sample sizes in it.

    "On days you tagged sleep, your mood was about 0.8 lower on average
    (9 check-ins vs 21)." The sample sizes are inside the sentence rather than
    in a footnote because a difference without its n is the most persuasive
    lie a dashboard can tell.
    """
    difference = item.difference or 0.0
    return strings.render(
        "factor.reported",
        {
            "factor": strings.string(f"factor.{item.factor}"),
            "measure": strings.string(f"measure.{item.measure.value}"),
            "difference": _points(difference, strings),
            "direction": _direction_word(difference, strings),
            "tagged_n": strings.localise_number(float(item.tagged_n), digits=0),
            "untagged_n": strings.localise_number(float(item.untagged_n), digits=0),
        },
    )


def pattern_sentence(item: GroupPattern, *, strings: InsightsStrings) -> str:
    """The time-of-day or weekday sentence, or why there is not one."""
    if not item.reported or item.lowest is None or item.highest is None:
        kind = strings.string(f"pattern_kind.{item.kind.value}")
        if item.holdback is Holdback.NOT_ENOUGH_DATA:
            return strings.render(
                "pattern.not_enough_data",
                {
                    "kind": kind,
                    "needed": strings.localise_number(float(MIN_GROUP_N), digits=0),
                },
            )
        return strings.render("pattern.none", {"kind": kind})
    prefix = item.kind.value
    return strings.render(
        f"pattern.{prefix}.reported",
        {
            "measure": strings.string(f"measure.{item.measure.value}"),
            "lowest": strings.string(f"{prefix}.{item.lowest.id}"),
            "lowest_mean": _points(_bucket_value(item.lowest, item.measure), strings),
            "lowest_n": strings.localise_number(float(item.lowest.entries), digits=0),
            "highest": strings.string(f"{prefix}.{item.highest.id}"),
            "highest_mean": _points(_bucket_value(item.highest, item.measure), strings),
            "highest_n": strings.localise_number(float(item.highest.entries), digits=0),
        },
    )


def weekly_sentences(report: WeeklyReport, *, strings: InsightsStrings) -> list[str]:
    """The localised weekly summary: one headline, one line per week, one trend.

    Generated from templates only. There is no model in this path and no
    free text from the person's check-ins reaches a sentence, so what a user
    reads here is exactly what a reviewer read in the locale file.
    """
    sentences: list[str] = []
    total = report.considered_check_ins

    if total == 0:
        sentences.append(
            strings.render(
                "weekly.no_check_ins",
                {"weeks": strings.localise_number(float(report.weeks), digits=0)},
            )
        )
        return sentences

    if not report.enough_data:
        sentences.append(
            strings.render(
                "weekly.not_enough_data",
                {
                    "entries": strings.localise_number(float(total), digits=0),
                    "needed": strings.localise_number(float(MIN_ENTRIES_FOR_TREND), digits=0),
                },
            )
        )
    else:
        sentences.append(
            strings.render(
                "weekly.heading",
                {
                    "weeks": strings.localise_number(float(report.weeks), digits=0),
                    "entries": strings.localise_number(float(total), digits=0),
                },
            )
        )

    for bucket in report.weekly:
        sentences.append(week_sentence(bucket, strings=strings))

    sentences.append(
        trend_sentence(
            report.trend.of(Measure.VALENCE),
            window_days=report.trend.window_days,
            strings=strings,
        )
    )
    return sentences


def week_sentence(bucket: WeekBucket, *, strings: InsightsStrings) -> str:
    """One line for one week — including the weeks with nothing in them."""
    week = strings.localise_date(bucket.start)
    if bucket.entries == 0:
        return strings.render("weekly.week_empty", {"week_start": week})
    values: Mapping[str, str] = {
        "week_start": week,
        "entries": strings.localise_number(float(bucket.entries), digits=0),
        "days": strings.localise_number(float(bucket.days_with_check_in), digits=0),
    }
    if not bucket.enough_data or bucket.valence_mean is None or bucket.energy_mean is None:
        return strings.render("weekly.week_thin", dict(values))
    return strings.render(
        "weekly.week_line",
        dict(
            values,
            valence=strings.localise_number(bucket.valence_mean, digits=1),
            energy=strings.localise_number(bucket.energy_mean, digits=1),
        ),
    )


def pattern_sentences(report: InsightsReport, *, strings: InsightsStrings) -> list[str]:
    """The localised summary for ``/insights/patterns``.

    Order is the order somebody reads: what the last week looks like, whether it
    moved, what the tags are associated with, then the shape of the days. When
    there is not enough data the whole thing is one sentence saying so — a list
    of four "not enough data" panels is not a summary, it is a shrug repeated.
    """
    if report.considered_check_ins == 0:
        return [
            strings.render(
                "patterns.no_check_ins",
                {"days": strings.localise_number(float(report.window_days), digits=0)},
            )
        ]
    if not report.enough_data:
        return [
            strings.render(
                "patterns.not_enough_data",
                {
                    "entries": strings.localise_number(
                        float(report.considered_check_ins), digits=0
                    ),
                    "needed": strings.localise_number(float(MIN_ENTRIES_FOR_TREND), digits=0),
                    "days": strings.localise_number(float(report.window_days), digits=0),
                },
            )
        ]

    sentences: list[str] = []
    for window in ROLLING_WINDOWS:
        for measure in (Measure.VALENCE, Measure.ENERGY):
            rolling = _rolling_for(report, measure=measure, window_days=window)
            if rolling is None or rolling.average is None:
                continue
            sentences.append(
                strings.render(
                    "rolling.average",
                    {
                        "measure": strings.string(f"measure.{measure.value}"),
                        "window_days": strings.localise_number(float(window), digits=0),
                        "average": strings.localise_number(rolling.average, digits=1),
                        "scale_max": strings.localise_number(float(SCALE_MAX), digits=0),
                        "entries": strings.localise_number(float(rolling.entries), digits=0),
                    },
                )
            )

    sentences.append(
        trend_sentence(
            report.trend.of(Measure.VALENCE),
            window_days=report.trend.window_days,
            strings=strings,
        )
    )

    reported = report.factors.reported()
    if reported:
        sentences.extend(factor_sentence(item, strings=strings) for item in reported)
    else:
        sentences.append(
            strings.render(
                "factor.none",
                {"needed": strings.localise_number(float(MIN_GROUP_N), digits=0)},
            )
        )

    sentences.append(pattern_sentence(report.time_of_day, strings=strings))
    sentences.append(pattern_sentence(report.weekday, strings=strings))
    return sentences


def summary_sentences(report: InsightsReport, *, strings: InsightsStrings) -> list[str]:
    """Alias kept for callers that think in "the summary" rather than "patterns"."""
    return pattern_sentences(report, strings=strings)


def _rolling_for(
    report: InsightsReport, *, measure: Measure, window_days: int
) -> RollingAverage | None:
    for item in report.rolling:
        if item.measure is measure and item.window_days == window_days:
            return item
    return None


def week_trend_sentence(report: WeeklyReport, *, strings: InsightsStrings) -> str:
    """The week-over-week line, kept separate so a UI can show it as a headline."""
    return trend_sentence(
        report.trend.of(Measure.VALENCE),
        window_days=report.trend.window_days,
        strings=strings,
    )


def local_datetime(instant: datetime, zone: ZoneInfo) -> datetime:
    """The check-in's own local time — the seam the hour bucket is read from."""
    aware = instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)
    return aware.astimezone(zone)


def local_day(instant: datetime, zone: ZoneInfo) -> date:
    """Re-exported for symmetry with :func:`local_datetime` (see ``streak``)."""
    return local_date(instant, zone)


def today_in(zone: ZoneInfo, *, now: datetime) -> date:
    """Re-exported clock seam, so the API and the streak agree on "today"."""
    return local_today(now, zone)
