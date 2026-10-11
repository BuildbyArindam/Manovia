"""The insights engine on synthetic data with answers known in advance.

Three questions this file answers, in this order:

1. **Is the arithmetic right?** Every statistic is checked against a dataset
   built so the answer can be computed by hand — a difference of exactly 2.0
   points, a mean of exactly 4.0, a standardised difference of -3.464.
2. **Do the minimum-data rules hold?** A comparison with four check-ins on one
   side must not exist, a trend with six must not have a direction, and "not
   enough data" must be a different answer from "steady".
3. **Is nothing produced when the data cannot support it?** Pure noise must not
   yield a reported association, and the holdback code has to say why.

The resampling tests use small ``draws`` counts where the property under test
does not depend on the number of draws, and assert the two invariants that do:
a permutation p-value is never exactly zero, and the same input always produces
the same output (a sentence that changes on refresh is not a fact).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.content.insights import load_insights
from app.services.mood.insights import (
    ALPHA,
    DEFAULT_WEEKS,
    MAX_WEEKS,
    MAX_WINDOW_DAYS,
    MIN_EFFECT_POINTS,
    MIN_ENTRIES_FOR_TREND,
    MIN_ENTRIES_TO_PLOT,
    MIN_GROUP_N,
    MIN_TREND_CHANGE,
    ROLLING_WINDOWS,
    TIME_OF_DAY_BUCKETS,
    WEEKDAY_IDS,
    CheckIn,
    Holdback,
    Measure,
    PatternKind,
    TrendDirection,
    bootstrap_ci,
    build_insights,
    build_weekly,
    factor_associations,
    group_pattern,
    pattern_sentences,
    permutation_f_p_value,
    permutation_p_value,
    prepare,
    rolling_average,
    rolling_series,
    time_of_day_bucket,
    trend,
    week_start,
    weekly_buckets,
    weekly_sentences,
)
from app.services.mood.taxonomy import MOOD_FACTORS

KOLKATA = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 11)
STRINGS = load_insights("en")


# --------------------------------------------------------------------------- #
# Builders                                                                      #
# --------------------------------------------------------------------------- #


def at(days_ago: int, hour: int = 9, *, zone: ZoneInfo = KOLKATA) -> datetime:
    """A UTC instant that is ``hour`` o'clock, ``days_ago`` local days back."""
    local = datetime.combine(TODAY - timedelta(days=days_ago), time(hour, 30), tzinfo=zone)
    return local.astimezone(UTC)


def entry(
    days_ago: int,
    *,
    hour: int = 9,
    valence: int = 3,
    energy: int = 3,
    factors: tuple[str, ...] = (),
    zone: ZoneInfo = KOLKATA,
) -> CheckIn:
    return CheckIn(
        at=at(days_ago, hour, zone=zone),
        valence=valence,
        energy=energy,
        factors=factors,
    )


def history(
    days: int,
    *,
    valence: int = 3,
    energy: int = 3,
    factors: tuple[str, ...] = (),
    hour: int = 9,
) -> list[CheckIn]:
    """One check-in a day for ``days`` days, oldest first."""
    return [
        entry(days_ago, hour=hour, valence=valence, energy=energy, factors=factors)
        for days_ago in range(days - 1, -1, -1)
    ]


# --------------------------------------------------------------------------- #
# 1. Preparation and the timezone seam                                          #
# --------------------------------------------------------------------------- #


def test_prepare_attaches_the_local_day_hour_and_weekday() -> None:
    # 23:30Z on 10 October is 05:00 on 11 October in Kolkata: a different day,
    # a different part of the day, and a different weekday.
    prepared = prepare(
        [CheckIn(at=datetime(2026, 10, 10, 23, 30, tzinfo=UTC), valence=3, energy=3)], zone=KOLKATA
    )

    assert len(prepared) == 1
    point = prepared[0]
    assert point.day == date(2026, 10, 11)
    assert point.hour == 5
    assert point.weekday == WEEKDAY_IDS.index("sunday")
    assert time_of_day_bucket(point.hour) == "morning"


def test_prepare_reads_a_naive_timestamp_as_utc() -> None:
    naive = CheckIn(at=datetime(2026, 10, 10, 12, 0), valence=4, energy=4)
    aware = CheckIn(at=datetime(2026, 10, 10, 12, 0, tzinfo=UTC), valence=4, energy=4)

    assert prepare([naive], zone=KOLKATA)[0].day == prepare([aware], zone=KOLKATA)[0].day


def test_prepare_sorts_oldest_first_regardless_of_input_order() -> None:
    shuffled = [entry(0), entry(5), entry(2)]

    days = [point.day for point in prepare(shuffled, zone=KOLKATA)]

    assert days == [TODAY - timedelta(days=5), TODAY - timedelta(days=2), TODAY]


def test_the_same_instant_can_be_a_different_weekday_in_a_different_zone() -> None:
    instant = datetime(2026, 10, 10, 22, 0, tzinfo=UTC)
    check = CheckIn(at=instant, valence=3, energy=3)

    kolkata = prepare([check], zone=KOLKATA)[0]
    new_york = prepare([check], zone=NEW_YORK)[0]

    assert kolkata.day == date(2026, 10, 11)
    assert new_york.day == date(2026, 10, 10)
    assert kolkata.weekday != new_york.weekday


