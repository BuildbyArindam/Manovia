"""Streaks that cannot be broken by having a bad week.

A streak is a motivational device, and the standard version of it is nasty: miss
one day and a number you were proud of resets to zero. On a mental-health product
that is worse than unkind — it is backwards. The days somebody cannot face a
check-in are exactly the days the app should be easiest to return to. So the
rules here are forgiving *by construction*, and they are the product's rules, not
a setting:

1. **Today is still open.** Not having checked in yet today never ends a streak.
   The day only counts once it is over.
2. **One missed day in any seven is bridged** (a "streak freeze"). Yesterday was
   hard; today still counts as the same run.
3. **Two missed days in a row reset the count, never the welcome.** Coming back
   after a gap produces a "returning" state with gentle copy — no guilt, no lost
   points to recover, nothing to apologise for.
4. **The streak is counted in the person's own timezone.** A check-in at 00:30 in
   Kolkata belongs to that day in Kolkata, not to the previous UTC day. Nothing
   else in this module makes sense without a real zone: the DST days are 23 or 25
   hours long, and a UTC-only streak would silently skip or double a day for
   somebody who moved their clock.

The whole module is a pure function over local dates — no database, no clock of
its own — so every edge (midnight, DST, a leap day, a year-long gap) is a unit
test rather than a hope. :func:`compute_streak` takes the check-in instants and
does the timezone conversion itself, because doing it in a repository would mean
two places that can disagree about where a day ends.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: How many days a freeze is good for: a missed day inside this window counts
#: against the allowance.
FREEZE_WINDOW_DAYS: int = 7

#: How many missed days may be bridged inside one window. One. Two missed days in
#: a row is a reset, which is the deliberate limit of the mechanism — a streak
#: that survives anything is not a streak, it is a counter.
MAX_FREEZES_PER_WINDOW: int = 1

#: A run stays "current" while its newest check-in is today or yesterday. Today
#: is allowed to be empty (rule 1); two empty days are not.
CURRENT_RUN_GRACE_DAYS: int = 1


class UnknownTimezoneError(ValueError):
    """Raised when a caller names a timezone this process cannot resolve.

    Failing loudly is the point: silently computing a streak in UTC for somebody
    who asked for Asia/Kolkata would show them a wrong number on a screen whose
    entire job is to be kind about a number.
    """


def load_timezone(name: str | None, *, default: str = "UTC") -> tuple[ZoneInfo, bool]:
    """Resolve an IANA timezone name.

    Returns ``(zone, fallback_used)``. ``fallback_used`` is true when the caller
    passed nothing and the default was used, which the API reports back so a
    client can see that its streak is being counted in UTC rather than shrug at a
    number that quietly disagrees with the person's calendar.

    Raises :class:`UnknownTimezoneError` for a name the platform does not know.
    """
    if name is None or not name.strip():
        return ZoneInfo(default), True
    try:
        return ZoneInfo(name.strip()), False
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnknownTimezoneError(name) from exc


def local_date(instant: datetime, zone: ZoneInfo) -> date:
    """The calendar day one UTC instant falls on in ``zone``.

    A naive timestamp is read as UTC: every timestamp this application writes is
    UTC-aware (``app.models.base.TIMESTAMP_TYPE``), and SQLite hands some of them
    back naive — treating those as local time would move a check-in across
    midnight depending on the server's TZ environment variable.
    """
    aware = instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)
    return aware.astimezone(zone).date()


def local_today(now: datetime, zone: ZoneInfo) -> date:
    """Today's date in ``zone`` (see :func:`local_date`)."""
    return local_date(now, zone)


class StreakState(StrEnum):
    """How a streak should be *spoken about*, decided once, on the server.

    The client switches copy on this rather than re-deriving intent from
    numbers — the difference between "checked in today" and "welcome back" is a
    product decision, and it belongs in one place.

    There is deliberately no ``"broken"``, ``"lost"`` or ``"at risk"`` state: a
    state machine that can only describe the past gently cannot be rendered
    cruelly by accident.
    """

    #: Nothing recorded yet.
    NEVER = "never"
    #: Checked in today; a run is alive and today counts.
    TODAY = "today"
    #: A run is alive but today is still open (newest check-in was yesterday).
    OPEN = "open"
    #: The newest check-in is two or more local days old. Start again — nothing
    #: was taken away.
    RETURNING = "returning"


@dataclass(frozen=True)
class StreakRun:
    """One unbroken run of check-in days, gaps of one day already bridged.

    ``days`` counts the days with a check-in, so a run of Monday/Wednesday is two
    — the bridged Tuesday is recorded in :attr:`freezes`, not invented as a day
    somebody checked in.
    """

    days: int
    newest: date
    oldest: date
    freezes: tuple[date, ...] = ()


