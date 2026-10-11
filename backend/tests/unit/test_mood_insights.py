"""Tests for the mood insights pure functions.

Three kinds of test live here, and the distinction is deliberate:

* **Known answers.** Synthetic series are built with a planted effect (``sleep``
  lowers mood by a point, ``friends`` raises it by half), so the assertion is
  about arithmetic rather than about the shape of a return value.
* **The minimum-data rules.** Below seven days there is no trend; below five
  days on either side of a comparison there is no association — and in both cases
  the result says *why* rather than reporting a number nobody should act on.
* **The wording.** Every generated sentence is checked for causal language, and
  the i18n templates are checked for being three different languages rather than
  one language with a translated heading.
"""

from __future__ import annotations

import csv
import io
import random
from datetime import UTC, date, datetime, timedelta
from statistics import mean
from typing import TYPE_CHECKING

from app.services.mood.insights import (
    BOOTSTRAP_RESAMPLES,
    MIN_ENTRIES_FOR_TREND,
    MIN_ENTRIES_PER_GROUP,
    PERMUTATION_RESAMPLES,
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

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Two check-ins, three emotions and five tags — the shapes every test builds on.
Entry = tuple[datetime, int, int, list[str], dict[str, object]]
ExportRow = tuple[datetime, int, int, list[str], dict[str, object], str | None]

BASE = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)

#: Phrases that must never appear in a generated sentence.
CAUSAL_PHRASES = (
    "causes",
    "caused",
    "because of",
    "due to",
    "leads to",
    "results in",
    "makes you",
)


# --------------------------------------------------------------------------- #
# Fixtures and synthetic series                                                 #
# --------------------------------------------------------------------------- #