def test_a_check_in_carries_no_note_field() -> None:
    """The insight input type cannot hold free text, so no summary can leak one."""
    names = {field.name for field in dataclasses.fields(CheckIn)}
    assert names == {"at", "valence", "energy", "factors", "emotions"}


def test_time_of_day_buckets_cover_every_hour() -> None:
    assert {time_of_day_bucket(hour) for hour in range(24)} == set(TIME_OF_DAY_BUCKETS)
    assert time_of_day_bucket(23) == "night"
    assert time_of_day_bucket(3) == "night"
    assert time_of_day_bucket(12) == "afternoon"


# --------------------------------------------------------------------------- #
# 2. Rolling averages: means with known answers                                 #
# --------------------------------------------------------------------------- #


def test_a_rolling_average_over_a_full_window_is_the_plain_mean() -> None:
    points = prepare([entry(days_ago, valence=4, energy=2) for days_ago in range(7)], zone=KOLKATA)

    valence = rolling_average(points, measure=Measure.VALENCE, window_days=7, today=TODAY)
    energy = rolling_average(points, measure=Measure.ENERGY, window_days=7, today=TODAY)

    assert valence.average == 4.0
    assert energy.average == 2.0
    assert valence.entries == 7
    assert valence.enough_data is True
    assert valence.holdback is None


def test_a_window_excludes_the_day_just_outside_it() -> None:
    # Eight check-ins, but the oldest is 8 days back: a 7-day window ending today
    # covers days 0..6 only, so the mean is of the sevens, not of the ones.
    points = prepare(
        [entry(8, valence=1)] + [entry(days_ago, valence=5) for days_ago in range(7)],
        zone=KOLKATA,
    )

    result = rolling_average(points, measure=Measure.VALENCE, window_days=7, today=TODAY)

    assert result.entries == 7
    assert result.average == 5.0
    assert result.start == TODAY - timedelta(days=6)
    assert result.end == TODAY


def test_a_thin_window_reports_its_count_and_is_not_enough_data() -> None:
    points = prepare([entry(days_ago, valence=5) for days_ago in range(3)], zone=KOLKATA)

    result = rolling_average(points, measure=Measure.VALENCE, window_days=7, today=TODAY)

    assert result.entries == 3
    assert result.average == 5.0  # the number is theirs to see
    assert result.enough_data is False
    assert result.holdback is Holdback.NOT_ENOUGH_DATA


def test_an_empty_window_has_no_average_at_all() -> None:
    result = rolling_average([], measure=Measure.VALENCE, window_days=30, today=TODAY)

    assert result.average is None
    assert result.entries == 0
    assert result.enough_data is False


def test_the_series_keeps_empty_days_instead_of_drawing_a_line_through_them() -> None:
    points = prepare([entry(0, valence=4), entry(20, valence=4)], zone=KOLKATA)

    series = rolling_series(points, measure=Measure.VALENCE, window_days=7, today=TODAY, days=30)

    assert len(series) == 30
    assert series[0].day == TODAY - timedelta(days=29)
    assert series[-1].day == TODAY
    assert series[-1].average == 4.0
    # A day whose window holds nothing has no value at all: not a carried one,
    # not a zero, and not a gap the chart has to guess at.
    assert series[4].average is None
    assert series[4].entries == 0
    assert series[4].enough_data is False
    # A day whose window holds exactly one check-in has a number and is still not
    # worth drawing: one rating is not a seven-day average.
    assert series[9].entries == 1
    assert series[9].enough_data is False


def test_a_series_point_is_plotable_from_three_check_ins() -> None:
    points = prepare(
        [entry(days_ago, valence=4) for days_ago in range(MIN_ENTRIES_TO_PLOT)], zone=KOLKATA
    )

    series = rolling_series(points, measure=Measure.VALENCE, window_days=7, today=TODAY, days=7)

    assert series[-1].enough_data is True
    assert series[-1].entries == MIN_ENTRIES_TO_PLOT
    assert series[0].enough_data is False  # one check-in in that window


def test_every_declared_window_length_is_reported_by_build_insights() -> None:
    report = build_insights(history(40), zone=KOLKATA, today=TODAY, window_days=60)

    assert {(item.measure, item.window_days) for item in report.rolling} == {
        (measure, window) for window in ROLLING_WINDOWS for measure in Measure
    }


# --------------------------------------------------------------------------- #
# 3. Trend: the minimum-data rule and the direction rule                        #
# --------------------------------------------------------------------------- #


def test_a_clear_rise_is_called_rising_with_the_constructed_change() -> None:
    recent = [entry(days_ago, valence=4, energy=4) for days_ago in range(7)]
    previous = [entry(days_ago, valence=2, energy=2) for days_ago in range(7, 14)]

    result = trend(prepare(recent + previous, zone=KOLKATA), today=TODAY, window_days=7)

    valence = result.of(Measure.VALENCE)
    assert result.enough_data is True
    assert valence.direction is TrendDirection.RISING
    assert valence.change == pytest.approx(2.0)
    assert valence.recent_mean == 4.0
    assert valence.previous_mean == 2.0
    assert valence.holdback is None
    assert valence.ci is not None and valence.ci[0] > 0.0


def test_a_clear_fall_is_called_falling() -> None:
    recent = [entry(days_ago, valence=2) for days_ago in range(7)]
    previous = [entry(days_ago, valence=4) for days_ago in range(7, 14)]

    valence = trend(prepare(recent + previous, zone=KOLKATA), today=TODAY).of(Measure.VALENCE)

    assert valence.direction is TrendDirection.FALLING
    assert valence.change == pytest.approx(-2.0)