@dataclass(frozen=True)
class StreakSummary:
    """Everything a client needs to talk about a streak, and nothing else.

    All fields are derived numbers and dates. There is no message here on
    purpose: the words live in the UI copy layer, where they can be reviewed as
    copy instead of being assembled by string formatting somewhere in the middle
    of a streak calculation.
    """

    #: The zone the count was made in — echoed so a client can show it.
    timezone: str
    #: True when the caller sent no zone and UTC was assumed.
    timezone_fallback_used: bool
    #: The local date "today" was decided to be.
    today: date
    state: StreakState
    #: Check-in days in the live run (0 when no run is live).
    current_streak: int
    #: The longest run ever, same forgiving rules.
    longest_streak: int
    checked_in_today: bool
    last_check_in: date | None
    days_since_last_check_in: int | None
    total_check_ins: int
    #: Missed days bridged inside the live run.
    freezes_used: int
    #: True when a future missed day would still be bridged: the allowance is
    #: unspent in the window ending today. This is what the UI uses to say
    #: "one quiet day is fine" instead of a countdown.
    freeze_available: bool


def runs_of(dates: Sequence[date]) -> list[StreakRun]:
    """Split check-in days into runs, bridging a single missed day per window.

    ``dates`` may contain duplicates and may be in any order. The result is
    ordered newest run first. Bridging walks the missed days one at a time, so
    four consecutive empty days can never be spanned by one freeze: each empty
    day has to pass the window check on its own.
    """
    unique = sorted(set(dates), reverse=True)
    runs: list[StreakRun] = []

    for day in unique:
        if not runs:
            runs.append(StreakRun(days=1, newest=day, oldest=day))
            continue

        current = runs[-1]
        freezes = list(current.freezes)
        # The walk continues from the run's *oldest* date: the run has been
        # extended many times by now, so ``newest`` is behind us.
        cursor = current.oldest - timedelta(days=1)
        # Walk the empty days between this date and the run. Consecutive dates
        # skip the loop entirely and always extend; anything else has to buy a
        # freeze, one empty day at a time.
        bridged = True

        while cursor > day:
            if _window_allows(cursor, freezes):
                freezes.append(cursor)
            else:
                bridged = False
                break
            cursor -= timedelta(days=1)

        if bridged:
            # The walk runs backwards in time, so ``day`` is now the oldest date
            # in the run; ``newest`` stays where the run started.
            runs[-1] = StreakRun(
                days=current.days + 1,
                newest=current.newest,
                oldest=day,
                freezes=tuple(freezes),
            )
        else:
            runs.append(StreakRun(days=1, newest=day, oldest=day))

    return runs


def _window_allows(day: date, freezes: Sequence[date]) -> bool:
    """True when spending a freeze on ``day`` respects the rolling window.

    ``freezes`` are later in time than ``day`` (the walk runs backwards), so a
    freeze counts against this day when it sits fewer than
    :data:`FREEZE_WINDOW_DAYS` days later — that is what makes two *consecutive*
    missed days impossible to bridge, while a run longer than a week can still
    spend a second freeze once the first has left the window.
    """
    within_window = sum(1 for later in freezes if (later - day).days < FREEZE_WINDOW_DAYS)
    return within_window < MAX_FREEZES_PER_WINDOW


def compute_streak(
    *,
    check_in_instants: Iterable[datetime],
    today: date,
    timezone: ZoneInfo,
    timezone_fallback_used: bool = False,
) -> StreakSummary:
    """Summarise a person's run of check-ins, in their own timezone.

    ``check_in_instants`` are the raw ``created_at`` values; the conversion to
    local days happens here (see the module docstring). ``today`` is passed in so
    a caller — and a test — controls the clock, and the ``today`` the API uses is
    always "now in the user's zone".
    """
    days = [local_date(instant, timezone) for instant in check_in_instants]
    unique_days = set(days)
    runs = runs_of(days)

    newest = runs[0].newest if runs else None
    checked_in_today = today in unique_days
    days_since_last = (today - newest).days if newest is not None else None

    # A run is live while its newest check-in is today or yesterday: today may
    # still be empty, but a second empty day has to mean something.
    live = days_since_last is not None and 0 <= days_since_last <= CURRENT_RUN_GRACE_DAYS
    current = runs[0] if live and runs else None

    if newest is None:
        state = StreakState.NEVER
    elif checked_in_today:
        state = StreakState.TODAY
    elif live:
        state = StreakState.OPEN
    else:
        state = StreakState.RETURNING

    freezes_used = len(current.freezes) if current else 0
    recent_freeze = bool(
        current and any((today - freeze).days < FREEZE_WINDOW_DAYS for freeze in current.freezes)
    )

    return StreakSummary(
        timezone=getattr(timezone, "key", None) or str(timezone),
        timezone_fallback_used=timezone_fallback_used,
        today=today,
        state=state,
        current_streak=current.days if current else 0,
        longest_streak=max((run.days for run in runs), default=0),
        checked_in_today=checked_in_today,
        last_check_in=newest,
        days_since_last_check_in=days_since_last,
        total_check_ins=len(days),
        freezes_used=freezes_used,
        freeze_available=not recent_freeze,
    )