def entry(
    day_offset: int,
    valence: int,
    energy: int,
    emotions: list[str] | None = None,
    factors: dict[str, object] | None = None,
    *,
    base: datetime = BASE,
    hour: int = 12,
) -> Entry:
    """One check-in at local noon on ``base + day_offset``."""
    at = (base + timedelta(days=day_offset)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return (at, valence, energy, emotions or [], factors or {})


def planted_series(
    *,
    days: int = 60,
    seed: int = 42,
    sleep_effect: float = -1.0,
    friends_effect: float = 0.5,
    sleep_rate: float = 0.3,
    friends_rate: float = 0.25,
    base: datetime = BASE,
) -> list[Entry]:
    """A series with known effects, deterministic for a seed.

    The jitter is deliberately wide relative to the planted effect: a narrow one
    rounds every day to the same integer, which makes the series constant and the
    effect size undefined (zero pooled variance) — a "noise" fixture that proves
    nothing because there is nothing to measure.
    """
    rng = random.Random(seed)
    rows: list[Entry] = []
    for offset in range(days):
        has_sleep = rng.random() < sleep_rate
        has_friends = rng.random() < friends_rate
        value = (
            3.0 + (sleep_effect if has_sleep else 0.0) + (friends_effect if has_friends else 0.0)
        )
        valence = max(1, min(5, round(value + rng.uniform(-1.2, 1.2))))
        energy = max(1, min(5, round(3.0 + rng.uniform(-1.2, 1.2))))
        factors: dict[str, object] = {}
        if has_sleep:
            factors["sleep"] = 1
        if has_friends:
            factors["friends"] = 1
        rows.append(entry(offset - days + 1, valence, energy, factors=factors, base=base))
    return rows


def noise_series(*, days: int = 45, seed: int = 0, base: datetime = BASE) -> list[Entry]:
    """A series with no planted effect at all."""
    return planted_series(days=days, seed=seed, sleep_effect=0.0, friends_effect=0.0, base=base)


def find(associations: Sequence[FactorAssociation], factor: str) -> FactorAssociation:
    for association in associations:
        if association.factor == factor:
            return association
    raise AssertionError(f"{factor} is missing from the result")


def assert_no_causal_language(text: str) -> None:
    lowered = text.lower()
    for phrase in CAUSAL_PHRASES:
        assert phrase not in lowered, f"{phrase!r} appears in {text!r}"


# --------------------------------------------------------------------------- #
# Rolling averages                                                              #
# --------------------------------------------------------------------------- #


def test_a_clear_upward_trend_is_reported_as_up() -> None:
    """Seven days, rising by about half a point a day: the slope is real."""
    base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    series = [entry(i, min(5, 1 + i), 3, base=base) for i in range(7)]

    result = rolling_average(series, window_days=7, zone="UTC")

    assert isinstance(result, RollingWindowResult)
    assert result.window_days == 7
    assert len(result.points) == 7
    assert result.trend == "up"
    assert result.trend_note == "Your mood has been trending up over this period."
    # The daily averages are the daily values, not a running mean of everything.
    assert [p.valence_avg for p in result.points] == [1.0, 2.0, 3.0, 4.0, 5.0, 5.0, 5.0]


def test_a_clear_downward_trend_is_reported_as_down() -> None:
    base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    series = [entry(i, max(1, 5 - i), 3, base=base) for i in range(7)]

    result = rolling_average(series, window_days=7, zone="UTC")

    assert result.trend == "down"
    assert result.trend_note == "Your mood has been trending down over this period."


def test_a_flat_series_is_not_called_a_trend() -> None:
    """Same value every day is stability, not an upward trend."""
    base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    series = [entry(i, 3, 3, base=base) for i in range(10)]

    result = rolling_average(series, window_days=7, zone="UTC")

    assert result.trend == "flat"
    assert result.trend_note == "Your mood has been stable over this period."


def test_six_days_is_not_enough_for_a_trend() -> None:
    """The rule, at its boundary: six days refuses, seven days answers."""
    base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    six = [entry(i, 1 + i, 3, base=base) for i in range(6)]
    seven = [entry(i, 1 + i, 3, base=base) for i in range(7)]

    refused = rolling_average(six, window_days=7, zone="UTC")
    allowed = rolling_average(seven, window_days=7, zone="UTC")

    assert refused.trend == "not_enough_data"
    assert refused.trend_note == "Need at least 7 days with check-ins to show a trend."
    assert len(refused.points) == 6  # the data is still shown, only the claim is withheld
    assert allowed.trend in ("up", "flat")


def test_no_check_ins_at_all_says_so() -> None:
    result = rolling_average([], window_days=7, zone="UTC")

    assert result.trend == "not_enough_data"
    assert result.points == []
    assert result.trend_note == "No check-ins in the last 7 days."


def test_the_30_day_window_is_clipped_to_30_days() -> None:
    series = noise_series(days=45, seed=1)

    result = rolling_average(series, window_days=30, zone="UTC")

    assert result.window_days == 30
    assert len(result.points) == 30
    assert result.points[-1].day == BASE.date()


def test_two_check_ins_on_one_day_are_one_point() -> None:
    """A day is the unit: two check-ins average, they do not count twice."""
    same_day = [entry(0, 2, 3), entry(0, 4, 3)]
    next_day = [entry(1, 3, 3)]

    result = rolling_average(same_day + next_day, window_days=7, zone="UTC")

    assert len(result.points) == 2
    assert result.points[0].count == 2
    assert result.points[0].valence_avg == 3.0


def test_days_are_counted_in_the_requested_timezone() -> None:
    """20:00Z is the next calendar day in Kolkata, and that is the day that counts."""
    series: list[Entry] = [
        (datetime(2026, 3, 15, 20, 0, tzinfo=UTC), 4, 3, [], {}),
        (datetime(2026, 3, 16, 20, 0, tzinfo=UTC), 3, 3, [], {}),
    ]

    result = rolling_average(series, window_days=7, zone="Asia/Kolkata")

    assert [point.day for point in result.points] == [date(2026, 3, 16), date(2026, 3, 17)]


# --------------------------------------------------------------------------- #
# Factor associations                                                           #
# --------------------------------------------------------------------------- #


def test_a_planted_effect_is_found_with_its_sample_sizes() -> None:
    """60 days, sleep -1.0 and friends +0.5: both signs, both sizes, both counts."""
    series = planted_series(days=60, seed=42, sleep_effect=-1.0, friends_effect=0.5)

    associations = factor_association(series, zone="UTC", rng=random.Random(7))
    sleep = find(associations, "sleep")
    friends = find(associations, "friends")

    assert sleep.with_factor_count >= MIN_ENTRIES_PER_GROUP
    assert sleep.without_factor_count >= MIN_ENTRIES_PER_GROUP
    assert sleep.with_factor_avg is not None
    assert sleep.without_factor_avg is not None
    assert sleep.with_factor_avg < sleep.without_factor_avg
    assert sleep.effect_size is not None and sleep.effect_size < 0
    assert sleep.ci_lower is not None and sleep.ci_upper is not None
    assert sleep.ci_lower < sleep.ci_upper < 0  # the whole interval is below zero

    assert friends.effect_size is not None and friends.effect_size > 0
    assert friends.with_factor_avg is not None
    assert friends.without_factor_avg is not None
    assert friends.with_factor_avg > friends.without_factor_avg

    # The sentence carries the direction, the size and the counts.
    assert f"n={sleep.with_factor_count} vs {sleep.without_factor_count}" in sleep.note
    assert "lower on average" in sleep.note
    assert "higher on average" in friends.note
    assert "not a causal link" in sleep.note


def test_four_days_on_one_side_is_not_enough() -> None:
    """The boundary again: four days refuses, five days answers."""
    # Values alternate so each group has variance: a constant group has no
    # pooled standard deviation, and an undefined effect size would make the
    # "five days is enough" half of this test vacuous.
    four_with = [entry(i, 2 + i % 2, 3, factors={"sleep": 1}) for i in range(4)]
    five_with = [entry(i, 2 + i % 2, 3, factors={"sleep": 1}) for i in range(5)]
    without = [entry(i + 10, 4 + i % 2, 3) for i in range(10)]

    refused = find(factor_association(four_with + without, zone="UTC"), "sleep")
    allowed = find(factor_association(five_with + without, zone="UTC"), "sleep")

    assert refused.effect_size is None
    assert refused.p_value is None
    assert refused.ci_lower is None
    assert "Not enough data" in refused.note
    assert "need at least 5" in refused.note

    assert allowed.effect_size is not None
    assert allowed.p_value is not None


def test_a_day_counts_as_tagged_when_any_check_in_that_day_carries_the_tag() -> None:
    """The unit of comparison is the day, not the row."""
    series = [
        entry(0, 2, 3, factors={"sleep": 1}),
        entry(0, 4, 3),  # same day, no tag — the day is still a "sleep" day
        entry(1, 3, 3),
    ]

    sleep = find(factor_association(series, zone="UTC", min_per_group=1), "sleep")

    assert sleep.with_factor_count == 1
    assert sleep.without_factor_count == 1
    assert sleep.with_factor_avg == 3.0  # the day's mean, not the tagged row's value


def test_no_generated_sentence_claims_a_cause() -> None:
    series = planted_series(days=60, seed=42)

    for association in factor_association(series, zone="UTC", rng=random.Random(7)):
        assert_no_causal_language(association.note)
        assert "association" in association.note or "Not enough data" in association.note


def test_the_same_seed_gives_the_same_numbers() -> None:
    """Bootstrap and permutation are resampled from an injected RNG, so a report
    can be re-run and compared — otherwise a "0.03" could never be investigated."""
    series = planted_series(days=60, seed=42)

    first = factor_association(series, zone="UTC", rng=random.Random(999))
    second = factor_association(series, zone="UTC", rng=random.Random(999))

    for left, right in zip(first, second, strict=True):
        assert left.ci_lower == right.ci_lower
        assert left.ci_upper == right.ci_upper
        assert left.p_value == right.p_value


# --------------------------------------------------------------------------- #
# Time patterns                                                                 #
# --------------------------------------------------------------------------- #


def test_hourly_patterns_cover_all_24_hours() -> None:
    base = datetime(2026, 3, 1, 0, 0, 0, tzinfo=UTC)
    series: list[Entry] = [
        (base.replace(hour=hour), 3, 3, [], {}) for hour in range(24) for _ in range(6)
    ]

    hourly, _ = time_patterns(series, zone="UTC", min_per_bucket=5)

    assert len(hourly) == 24
    assert all(pattern.count == 6 for pattern in hourly)
    assert all(pattern.valence_avg is not None for pattern in hourly)
    assert hourly[10].bucket == "10"
    assert hourly[10].note == "6 check-in(s) in this hour."


def test_weekday_patterns_cover_all_seven_days() -> None:
    monday = datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)
    series: list[Entry] = [
        (monday + timedelta(days=offset), 3, 3, [], {}) for offset in range(7) for _ in range(6)
    ]

    _, weekday = time_patterns(series, zone="UTC", min_per_bucket=5)

    assert [pattern.bucket for pattern in weekday] == [
        "Mon",
        "Tue",
        "Wed",
        "Thu",
        "Fri",
        "Sat",
        "Sun",
    ]
    assert all(pattern.count == 6 for pattern in weekday)