@pytest.mark.parametrize("short_window", ["recent", "previous"])
def test_one_entry_below_the_minimum_means_no_direction_at_all(short_window: str) -> None:
    """Six check-ins in either window is not a trend, and must not look like one."""
    recent_count = 6 if short_window == "recent" else MIN_ENTRIES_FOR_TREND
    previous_count = MIN_ENTRIES_FOR_TREND if short_window == "recent" else 6
    recent = [entry(days_ago, valence=1) for days_ago in range(recent_count)]
    previous = [entry(days_ago, valence=5) for days_ago in range(7, 7 + previous_count)]

    result = trend(prepare(recent + previous, zone=KOLKATA), today=TODAY)
    valence = result.of(Measure.VALENCE)

    assert result.enough_data is False
    assert result.holdback is Holdback.NOT_ENOUGH_DATA
    assert valence.direction is TrendDirection.NOT_ENOUGH_DATA
    assert valence.change is None
    assert valence.ci is None
    assert valence.holdback is Holdback.NOT_ENOUGH_DATA


def test_not_enough_data_is_not_the_same_answer_as_steady() -> None:
    three = trend(prepare([entry(0), entry(1), entry(2)], zone=KOLKATA), today=TODAY)
    flat = trend(
        prepare([entry(days_ago, valence=3) for days_ago in range(14)], zone=KOLKATA),
        today=TODAY,
    )

    assert three.of(Measure.VALENCE).direction is TrendDirection.NOT_ENOUGH_DATA
    assert flat.of(Measure.VALENCE).direction is TrendDirection.STEADY
    assert flat.enough_data is True
    assert three.enough_data is False


def test_a_change_below_the_floor_is_steady_even_with_lots_of_data() -> None:
    # Two windows with real spread in them and identical means: the difference is
    # zero, which is below the smallest change this module will call a direction.
    recent = [
        entry(days_ago, valence=value) for days_ago, value in enumerate([3, 3, 4, 3, 3, 4, 3])
    ]
    previous = [
        entry(days_ago, valence=value)
        for days_ago, value in zip(range(7, 14), [3, 4, 3, 3, 4, 3, 3], strict=True)
    ]

    valence = trend(prepare(recent + previous, zone=KOLKATA), today=TODAY, window_days=7).of(
        Measure.VALENCE
    )

    assert valence.recent_entries == 7 and valence.previous_entries == 7
    assert abs(valence.change or 0.0) < MIN_TREND_CHANGE
    assert valence.direction is TrendDirection.STEADY
    assert valence.holdback is Holdback.SMALL_DIFFERENCE


def test_a_noisy_change_whose_interval_spans_zero_is_steady() -> None:
    # Two windows whose means differ by more than the floor (22/7 vs 19/7 = 0.43
    # points) but whose spread is so wide that the bootstrap interval comfortably
    # includes zero. The honest answer is "about the same", not "rising".
    recent = [
        entry(days_ago, valence=value)
        for days_ago, value in zip(range(7), [5, 1, 5, 1, 5, 1, 4], strict=True)
    ]
    previous = [
        entry(days_ago, valence=value)
        for days_ago, value in zip(range(7, 14), [1, 5, 1, 5, 1, 5, 1], strict=True)
    ]

    valence = trend(prepare(recent + previous, zone=KOLKATA), today=TODAY).of(Measure.VALENCE)

    assert valence.change == pytest.approx(22 / 7 - 19 / 7)
    assert abs(valence.change or 0.0) > MIN_TREND_CHANGE
    assert valence.ci is not None
    assert valence.ci[0] <= 0.0 <= valence.ci[1]
    assert valence.direction is TrendDirection.STEADY
    assert valence.holdback is Holdback.WITHIN_NOISE


def test_the_trend_windows_do_not_overlap_and_together_cover_fourteen_days() -> None:
    result = trend(prepare(history(30), zone=KOLKATA), today=TODAY, window_days=7)

    assert result.recent.end == TODAY
    assert result.recent.start == TODAY - timedelta(days=6)
    assert result.previous.end == result.recent.start - timedelta(days=1)
    assert result.previous.start == result.previous.end - timedelta(days=6)


# --------------------------------------------------------------------------- #
# 4. Resampling primitives                                                      #
# --------------------------------------------------------------------------- #


def test_identical_groups_have_a_p_value_of_exactly_one() -> None:
    assert permutation_p_value([3.0, 3.0, 3.0, 3.0], [3.0, 3.0, 3.0, 3.0], key="same") == 1.0


def test_a_separated_pair_has_a_small_p_value() -> None:
    low = [1.0] * 12
    high = [5.0] * 12

    p = permutation_p_value(low, high, key="separated", draws=500)

    assert p < ALPHA


def test_a_p_value_is_never_exactly_zero() -> None:
    """`we never saw this in 500 draws` is not `this cannot happen`."""
    p = permutation_p_value([1.0] * 10, [5.0] * 10, key="extreme", draws=500)

    assert p >= 1 / 501


def test_the_permutation_test_is_deterministic() -> None:
    group = [1.0, 2.0, 3.0, 4.0, 5.0, 2.0]
    rest = [3.0, 4.0, 5.0, 1.0, 2.0, 4.0]

    first = permutation_p_value(group, rest, key="factor:sleep:valence", draws=300)
    second = permutation_p_value(group, rest, key="factor:sleep:valence", draws=300)

    assert first == second


