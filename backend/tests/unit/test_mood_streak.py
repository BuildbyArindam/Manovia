"""The forgiving streak: pure logic, real timezones, every edge named.

These tests are the specification of the rules in
:mod:`app.services.mood.streak`, and they are written from the *person's* point
of view rather than the algorithm's: a check-in at 00:30 in Kolkata belongs to
that Kolkata day, a 23-hour DST day is still one day, one missed day does not
cost anything, and two missed days cost a count but never a welcome.

No test here needs a database or a clock: ``today`` is a parameter, which is why
the end-of-year, midnight and DST cases can be pinned exactly instead of waited
for.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.mood.streak import (
    CURRENT_RUN_GRACE_DAYS,
    FREEZE_WINDOW_DAYS,
    MAX_FREEZES_PER_WINDOW,
    StreakState,
    StreakSummary,
    UnknownTimezoneError,
    compute_streak,
    load_timezone,
    local_date,
    runs_of,
)

KOLKATA = ZoneInfo("Asia/Kolkata")
NEW_YORK = ZoneInfo("America/New_York")
UTC_ZONE = ZoneInfo("UTC")


def at(day: date, *, hour: int = 9, zone: ZoneInfo = UTC_ZONE) -> datetime:
    """An instant at ``hour`` local time on ``day``, as UTC.

    Building fixtures in local time and converting is the only way to write a
    timezone test that is still readable a year later.
    """
    return datetime(day.year, day.month, day.day, hour, tzinfo=zone).astimezone(UTC)


def check_in(*days: date, zone: ZoneInfo = UTC_ZONE) -> list[datetime]:
    """One check-in per day, at 09:00 local — the ordinary case."""
    return [at(day, zone=zone) for day in days]


def summary(instants: list[datetime], today: date, zone: ZoneInfo = UTC_ZONE) -> StreakSummary:
    return compute_streak(check_in_instants=instants, today=today, timezone=zone)


# --------------------------------------------------------------------------- #
# The rules                                                                     #
# --------------------------------------------------------------------------- #


def test_no_check_ins_yet_is_a_state_not_a_judgement() -> None:
    result = summary([], date(2026, 3, 15))

    assert result.state is StreakState.NEVER
    assert result.current_streak == 0
    assert result.longest_streak == 0
    assert result.last_check_in is None
    assert result.days_since_last_check_in is None
    assert result.freeze_available is True


def test_three_days_in_a_row() -> None:
    days = [date(2026, 3, 13), date(2026, 3, 14), date(2026, 3, 15)]

    result = summary(check_in(*days), date(2026, 3, 15))

    assert result.state is StreakState.TODAY
    assert result.checked_in_today is True
    assert result.current_streak == 3
    assert result.longest_streak == 3
    assert result.freezes_used == 0


def test_today_still_open_does_not_break_anything() -> None:
    """Rule 1: not having checked in yet today is not missing a day."""
    days = [date(2026, 3, 13), date(2026, 3, 14)]

    result = summary(check_in(*days), date(2026, 3, 15))

    assert result.state is StreakState.OPEN
    assert result.checked_in_today is False
    assert result.current_streak == 2
    assert result.days_since_last_check_in == 1
    # Nothing was spent: an empty today is not a bridged day.
    assert result.freezes_used == 0
    assert result.freeze_available is True


def test_yesterdays_open_day_is_not_a_saved_gap() -> None:
    """Grace covers *today* only. A run with no check-in for two days is old."""
    days = [date(2026, 3, 12), date(2026, 3, 13)]

    result = summary(check_in(*days), date(2026, 3, 15))

    assert result.state is StreakState.RETURNING
    assert result.current_streak == 0
    assert result.days_since_last_check_in == 2
    # The run is still on the record: longest_streak remembers it.
    assert result.longest_streak == 2
    assert result.freeze_available is True


def test_one_missed_day_is_bridged() -> None:
    """Rule 2: the 13th is empty, and the run survives it."""
    days = [date(2026, 3, 11), date(2026, 3, 12), date(2026, 3, 14), date(2026, 3, 15)]

    result = summary(check_in(*days), date(2026, 3, 15))

    assert result.state is StreakState.TODAY
    assert result.current_streak == 4  # days with a check-in, not the span
    assert result.freezes_used == 1
    assert result.freeze_available is False  # spent in the last 7 days
    assert result.total_check_ins == 4


def test_two_missed_days_in_a_row_reset_the_count_not_the_history() -> None:
    days = [date(2026, 3, 11), date(2026, 3, 12), date(2026, 3, 15)]

    result = summary(check_in(*days), date(2026, 3, 15))

    assert result.state is StreakState.TODAY
    assert result.current_streak == 1
    assert result.longest_streak == 2
    # A new run starts with its allowance intact: coming back is not punished by
    # the previous gap still counting against you.
    assert result.freezes_used == 0
    assert result.freeze_available is True


def test_two_missed_days_then_no_return_is_welcome_back() -> None:
    days = [date(2026, 3, 11), date(2026, 3, 12)]

    result = summary(check_in(*days), date(2026, 3, 15))

    assert result.state is StreakState.RETURNING
    assert result.current_streak == 0
    assert result.days_since_last_check_in == 3
    assert result.longest_streak == 2


def test_one_freeze_per_window_and_not_two() -> None:
    """A second missed day within the window is not bridged by the same allowance.

    Days: 1,2, X, 4…8, X, 10. The 3rd is bridged (window 7 days), the 9th is not:
    a freeze spent on the 9th would sit six days after the one spent on the 3rd.
    """
    days = [date(2026, 3, day) for day in (1, 2, 4, 5, 6, 7, 8, 10)]

    result = summary(check_in(*days), date(2026, 3, 10))

    assert result.current_streak == 6  # the 4th to the 10th, with the 9th bridged
    assert result.current_streak != 8
    assert result.freezes_used == 1
    assert result.longest_streak == 6


def test_a_long_gap_is_not_bridged_by_repeating_freezes() -> None:
    days = [date(2026, 3, 1), date(2026, 3, 10)]

    result = summary(check_in(*days), date(2026, 3, 10))

    assert result.current_streak == 1
    assert result.longest_streak == 1


def test_longest_streak_keeps_the_best_run() -> None:
    days = [
        date(2026, 2, 1),
        date(2026, 2, 2),
        date(2026, 2, 3),
        date(2026, 2, 4),
        # five empty days
        date(2026, 2, 10),
        date(2026, 2, 11),
    ]

    result = summary(check_in(*days), date(2026, 2, 11))

    assert result.current_streak == 2
    assert result.longest_streak == 4


def test_duplicate_check_ins_on_one_day_count_once() -> None:
    first = at(date(2026, 3, 14), hour=8)
    second = at(date(2026, 3, 14), hour=21)

    result = summary([first, second], date(2026, 3, 14))

    assert result.current_streak == 1
    assert result.total_check_ins == 2  # the entries exist…
    assert result.longest_streak == 1  # …but the day is one day


def test_the_rules_are_the_numbers_they_claim_to_be() -> None:
    """Guard rail: these constants *are* the product promise, not tuning knobs."""
    assert CURRENT_RUN_GRACE_DAYS == 1
    assert FREEZE_WINDOW_DAYS == 7
    assert MAX_FREEZES_PER_WINDOW == 1


# --------------------------------------------------------------------------- #
# Timezones: midnight, DST, and the shapes of days                              #
# --------------------------------------------------------------------------- #


def test_midnight_belongs_to_the_local_day_not_the_utc_one() -> None:
    """18:30Z is 00:00 the next day in Kolkata; 18:29Z is still the same evening."""
    just_before = datetime(2026, 3, 10, 18, 29, tzinfo=UTC)
    just_after = datetime(2026, 3, 10, 18, 30, tzinfo=UTC)

    assert local_date(just_before, KOLKATA) == date(2026, 3, 10)
    assert local_date(just_after, KOLKATA) == date(2026, 3, 11)

    # Counted in Kolkata both are check-ins on consecutive days; counted in UTC
    # they would collapse into one day and report a streak of 1.
    kolkata_result = summary([just_before, just_after], date(2026, 3, 11), KOLKATA)
    utc_result = summary([just_before, just_after], date(2026, 3, 11), UTC_ZONE)

    assert kolkata_result.current_streak == 2
    assert utc_result.current_streak == 1
    assert kolkata_result.timezone == "Asia/Kolkata"


def test_spring_forward_still_produces_consecutive_days() -> None:
    """America/New_York, 2026-03-08: 02:00 does not exist. 23 hours, still a day."""
    late_on_the_7th = datetime(2026, 3, 8, 4, 30, tzinfo=UTC)  # 23:30 EST on 03-07
    early_on_the_8th = datetime(2026, 3, 8, 7, 30, tzinfo=UTC)  # 03:30 EDT on 03-08

    assert local_date(late_on_the_7th, NEW_YORK) == date(2026, 3, 7)
    assert local_date(early_on_the_8th, NEW_YORK) == date(2026, 3, 8)

    result = summary([late_on_the_7th, early_on_the_8th], date(2026, 3, 8), NEW_YORK)
    assert result.current_streak == 2
    assert result.state is StreakState.TODAY


def test_fall_back_does_not_double_a_day() -> None:
    """America/New_York, 2026-11-01: 01:30 happens twice. It is one day."""
    first_pass = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 01:30 EDT
    second_pass = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)  # 01:30 EST

    assert local_date(first_pass, NEW_YORK) == date(2026, 11, 1)
    assert local_date(second_pass, NEW_YORK) == date(2026, 11, 1)

    result = summary([first_pass, second_pass], date(2026, 11, 1), NEW_YORK)
    assert result.current_streak == 1
    assert result.total_check_ins == 2


def test_a_traveller_keeps_one_day_per_local_calendar_day() -> None:
    """Check in in Delhi, wake up in New York: the days are still consecutive."""
    delhi_evening = datetime(2026, 3, 9, 20, 0, tzinfo=KOLKATA)  # 14:30Z on 03-09
    new_york_morning = datetime(2026, 3, 10, 13, 0, tzinfo=NEW_YORK)  # 17:00Z on 03-10

    # The streak is counted in whichever zone the caller is in *now* (New York):
    # 03-09 14:30Z is 10:30 EDT on 03-09, and 03-10 17:00Z is 03-10.
    result = summary([delhi_evening, new_york_morning], date(2026, 3, 10), NEW_YORK)
    assert result.current_streak == 2


def test_leap_day_is_an_ordinary_day() -> None:
    days = [date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1)]

    result = summary(check_in(*days), date(2028, 3, 1))
    assert result.current_streak == 3


def test_year_boundary_is_an_ordinary_day() -> None:
    days = [date(2026, 12, 31), date(2027, 1, 1)]

    result = summary(check_in(*days), date(2027, 1, 1))
    assert result.current_streak == 2


def test_naive_timestamps_are_read_as_utc() -> None:
    """SQLite hands timestamps back naive; guessing local time would move a day."""
    naive = datetime(2026, 3, 11, 18, 30)

    assert local_date(naive, KOLKATA) == date(2026, 3, 12)
    assert local_date(datetime(2026, 3, 11, 18, 30, tzinfo=UTC), KOLKATA) == date(2026, 3, 12)


def test_a_missing_timezone_is_utc_and_says_so() -> None:
    zone, fallback = load_timezone(None)

    assert str(zone) == "UTC"
    assert fallback is True
    assert load_timezone("   ")[1] is True
    assert load_timezone("Asia/Kolkata")[0].key == "Asia/Kolkata"
    assert load_timezone("Asia/Kolkata")[1] is False


def test_an_unknown_timezone_fails_loudly() -> None:
    with pytest.raises(UnknownTimezoneError):
        load_timezone("Mars/Olympus_Mons")


def test_summary_reports_the_timezone_it_counted_in() -> None:
    result = compute_streak(
        check_in_instants=[],
        today=date(2026, 3, 15),
        timezone=NEW_YORK,
        timezone_fallback_used=False,
    )

    assert result.timezone == "America/New_York"
    assert result.today == date(2026, 3, 15)
    assert result.timezone_fallback_used is False


# --------------------------------------------------------------------------- #
# The run splitter, on its own                                                  #
# --------------------------------------------------------------------------- #


def test_runs_are_newest_first_and_count_days_not_span() -> None:
    days = [
        date(2026, 3, 1),
        date(2026, 3, 2),
        date(2026, 3, 5),  # one missed day (3rd), bridged; the 4th is not
    ]

    runs = runs_of(days)

    assert [(run.days, run.newest, run.oldest) for run in runs] == [
        (1, date(2026, 3, 5), date(2026, 3, 5)),
        (2, date(2026, 3, 2), date(2026, 3, 1)),
    ]
    assert runs[1].freezes == ()


def test_runs_bridge_a_single_missed_day_and_record_it_as_a_freeze() -> None:
    runs = runs_of([date(2026, 3, 10), date(2026, 3, 12)])

    assert len(runs) == 1
    assert runs[0].days == 2
    assert runs[0].freezes == (date(2026, 3, 11),)


def test_runs_accept_unsorted_duplicate_dates() -> None:
    runs = runs_of([date(2026, 3, 12), date(2026, 3, 10), date(2026, 3, 12)])

    assert len(runs) == 1
    assert runs[0].days == 2
    assert runs[0].newest == date(2026, 3, 12)


def test_a_second_freeze_is_allowed_once_the_window_moves_on() -> None:
    """The allowance is rolling, not one-per-streak.

    Freeze on the 3rd, then eight ordinary days, then a freeze on the 10th: seven
    days after the first, so the window has moved past it. This is the exact
    boundary the 6-day case above fails — the two tests together pin it.
    """
    days = [date(2026, 1, day) for day in (1, 2, 4, 5, 6, 7, 8, 9, 11)]

    result = compute_streak(
        check_in_instants=check_in(*days),
        today=date(2026, 1, 11),
        timezone=UTC_ZONE,
    )

    assert result.current_streak == 9
    assert result.freezes_used == 2
    assert result.freeze_available is False  # the 10th is inside the window


def test_streak_arithmetic_is_independent_of_the_wall_clock() -> None:
    """A pinned ``today`` gives the same answer at either end of the day."""
    days = [date(2026, 3, 14), date(2026, 3, 15)]
    instants = check_in(*days)

    assert summary(instants, date(2026, 3, 15)) == compute_streak(
        check_in_instants=instants,
        today=date(2026, 3, 15),
        timezone=UTC_ZONE,
    )


def test_a_day_has_a_boundary_in_every_zone() -> None:
    """Every zone this product ships content for is resolvable."""
    for name in ("Asia/Kolkata", "America/New_York", "Europe/London", "UTC"):
        zone = ZoneInfo(name)
        moment = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        # Simply: the conversion works and lands on some date.
        assert isinstance(local_date(moment, zone), date)
        assert local_date(moment + timedelta(days=1), zone) > local_date(moment, zone)