def test_a_thin_bucket_reports_no_average_and_says_why() -> None:
    series: list[Entry] = [entry(0, 3, 3, hour=10), entry(0, 4, 3, hour=10)]

    hourly, _ = time_patterns(series, zone="UTC", min_per_bucket=5)

    thin = hourly[10]
    assert thin.count == 2
    assert thin.valence_avg is None
    assert thin.energy_avg is None
    assert "Only 2 check-in(s)" in thin.note
    assert "need 5" in thin.note
    # Every other hour is empty, and says that too rather than showing a zero.
    assert hourly[11].count == 0
    assert hourly[11].valence_avg is None


def test_hours_are_the_callers_local_hours() -> None:
    """18:30Z on the 14th is 00:00 on the 15th in Kolkata."""
    series: list[Entry] = [
        (datetime(2026, 3, 14, 18, 30, tzinfo=UTC) + timedelta(minutes=i), 2, 2, [], {})
        for i in range(6)
    ]

    utc_hourly, utc_weekday = time_patterns(series, zone="UTC", min_per_bucket=5)
    kolkata_hourly, kolkata_weekday = time_patterns(series, zone="Asia/Kolkata", min_per_bucket=5)

    assert utc_hourly[18].count == 6
    assert kolkata_hourly[0].count == 6
    assert utc_weekday[5].bucket == "Sat"  # the 14th is a Saturday in UTC
    assert kolkata_weekday[6].bucket == "Sun"  # and a Sunday in Kolkata
    assert kolkata_weekday[6].count == 6