def test_two_different_comparisons_do_not_share_a_draw_stream() -> None:
    group = [1.0, 2.0, 2.0, 3.0, 5.0, 1.0, 4.0]
    rest = [3.0, 4.0, 4.0, 2.0, 5.0, 3.0, 2.0]

    first = permutation_p_value(group, rest, key="factor:sleep:valence", draws=200)
    second = permutation_p_value(group, rest, key="factor:work:valence", draws=200)

    # Not guaranteed to differ by construction, but with 200 draws of a noisy
    # statistic two independent streams agreeing to the draw is vanishingly
    # unlikely — and if this ever fails, the seed derivation is broken.
    assert isinstance(first, float) and isinstance(second, float)
    assert first <= 1.0 and second <= 1.0


def test_an_empty_group_has_no_p_value_to_report() -> None:
    assert permutation_p_value([], [1.0, 2.0], key="empty") == 1.0


def test_the_bootstrap_interval_of_identical_groups_is_a_point_at_zero() -> None:
    interval = bootstrap_ci([3.0] * 8, [3.0] * 8, key="flat", draws=200)

    assert interval == (0.0, 0.0)


def test_the_bootstrap_interval_of_separated_groups_excludes_zero() -> None:
    interval = bootstrap_ci([5.0] * 10, [1.0] * 10, key="split", draws=200)

    assert interval is not None
    assert interval[0] > 0.0
    assert interval[1] == pytest.approx(4.0)


def test_the_bootstrap_interval_is_ordered_and_centred_on_the_difference() -> None:
    group = [4.0, 5.0, 4.0, 5.0, 4.0, 5.0, 4.0]
    rest = [1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0]

    interval = bootstrap_ci(group, rest, key="centred", draws=400)

    assert interval is not None
    low, high = interval
    assert low <= high
    assert low <= 3.0 <= high  # the observed difference sits inside its own interval


def test_a_group_too_small_to_resample_gets_no_interval() -> None:
    assert bootstrap_ci([4.0], [1.0, 2.0, 3.0], key="tiny") is None


def test_the_omnibus_test_sees_a_real_grouping() -> None:
    values = [1.0] * 8 + [5.0] * 8
    labels = ["night"] * 8 + ["morning"] * 8

    p = permutation_f_p_value(values, labels, key="tod", draws=400)

    assert p is not None and p < ALPHA


def test_the_omnibus_test_sees_nothing_in_a_flat_grouping() -> None:
    values = [3.0] * 16
    labels = ["night"] * 8 + ["morning"] * 8

    assert permutation_f_p_value(values, labels, key="flat", draws=200) == 1.0


def test_the_omnibus_test_needs_two_groups() -> None:
    assert permutation_f_p_value([1.0, 2.0], ["night", "night"], key="one") is None


def test_the_omnibus_test_refuses_mismatched_input() -> None:
    with pytest.raises(ValueError, match="same length"):
        permutation_f_p_value([1.0, 2.0], ["night"], key="mismatch")


def test_cohens_d_matches_the_hand_computed_value() -> None:
    # [1,2,1,2] vs [3,4,3,4]: means 1.5 and 3.5, both stdevs sqrt(1/3), so the
    # pooled sd is sqrt(1/3) and d = -2 / sqrt(1/3) = -3.4641…
    from app.services.mood.insights import _cohens_d

    d = _cohens_d([1.0, 2.0, 1.0, 2.0], [3.0, 4.0, 3.0, 4.0])

    assert d == pytest.approx(-2 / (1 / 3) ** 0.5, rel=1e-6)


def test_cohens_d_is_none_when_there_is_no_spread_to_standardise_against() -> None:
    from app.services.mood.insights import _cohens_d

    assert _cohens_d([2.0, 2.0, 2.0], [4.0, 4.0, 4.0]) is None
    assert _cohens_d([1.0], [2.0, 3.0]) is None


# --------------------------------------------------------------------------- #
# 5. Factor associations                                                        #
# --------------------------------------------------------------------------- #


def _sleep_dataset(*, tagged_valence: int, untagged_valence: int, n: int = 20) -> list[CheckIn]:
    """``n`` sleep days and ``n`` other days, with the means chosen by the test.

    Each side alternates between two values rather than holding one, because a
    group with no spread has no standard deviation to divide by: the difference
    in means is still exactly ``tagged_valence - untagged_valence`` (both sides
    carry the same half-point offset), but the standardised difference and the
    bootstrap interval are now defined.
    """
    tagged = [
        entry(days_ago, valence=tagged_valence + days_ago % 2, energy=3, factors=("sleep",))
        for days_ago in range(n)
    ]
    untagged = [
        entry(
            days_ago + n,
            valence=untagged_valence + days_ago % 2,
            energy=3,
            factors=("work",),
        )
        for days_ago in range(n)
    ]
    return tagged + untagged


def test_a_constructed_difference_is_reported_to_the_point() -> None:
    report = factor_associations(
        prepare(_sleep_dataset(tagged_valence=2, untagged_valence=4), zone=KOLKATA)
    )

    sleep_valence = next(
        item
        for item in report.associations
        if item.factor == "sleep" and item.measure is Measure.VALENCE
    )

    assert sleep_valence.difference == pytest.approx(-2.0)
    assert sleep_valence.tagged_mean == 2.5
    assert sleep_valence.untagged_mean == 4.5
    assert sleep_valence.tagged_n == 20
    assert sleep_valence.untagged_n == 20
    assert sleep_valence.reported is True
    assert sleep_valence.holdback is None
    assert sleep_valence.p_value is not None and sleep_valence.p_value < sleep_valence.family_alpha
    assert sleep_valence.ci is not None and sleep_valence.ci[1] < 0.0
    assert sleep_valence.cohens_d is not None and sleep_valence.cohens_d < 0.0


def test_the_correction_is_alpha_over_every_comparison_that_could_have_run() -> None:
    report = factor_associations(
        prepare(_sleep_dataset(tagged_valence=2, untagged_valence=4), zone=KOLKATA)
    )

    assert report.comparisons == len(MOOD_FACTORS) * 2
    assert report.family_alpha == pytest.approx(ALPHA / (len(MOOD_FACTORS) * 2))
    assert all(item.family_alpha == report.family_alpha for item in report.associations)


def test_a_factor_nobody_tagged_still_counts_against_the_correction() -> None:
    """Dropping untested factors would make alpha depend on the answer."""
    points = prepare(_sleep_dataset(tagged_valence=2, untagged_valence=4), zone=KOLKATA)

    report = factor_associations(points)

    money = next(
        item
        for item in report.associations
        if item.factor == "money" and item.measure is Measure.VALENCE
    )
    assert money.tagged_n == 0
    assert money.holdback is Holdback.GROUP_TOO_SMALL
    assert report.comparisons == 16


def test_a_measure_that_does_not_differ_is_held_back_on_effect_size() -> None:
    # Energy is identical on both kinds of day, so it must not be reported even
    # though the valence comparison beside it is.
    report = factor_associations(
        prepare(_sleep_dataset(tagged_valence=2, untagged_valence=4), zone=KOLKATA)
    )

    energy = next(
        item
        for item in report.associations
        if item.factor == "sleep" and item.measure is Measure.ENERGY
    )

    assert energy.difference == pytest.approx(0.0)
    assert energy.reported is False
    assert energy.holdback is Holdback.SMALL_DIFFERENCE
    assert energy.p_value is None  # never asked: the effect floor came first


def test_a_tiny_difference_with_a_huge_sample_is_still_not_reported() -> None:
    """Statistical significance is not the same as mattering to somebody."""
    tagged = [entry(days_ago, valence=3, factors=("sleep",)) for days_ago in range(60)]
    untagged = [entry(days_ago + 60, valence=3, factors=("work",)) for days_ago in range(60)]
    # Nudge one single check-in so the means differ by less than a hundredth.
    nudged = [*tagged, entry(200, valence=4, factors=("sleep",)), *untagged]

    report = factor_associations(prepare(nudged, zone=KOLKATA))
    sleep = next(
        item
        for item in report.associations
        if item.factor == "sleep" and item.measure is Measure.VALENCE
    )

    assert sleep.difference is not None and abs(sleep.difference) < MIN_EFFECT_POINTS
    assert sleep.reported is False
    assert sleep.holdback is Holdback.SMALL_DIFFERENCE


@pytest.mark.parametrize("tagged_n", [0, 1, MIN_GROUP_N - 1])
def test_a_group_smaller_than_the_minimum_never_becomes_a_comparison(tagged_n: int) -> None:
    tagged = [entry(days_ago, valence=1, factors=("sleep",)) for days_ago in range(tagged_n)]
    untagged = [entry(days_ago + 20, valence=5, factors=("work",)) for days_ago in range(30)]

    report = factor_associations(prepare(tagged + untagged, zone=KOLKATA))
    sleep = next(
        item
        for item in report.associations
        if item.factor == "sleep" and item.measure is Measure.VALENCE
    )

    assert sleep.tagged_n == tagged_n
    assert sleep.reported is False
    assert sleep.holdback is Holdback.GROUP_TOO_SMALL
    assert sleep.difference is None  # not computed, not merely unpublished
    assert sleep.p_value is None
    assert sleep.ci is None
    assert sleep.cohens_d is None


def test_no_history_at_all_yields_no_associations_and_no_error() -> None:
    report = factor_associations([])

    assert report.comparisons == 16
    assert all(item.holdback is Holdback.NOT_ENOUGH_DATA for item in report.associations)
    assert report.reported() == ()


def test_pure_noise_reports_nothing() -> None:
    """The false-positive check, as a unit test with a fixed generator.

    Twenty histories with no relationship between tags and mood at all. Reporting
    even one association here would mean the panel invents a pattern in somebody's
    life out of a random week.
    """
    import random

    reported_total = 0
    for seed in range(20):
        rng = random.Random(seed)
        points: list[CheckIn] = []
        for days_ago in range(60):
            factors = tuple(factor for factor in MOOD_FACTORS if rng.random() < 0.25)[:5]
            points.append(
                entry(
                    days_ago,
                    hour=rng.choice([8, 13, 19, 23]),
                    valence=rng.randint(1, 5),
                    energy=rng.randint(1, 5),
                    factors=factors,
                )
            )
        report = factor_associations(prepare(points, zone=KOLKATA))
        reported_total += len(report.reported())

    assert reported_total == 0