# --------------------------------------------------------------------------- #
# The weekly summary                                                            #
# --------------------------------------------------------------------------- #


def test_a_full_week_summarises_itself() -> None:
    base = datetime(2026, 3, 9, 12, 0, 0, tzinfo=UTC)  # a Monday
    series = [
        entry(i, 2 + i, 3, factors=({"sleep": 1} if i % 2 else {}), base=base) for i in range(7)
    ]

    summary = weekly_summary(series, zone="UTC", locale="en", now=base + timedelta(days=7))

    assert isinstance(summary, WeeklySummary)
    assert summary.total_check_ins == 7
    assert summary.valence_avg is not None and summary.energy_avg is not None
    assert summary.trend in ("up", "flat")  # clamping softens the last two days
    assert summary.top_factors and summary.top_factors[0][0] == "sleep"
    assert "Your week in review" in summary.text
    assert "pattern summary, not a diagnosis" in summary.text
    assert_no_causal_language(summary.text)


def test_the_summary_window_is_the_seven_days_before_today() -> None:
    """``now`` is the seam; the window ends yesterday, so "this week" is complete."""
    base = datetime(2026, 3, 9, 12, 0, 0, tzinfo=UTC)
    series = [entry(i, 3, 3, base=base) for i in range(10)]

    summary = weekly_summary(series, zone="UTC", locale="en", now=base + timedelta(days=8))

    assert summary.period_start == date(2026, 3, 10)
    assert summary.period_end == date(2026, 3, 16)
    assert summary.total_check_ins == 7  # days 10..16 inclusive, one each


def test_an_empty_week_says_so_instead_of_showing_zeros() -> None:
    summary = weekly_summary([], zone="UTC", locale="en")

    assert summary.total_check_ins == 0
    assert summary.valence_avg is None
    assert summary.energy_avg is None
    assert summary.trend == "not_enough_data"
    assert summary.text == "No check-ins this week. Whenever you start, that's day one."