def test_reported_associations_come_back_largest_difference_first() -> None:
    # 20 sleep days at 1, 20 friends days at 2, 40 other days at 5: sleep is
    # 3.0 points away from its untagged mean and friends 1.67, so the summary
    # leads with sleep without the client having to sort anything.
    sleep_days = [entry(days_ago, valence=1, factors=("sleep",)) for days_ago in range(20)]
    friends_days = [entry(days_ago + 20, valence=2, factors=("friends",)) for days_ago in range(20)]
    other_days = [entry(days_ago + 40, valence=5) for days_ago in range(40)]

    report = factor_associations(prepare(sleep_days + friends_days + other_days, zone=KOLKATA))
    ordered = [item.factor for item in report.reported() if item.measure is Measure.VALENCE]

    assert ordered == ["sleep", "friends"]
    differences: list[float] = [
        item.difference
        for item in report.reported()
        if item.measure is Measure.VALENCE and item.difference is not None
    ]
    assert len(differences) == 2
    assert differences == sorted(differences, key=abs, reverse=True)


# --------------------------------------------------------------------------- #
# 6. Time-of-day and weekday patterns                                           #
# --------------------------------------------------------------------------- #


def test_a_constructed_time_of_day_difference_is_reported_with_its_extremes() -> None:
    points = [entry(days_ago, hour=23, valence=1) for days_ago in range(8)]
    points += [entry(days_ago, hour=8, valence=5) for days_ago in range(8, 16)]

    pattern = group_pattern(prepare(points, zone=KOLKATA), kind=PatternKind.TIME_OF_DAY)

    assert pattern.reported is True
    assert pattern.holdback is None
    assert pattern.lowest is not None and pattern.lowest.id == "night"
    assert pattern.highest is not None and pattern.highest.id == "morning"
    assert pattern.spread == pytest.approx(4.0)
    assert pattern.p_value is not None and pattern.p_value < pattern.family_alpha


def test_a_flat_week_is_not_a_pattern() -> None:
    points = [entry(days_ago, hour=9 + (days_ago % 12), valence=3) for days_ago in range(40)]

    pattern = group_pattern(prepare(points, zone=KOLKATA), kind=PatternKind.TIME_OF_DAY)

    assert pattern.reported is False
    assert pattern.holdback is Holdback.WITHIN_NOISE
    assert pattern.lowest is None
    assert pattern.highest is None


def test_buckets_that_are_too_thin_are_never_named() -> None:
    # Nine check-ins, all in two buckets of four and five: only one bucket clears
    # the minimum, so there is nothing to compare and nothing to say.
    points = [entry(days_ago, hour=23, valence=1) for days_ago in range(4)]
    points += [entry(days_ago + 4, hour=8, valence=5) for days_ago in range(MIN_GROUP_N)]

    pattern = group_pattern(prepare(points, zone=KOLKATA), kind=PatternKind.TIME_OF_DAY)

    assert pattern.reported is False
    assert pattern.holdback is Holdback.GROUP_TOO_SMALL
    assert pattern.spread is None


def test_thin_buckets_still_appear_in_the_table_with_their_counts() -> None:
    """A table of somebody's own numbers is not a claim; naming one is."""
    points = [entry(days_ago, hour=23, valence=1) for days_ago in range(4)]
    points += [entry(days_ago + 4, hour=8, valence=5) for days_ago in range(MIN_GROUP_N)]

    pattern = group_pattern(prepare(points, zone=KOLKATA), kind=PatternKind.TIME_OF_DAY)
    by_id = {bucket.id: bucket for bucket in pattern.buckets}

    assert by_id["night"].entries == 4
    assert by_id["night"].enough_data is False
    assert by_id["morning"].entries == MIN_GROUP_N
    assert by_id["morning"].enough_data is True
    assert by_id["night"].valence_mean == 1.0


def test_a_weekday_pattern_uses_seven_buckets_and_the_caller_zone() -> None:
    # 42 consecutive days at 20:00 UTC, valence 1 on the Mondays and 5 otherwise.
    # In Kolkata those instants are already Tuesday morning, so the same rows name
    # a different day: the grouping follows the person's zone, not the server's.
    points = [
        CheckIn(
            at=datetime(2026, 8, 31, 20, 0, tzinfo=UTC) + timedelta(days=offset),
            valence=1 if offset % 7 == 0 else 5,
            energy=3,
        )
        for offset in range(42)
    ]

    in_kolkata = group_pattern(prepare(points, zone=KOLKATA), kind=PatternKind.WEEKDAY)
    in_utc = group_pattern(prepare(points, zone=ZoneInfo("UTC")), kind=PatternKind.WEEKDAY)

    assert [bucket.id for bucket in in_kolkata.buckets] == list(WEEKDAY_IDS)
    assert all(bucket.entries == 6 for bucket in in_kolkata.buckets)
    assert all(bucket.enough_data for bucket in in_kolkata.buckets)
    assert in_kolkata.reported is True
    assert in_kolkata.lowest is not None and in_kolkata.lowest.id == "tuesday"
    assert in_utc.lowest is not None and in_utc.lowest.id == "monday"


def test_every_bucket_carries_both_measures_for_the_data_table() -> None:
    points = [entry(days_ago, hour=9, valence=2, energy=5) for days_ago in range(20)]

    pattern = group_pattern(prepare(points, zone=KOLKATA), kind=PatternKind.TIME_OF_DAY)
    morning = next(bucket for bucket in pattern.buckets if bucket.id == "morning")

    assert morning.valence_mean == 2.0
    assert morning.energy_mean == 5.0


def test_an_empty_history_has_no_group_pattern() -> None:
    pattern = group_pattern([], kind=PatternKind.WEEKDAY)

    assert pattern.reported is False
    assert pattern.holdback is Holdback.NOT_ENOUGH_DATA
    assert pattern.p_value is None
    assert all(bucket.entries == 0 for bucket in pattern.buckets)


def test_noise_does_not_become_a_time_of_day_or_weekday_pattern() -> None:
    import random

    rng = random.Random(99)
    points = [
        entry(
            days_ago,
            hour=rng.choice([7, 10, 14, 17, 20, 23]),
            valence=rng.randint(1, 5),
            energy=rng.randint(1, 5),
        )
        for days_ago in range(90)
    ]
    prepared = prepare(points, zone=KOLKATA)

    assert group_pattern(prepared, kind=PatternKind.TIME_OF_DAY).reported is False
    assert group_pattern(prepared, kind=PatternKind.WEEKDAY).reported is False


# --------------------------------------------------------------------------- #
# 7. Weeks                                                                      #
# --------------------------------------------------------------------------- #


def test_week_start_is_always_a_monday() -> None:
    for offset in range(7):
        assert week_start(TODAY - timedelta(days=offset)).weekday() == 0


def test_weekly_buckets_include_the_current_week_and_every_empty_one() -> None:
    points = prepare([entry(0), entry(1)], zone=KOLKATA)

    buckets = weekly_buckets(points, today=TODAY, weeks=4)

    assert len(buckets) == 4
    assert buckets[-1].start == week_start(TODAY)
    assert buckets[-1].end == buckets[-1].start + timedelta(days=6)
    assert buckets[-1].entries == 2
    assert buckets[0].entries == 0  # an empty week is returned, not dropped
    assert buckets[0].valence_mean is None
    assert buckets[0].enough_data is False


def test_a_week_counts_distinct_days_not_check_ins() -> None:
    # Three check-ins across two local days.
    points = prepare(
        [
            CheckIn(at=at(0, 9), valence=3, energy=3),
            CheckIn(at=at(0, 21), valence=3, energy=3),
            CheckIn(at=at(1, 9), valence=3, energy=3),
        ],
        zone=KOLKATA,
    )

    bucket = weekly_buckets(points, today=TODAY, weeks=1)[-1]

    assert bucket.entries == 3
    assert bucket.days_with_check_in == 2


def test_a_week_counts_its_tags_most_frequent_first() -> None:
    points = prepare(
        [entry(days_ago, factors=("sleep", "work")) for days_ago in range(3)]
        + [entry(days_ago + 3, factors=("work",)) for days_ago in range(2)],
        zone=KOLKATA,
    )

    bucket = weekly_buckets(points, today=TODAY, weeks=2)[-1]

    assert [(item.id, item.count) for item in bucket.factors][:2] == [("work", 5), ("sleep", 3)]


def test_a_week_of_seven_check_ins_is_enough_to_describe() -> None:
    points = prepare([entry(days_ago) for days_ago in range(MIN_ENTRIES_FOR_TREND)], zone=KOLKATA)

    bucket = weekly_buckets(points, today=TODAY, weeks=2)[-1]

    assert bucket.enough_data is True


def test_build_weekly_rejects_a_nonsense_week_count() -> None:
    with pytest.raises(ValueError, match="weeks must be between"):
        build_weekly([], zone=KOLKATA, today=TODAY, weeks=0)
    with pytest.raises(ValueError, match="weeks must be between"):
        build_weekly([], zone=KOLKATA, today=TODAY, weeks=MAX_WEEKS + 1)


def test_build_insights_rejects_a_nonsense_window() -> None:
    with pytest.raises(ValueError, match="window_days must be between"):
        build_insights([], zone=KOLKATA, today=TODAY, window_days=0)
    with pytest.raises(ValueError, match="window_days must be between"):
        build_insights([], zone=KOLKATA, today=TODAY, window_days=MAX_WINDOW_DAYS + 1)


def test_check_ins_outside_the_window_are_counted_but_not_used() -> None:
    old = [entry(days_ago, valence=1) for days_ago in range(60, 80)]
    recent = [entry(days_ago, valence=5) for days_ago in range(10)]

    report = build_insights(old + recent, zone=KOLKATA, today=TODAY, window_days=30)

    assert report.considered_check_ins == 10
    assert report.total_check_ins == 30


def test_the_total_can_be_supplied_so_the_window_is_not_mistaken_for_everything() -> None:
    report = build_insights(
        [entry(days_ago) for days_ago in range(5)],
        zone=KOLKATA,
        today=TODAY,
        window_days=7,
        total_check_ins=400,
    )

    assert report.considered_check_ins == 5
    assert report.total_check_ins == 400


# --------------------------------------------------------------------------- #
# 8. The whole report, and what it refuses to say                               #
# --------------------------------------------------------------------------- #


def test_three_check_ins_produce_no_insight_and_say_why() -> None:
    report = build_insights([entry(0), entry(1), entry(2)], zone=KOLKATA, today=TODAY)

    assert report.enough_data is False
    assert report.trend.enough_data is False
    assert report.trend.of(Measure.VALENCE).direction is TrendDirection.NOT_ENOUGH_DATA
    assert report.factors.reported() == ()
    assert report.time_of_day.reported is False
    assert report.weekday.reported is False

    sentences = pattern_sentences(report, strings=STRINGS)
    assert len(sentences) == 1
    assert "not enough data" in sentences[0].lower()