def test_six_days_in_the_window_still_means_no_trend() -> None:
    base = datetime(2026, 3, 9, 12, 0, 0, tzinfo=UTC)
    series = [entry(i, 1 + i, 3, base=base) for i in range(6)]

    summary = weekly_summary(series, zone="UTC", locale="en", now=base + timedelta(days=7))

    assert summary.total_check_ins == 6
    assert summary.trend == "not_enough_data"
    assert "need 7 days" in summary.text


def test_the_summary_is_three_real_languages() -> None:
    """Not one English template with a translated heading.

    Asserted by script: Devanagari for Hindi and Bengali for Bengali, and the
    disclaimer present in each — a summary that reads like a verdict is worse
    than no summary, in every language.
    """
    base = datetime(2026, 3, 9, 12, 0, 0, tzinfo=UTC)
    series = [entry(i, 3, 3, base=base) for i in range(7)]

    texts = {
        locale: weekly_summary(series, zone="UTC", locale=locale, now=base + timedelta(days=8)).text
        for locale in ("en", "hi", "bn")
    }

    assert len({text for text in texts.values()}) == 3
    assert any("\u0900" <= ch <= "\u097f" for ch in texts["hi"]), "hi is not Devanagari"
    assert any("\u0980" <= ch <= "\u09ff" for ch in texts["bn"]), "bn is not Bengali"
    assert "diagnosis" in texts["en"]
    assert "निदान" in texts["hi"]
    assert "নির্ণয়" in texts["bn"]
    for text in texts.values():
        assert "7" in text  # the count is in the sentence, not only in a card


def test_an_unknown_locale_falls_back_to_english() -> None:
    base = datetime(2026, 3, 9, 12, 0, 0, tzinfo=UTC)
    series = [entry(i, 3, 3, base=base) for i in range(7)]

    summary = weekly_summary(series, zone="UTC", locale="fr", now=base + timedelta(days=8))

    assert "Your week in review" in summary.text


# --------------------------------------------------------------------------- #
# CSV export                                                                    #
# --------------------------------------------------------------------------- #


def test_the_entries_csv_has_a_row_per_check_in_and_the_owners_note() -> None:
    rows: list[ExportRow] = [
        (*entry(0, 3, 4, emotions=["joy"], factors={"sleep": 1}), "a note"),
        (*entry(1, 4, 3, emotions=["calm"], factors={"friends": 1}), None),
    ]

    parsed = list(csv.reader(io.StringIO(mood_entries_to_csv(rows, zone="UTC"))))

    assert parsed[0] == [
        "local_date",
        "created_at_utc",
        "valence",
        "energy",
        "emotions",
        "factors",
        "note",
    ]
    assert len(parsed) == 3
    assert parsed[1][0] == "2026-03-15"
    assert parsed[1][4] == "joy"
    assert parsed[1][5] == "sleep"
    assert parsed[1][6] == "a note"
    assert parsed[2][6] == ""  # no note is an empty cell, not the word "None"


def test_a_note_with_commas_and_quotes_does_not_shift_the_columns() -> None:
    """Parse the output back, so a hand-rolled writer that escaped the obvious
    case would still fail here."""
    awkward = 'slept badly, "again"\nand a third line, still here'
    rows: list[ExportRow] = [(*entry(0, 2, 2), awkward)]

    parsed = list(csv.reader(io.StringIO(mood_entries_to_csv(rows, zone="UTC"))))

    assert len(parsed) == 2
    assert len(parsed[1]) == 7
    assert parsed[1][6] == awkward
    assert parsed[1][2] == "2"


def test_the_associations_csv_has_a_row_per_tag_with_blanks_where_undefined() -> None:
    from app.services.mood.insights import factor_association as analyse

    series = planted_series(days=3, seed=1)  # too short for any comparison
    associations = analyse(series, zone="UTC")

    parsed = list(csv.reader(io.StringIO(factor_associations_to_csv(associations))))

    assert parsed[0][0] == "factor"
    assert parsed[0][-1] == "note"
    assert len(parsed) == 9  # header + the eight tags
    sleep_row = next(row for row in parsed[1:] if row[0] == "sleep")
    assert sleep_row[5] == ""  # no effect size
    assert sleep_row[8] == ""  # no p-value
    assert "Not enough data" in sleep_row[9]