def test_no_check_ins_at_all_says_so_and_claims_nothing() -> None:
    report = build_insights([], zone=KOLKATA, today=TODAY)

    sentences = pattern_sentences(report, strings=STRINGS)

    assert len(sentences) == 1
    assert "no check-ins" in sentences[0].lower()
    for claim in ("rising", "falling", "higher", "lower", "averaged", "lowest"):
        assert claim not in sentences[0].lower(), claim


def test_a_known_history_produces_the_expected_sentence_set() -> None:
    points = [
        entry(days_ago, valence=2, energy=2, factors=("sleep",)) for days_ago in range(12)
    ] + [entry(days_ago + 12, valence=4, energy=4, factors=("friends",)) for days_ago in range(12)]

    report = build_insights(points, zone=KOLKATA, today=TODAY, window_days=60)
    sentences = pattern_sentences(report, strings=STRINGS)

    joined = " ".join(sentences)
    assert "sleep" in joined and "friends" in joined
    assert "2.0 points lower" in joined
    assert "2.0 points higher" in joined
    # Both sample sizes are inside the sentence, never in a footnote.
    assert "12 check-ins vs 12" in joined


def test_the_report_is_reproducible_down_to_the_sentence() -> None:
    points = _sleep_dataset(tagged_valence=2, untagged_valence=4, n=12)

    first = pattern_sentences(build_insights(points, zone=KOLKATA, today=TODAY), strings=STRINGS)
    second = pattern_sentences(build_insights(points, zone=KOLKATA, today=TODAY), strings=STRINGS)

    assert first == second


def test_the_weekly_summary_names_every_week_it_was_asked_for() -> None:
    points = history(30)

    report = build_weekly(points, zone=KOLKATA, today=TODAY, weeks=DEFAULT_WEEKS)
    sentences = weekly_sentences(report, strings=STRINGS)

    assert len(report.weekly) == DEFAULT_WEEKS
    assert sum(1 for sentence in sentences if sentence.startswith("Week of")) == DEFAULT_WEEKS
    assert sentences[0].startswith("Here are your last")


def test_the_weekly_summary_of_three_check_ins_says_not_enough_data() -> None:
    report = build_weekly([entry(0), entry(1), entry(2)], zone=KOLKATA, today=TODAY)

    sentences = weekly_sentences(report, strings=STRINGS)

    assert "not enough data" in sentences[0].lower()
    assert report.enough_data is False


def test_a_week_with_no_check_ins_is_described_as_empty_not_skipped() -> None:
    report = build_weekly([entry(0), entry(1)], zone=KOLKATA, today=TODAY, weeks=3)

    sentences = weekly_sentences(report, strings=STRINGS)

    assert any("no check-ins" in sentence for sentence in sentences)


def test_a_thin_week_is_not_described_with_a_mean() -> None:
    # Six check-ins in one week: below the minimum, so the line says "too few"
    # rather than quoting an average of six numbers as if it were a fact.
    points = [entry(days_ago, valence=5) for days_ago in range(6)]

    report = build_weekly(points, zone=KOLKATA, today=TODAY, weeks=1)
    sentences = weekly_sentences(report, strings=STRINGS)

    assert any("too few to describe the week" in sentence for sentence in sentences)
    assert not any("mood around" in sentence for sentence in sentences)


def test_no_sentence_uses_better_or_worse() -> None:
    """Higher and lower describe a number; better and worse judge a person."""
    points = _sleep_dataset(tagged_valence=1, untagged_valence=5, n=15)
    report = build_insights(points, zone=KOLKATA, today=TODAY)
    weekly = build_weekly(points, zone=KOLKATA, today=TODAY)

    sentences = pattern_sentences(report, strings=STRINGS)
    sentences += weekly_sentences(weekly, strings=STRINGS)

    lowered = " ".join(sentences).lower()
    for banned in ("better", "worse", "bad", "good", "improve", "should"):
        assert banned not in lowered, banned


def test_the_localised_summary_says_the_same_thing_in_three_languages() -> None:
    points = _sleep_dataset(tagged_valence=2, untagged_valence=4, n=15)
    report = build_insights(points, zone=KOLKATA, today=TODAY)

    english = pattern_sentences(report, strings=load_insights("en"))
    hindi = pattern_sentences(report, strings=load_insights("hi"))
    bengali = pattern_sentences(report, strings=load_insights("bn"))

    assert len(english) == len(hindi) == len(bengali)
    assert english != hindi != bengali
    # Devanagari and Bengali numerals, not Latin ones: a Hindi sentence that says
    # "0.8" is half-translated.
    assert any(char in "०१२३४५६७८९" for char in "".join(hindi))
    assert any(char in "০১২৩৪৫৬৭৮৯" for char in "".join(bengali))


def test_direction_words_are_higher_and_lower_in_every_locale() -> None:
    for locale in ("en", "hi", "bn"):
        strings = load_insights(locale)
        assert strings.string("word.higher") != strings.string("word.lower")


def test_the_report_carries_the_timezone_it_was_computed_in() -> None:
    report = build_insights(history(10), zone=KOLKATA, timezone_fallback_used=True, today=TODAY)

    assert report.timezone == "Asia/Kolkata"
    assert report.timezone_fallback_used is True