def test_the_time_patterns_csv_has_every_bucket() -> None:
    hourly = [TimePattern("10", 3.0, 3.5, 5, "5 check-in(s) in this hour.")]
    weekday = [TimePattern("Mon", 3.2, 3.0, 6, "6 check-in(s) on this weekday.")]

    parsed = list(csv.reader(io.StringIO(time_patterns_to_csv(hourly, weekday))))

    assert parsed[0] == ["type", "bucket", "valence_avg", "energy_avg", "count", "note"]
    assert parsed[1] == ["hour", "10", "3.0", "3.5", "5", "5 check-in(s) in this hour."]
    assert parsed[2][0] == "weekday"
    assert parsed[2][1] == "Mon"


def test_the_csv_never_contains_a_value_that_was_not_an_input() -> None:
    """A CSV is data leaving the app; every number in it must be traceable."""
    rows: list[ExportRow] = [(*entry(0, 3, 4, emotions=["joy"], factors={"sleep": 1}), "note")]
    parsed = list(csv.reader(io.StringIO(mood_entries_to_csv(rows, zone="UTC"))))

    assert parsed[1][2] == "3"
    assert parsed[1][3] == "4"
    assert set(parsed[1]) == {"2026-03-15", BASE.isoformat(), "3", "4", "joy", "sleep", "note"}


# --------------------------------------------------------------------------- #
# The published constants                                                       #
# --------------------------------------------------------------------------- #


def test_the_minimum_data_rules_are_the_published_ones() -> None:
    """A change to these numbers is a product decision, so it fails here first."""
    assert MIN_ENTRIES_FOR_TREND == 7
    assert MIN_ENTRIES_PER_GROUP == 5
    assert BOOTSTRAP_RESAMPLES == 2000
    assert PERMUTATION_RESAMPLES == 2000


def test_a_noise_series_never_produces_a_strong_association() -> None:
    """Twenty independent noise series, through the statistics themselves.

    The number that matters is the rate, and it is printed by
    ``scripts/verify_day16.py``; this test is the in-suite guard so a regression
    is caught here rather than in a hand-run report.
    """
    strong = 0
    compared = 0
    for seed in range(20):
        associations = factor_association(
            noise_series(days=45, seed=5000 + seed), zone="UTC", rng=random.Random(seed)
        )
        for association in associations:
            if association.p_value is None or association.effect_size is None:
                continue
            compared += 1
            if association.p_value < 0.05 and abs(association.effect_size) > 0.8:
                strong += 1

    assert compared > 20, "the sweep compared too few factors to mean anything"
    rate = strong / compared
    assert rate <= 0.10, (
        f"{strong}/{compared} noise comparisons looked like real effects ({rate:.1%})"
    )


def test_a_planted_effect_survives_a_narrower_jitter() -> None:
    """A sanity check on the statistics themselves, independent of the fixture.

    Twenty check-ins with the tag at 2 and twenty without at 4 is not noise:
    a median-split effect this large must be found, and the interval must sit
    below zero.
    """
    series = [
        *[entry(i, 2, 3, factors={"sleep": 1}) for i in range(20)],
        *[entry(i + 30, 4, 3) for i in range(20)],
    ]

    sleep = find(factor_association(series, zone="UTC", rng=random.Random(3)), "sleep")

    assert sleep.with_factor_avg == 2.0
    assert sleep.without_factor_avg == 4.0
    assert sleep.effect_size is None  # zero variance inside each group…
    assert sleep.p_value is not None and sleep.p_value < 0.01  # …but the shift is unmistakable
    assert sleep.ci_lower == -2.0 and sleep.ci_upper == -2.0  # every resample agrees


def test_averaging_across_days_uses_every_check_in() -> None:
    """Three check-ins in one bucket average all three, not the first."""
    base = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    series = [entry(0, 2, 1, base=base), entry(0, 3, 2, base=base), entry(0, 5, 3, base=base)]

    result = rolling_average(series, window_days=7, zone="UTC")

    assert len(result.points) == 1
    assert result.points[0].count == 3
    assert result.points[0].valence_avg == mean([2, 3, 5])
    assert result.points[0].energy_avg == mean([1, 2, 3])
