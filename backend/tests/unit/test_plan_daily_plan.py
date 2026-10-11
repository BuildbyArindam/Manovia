"""Planner rules: table-driven, with the hard invariants pinned by their own tests.

The rule table here mirrors :data:`app.services.plan.daily_plan.RULES` row by
row: each named scenario builds a context, runs one rule, and asserts the exact
outcome. The invariants the Day 21 task names — never more than three items,
quiet hours respected, a "why" on every suggestion, and item-9 only ever
producing support options — each get a dedicated test so a future rule edit
cannot quietly drop one of them.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import PlanItemKind
from app.services.exercises.engine import ExerciseCatalog
from app.services.plan import daily_plan
from app.services.plan.daily_plan import (
    MAX_SUGGESTIONS,
    MISSED_CHECKIN_DAYS,
    RULES,
    InvalidQuietTimeError,
    MoodSignal,
    PlannerContext,
    PlanPreferences,
    PlanSuggestion,
    _rule_anxiety,
    _rule_exercise_habit,
    _rule_first_check_in,
    _rule_low_mood,
    _rule_missed_check_in,
    _rule_open_thought_record,
    build_daily_plan,
    day_part,
    is_quiet_hours,
    parse_quiet_time,
)

#: A fixed "Sunday morning" so time-of-day behaviour is deterministic.
MORNING = datetime(2026, 10, 11, 9, 0, tzinfo=ZoneInfo("UTC"))
EVENING = MORNING.replace(hour=20)
AFTERNOON = MORNING.replace(hour=14)


def ctx(**overrides: object) -> PlannerContext:
    """A context with every field defaulted to "nothing to report"."""
    defaults: dict[str, object] = {
        "now": MORNING,
        "check_ins": (),
        "last_check_in_at": None,
        "exercise_days": (),
        "exercise_ids_recent": (),
        "open_thought_records": 0,
        "phq9_item9_flagged": False,
        "preferences": PlanPreferences(),
    }
    defaults.update(overrides)
    return PlannerContext(**defaults)  # type: ignore[arg-type]


def mood(days_ago: int, valence: int, emotions: tuple[str, ...] = ()) -> MoodSignal:
    return MoodSignal(
        local_date=MORNING.date() - timedelta(days=days_ago), valence=valence, emotions=emotions
    )


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (4, "evening"),
        (5, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (16, "afternoon"),
        (17, "evening"),
        (23, "evening"),
    ],
)
def test_day_part_boundaries(hour: int, expected: str) -> None:
    assert day_part(MORNING.replace(hour=hour)) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("00:00", time(0, 0)), ("07:05", time(7, 5)), ("22:00", time(22, 0)), ("23:59", time(23, 59))],
)
def test_parse_quiet_time_accepts_valid_bounds(raw: str, expected: time) -> None:
    assert parse_quiet_time(raw) == expected


@pytest.mark.parametrize(
    "raw", ["7:00", "24:00", "12:60", "noon", "1200", "12:5", "", "12:00:00", "ab:cd"]
)
def test_parse_quiet_time_rejects_bad_bounds(raw: str) -> None:
    with pytest.raises(InvalidQuietTimeError):
        parse_quiet_time(raw)


def test_suggestion_requires_a_why() -> None:
    with pytest.raises(ValueError):
        PlanSuggestion(kind=PlanItemKind.CHECK_IN, title="Check in", why="   ")
    with pytest.raises(ValueError):
        PlanSuggestion(kind=PlanItemKind.CHECK_IN, title="", why="Because.")


# --------------------------------------------------------------------------- #
# Quiet hours                                                                  #
# --------------------------------------------------------------------------- #


def test_quiet_hours_disabled_never_quiet() -> None:
    preferences = PlanPreferences(
        quiet_hours_enabled=False, quiet_start=time(0, 0), quiet_end=time(23, 59)
    )
    assert is_quiet_hours(MORNING, preferences) is False


def test_quiet_hours_simple_window() -> None:
    preferences = PlanPreferences(
        quiet_hours_enabled=True, quiet_start=time(22, 0), quiet_end=time(23, 0)
    )
    assert is_quiet_hours(MORNING.replace(hour=22, minute=30), preferences) is True
    assert is_quiet_hours(MORNING.replace(hour=21, minute=59), preferences) is False
    assert is_quiet_hours(MORNING.replace(hour=23, minute=0), preferences) is False


def test_quiet_hours_window_crosses_midnight() -> None:
    preferences = PlanPreferences(
        quiet_hours_enabled=True, quiet_start=time(22, 0), quiet_end=time(7, 0)
    )
    assert is_quiet_hours(MORNING.replace(hour=23, minute=0), preferences) is True
    assert is_quiet_hours(MORNING.replace(hour=6, minute=59), preferences) is True
    assert is_quiet_hours(MORNING.replace(hour=7, minute=0), preferences) is False
    assert is_quiet_hours(MORNING.replace(hour=12, minute=0), preferences) is False


def test_quiet_hours_equal_bounds_cover_the_day() -> None:
    preferences = PlanPreferences(
        quiet_hours_enabled=True, quiet_start=time(8, 0), quiet_end=time(8, 0)
    )
    assert is_quiet_hours(MORNING, preferences) is True


def test_quiet_hours_defaults_apply_when_bounds_missing() -> None:
    preferences = PlanPreferences(quiet_hours_enabled=True)
    assert is_quiet_hours(MORNING.replace(hour=23, minute=0), preferences) is True
    assert is_quiet_hours(MORNING.replace(hour=12, minute=0), preferences) is False


# --------------------------------------------------------------------------- #
# The rule table, row by row                                                    #
# --------------------------------------------------------------------------- #

RULE_TESTS: list[tuple[str, PlannerContext, str | None]] = [
    # -- first_check_in ------------------------------------------------------
    ("first_check_in: a brand-new person gets one gentle invite", ctx(), "first_check_in"),
    (
        "first_check_in: not for somebody with check-ins",
        ctx(check_ins=(mood(0, 4),), last_check_in_at=MORNING),
        None,
    ),
    (
        "first_check_in: a returning person gets the missed invite, not the first one",
        ctx(last_check_in_at=MORNING - timedelta(days=30)),
        "missed_check_in",
    ),
    # -- open_thought_record --------------------------------------------------
    (
        "open_thought_record: one unfinished record fires it",
        ctx(open_thought_records=1, last_check_in_at=MORNING, check_ins=(mood(0, 4),)),
        "open_thought_record",
    ),
    # -- low_mood -------------------------------------------------------------
    (
        "low_mood: a real drop across both windows",
        ctx(
            check_ins=tuple(
                [mood(d, v) for d, v in [(0, 1), (1, 1), (2, 2)]]
                + [mood(d, 4) for d in (3, 4, 5, 6)]
            ),
            last_check_in_at=MORNING,
        ),
        "low_mood",
    ),
    (
        "low_mood: persistently low recent window, no prior data",
        ctx(check_ins=(mood(0, 2), mood(1, 1), mood(2, 2)), last_check_in_at=MORNING),
        "low_mood",
    ),
    (
        "low_mood: stable mood fires nothing",
        ctx(
            check_ins=tuple(mood(d, 4) for d in range(7)),
            last_check_in_at=MORNING,
        ),
        None,
    ),
    (
        "low_mood: a 0.33-point dip is below the effect floor",
        ctx(
            check_ins=tuple(
                [mood(d, v) for d, v in [(0, 3), (1, 3), (2, 2)]]
                + [mood(d, 3) for d in (3, 4, 5, 6)]
            ),
            last_check_in_at=MORNING,
        ),
        None,
    ),
    (
        "low_mood: two recent check-ins are not enough to compare",
        ctx(
            check_ins=(mood(0, 1), mood(1, 1), mood(3, 4), mood(4, 4), mood(5, 4)),
            last_check_in_at=MORNING,
        ),
        None,
    ),
    # -- anxiety ---------------------------------------------------------------
    (
        "anxiety: an anxiety tag within three days",
        ctx(check_ins=(mood(1, 3, ("anxiety",)),), last_check_in_at=MORNING - timedelta(days=1)),
        "anxiety",
    ),
    (
        "anxiety: fear counts too",
        ctx(check_ins=(mood(0, 3, ("fear",)),), last_check_in_at=MORNING),
        "anxiety",
    ),
    (
        "anxiety: a stale tag earns the missed invite, not the anxiety card",
        ctx(
            check_ins=(mood(5, 3, ("anxiety",)),),
            last_check_in_at=MORNING - timedelta(days=5),
        ),
        "missed_check_in",
    ),
    (
        "anxiety: sadness is not an anxiety signal",
        ctx(check_ins=(mood(0, 3, ("sadness",)),), last_check_in_at=MORNING),
        None,
    ),
    # -- missed_check_in ---------------------------------------------------------
    (
        "missed_check_in: three quiet days earn one gentle ask",
        ctx(
            check_ins=(mood(3, 4),),
            last_check_in_at=MORNING - timedelta(days=MISSED_CHECKIN_DAYS + 1),
        ),
        "missed_check_in",
    ),
    (
        "missed_check_in: today's check-in stays quiet",
        ctx(check_ins=(mood(0, 4),), last_check_in_at=MORNING),
        None,
    ),
    (
        "missed_check_in: one missed day is nobody's business",
        ctx(
            check_ins=(mood(1, 4),),
            last_check_in_at=MORNING - timedelta(days=MISSED_CHECKIN_DAYS - 1),
        ),
        None,
    ),
    (
        "missed_check_in: a fortnight away still gets the invite",
        ctx(last_check_in_at=MORNING - timedelta(days=14)),
        "missed_check_in",
    ),
    # -- exercise_habit ------------------------------------------------------------
    (
        "exercise_habit: active this week, nothing yet today",
        ctx(
            exercise_days=(MORNING.date() - timedelta(days=1), MORNING.date() - timedelta(days=3)),
            exercise_ids_recent=("box-breathing",),
            check_ins=(mood(0, 4),),
            last_check_in_at=MORNING,
        ),
        "exercise_habit",
    ),
    (
        "exercise_habit: already exercised today",
        ctx(
            exercise_days=(MORNING.date(),),
            exercise_ids_recent=("box-breathing",),
            check_ins=(mood(0, 4),),
            last_check_in_at=MORNING,
        ),
        None,
    ),
]

RULE_BUILDERS = {
    "first_check_in": _rule_first_check_in,
    "open_thought_record": _rule_open_thought_record,
    "low_mood": _rule_low_mood,
    "anxiety": _rule_anxiety,
    "missed_check_in": _rule_missed_check_in,
    "exercise_habit": _rule_exercise_habit,
}


@pytest.mark.parametrize(
    ("name", "context", "expected_rule"),
    RULE_TESTS,
    ids=[case[0] for case in RULE_TESTS],
)
def test_rule_table(name: str, context: PlannerContext, expected_rule: str | None) -> None:
    fired = [rule_id for rule_id, build in RULE_BUILDERS.items() if build(context) is not None]
    if expected_rule is None:
        expected: list[str] = []
    else:
        expected = [rule_id for rule_id in RULE_BUILDERS if rule_id == expected_rule]
    assert fired == expected, f"{name}: expected {expected}, got {fired}"


def test_every_rule_has_a_unique_id_and_description() -> None:
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
    assert all(rule.description.strip() for rule in RULES)


# --------------------------------------------------------------------------- #
# build_daily_plan: the invariants                                              #
# --------------------------------------------------------------------------- #


def everything_context(**overrides: object) -> PlannerContext:
    """A person for whom every rule wants to fire at once."""
    low_week = [mood(d, v) for d, v in [(0, 1), (1, 1), (2, 2)]]
    stable_before = [mood(d, 4) for d in (3, 4, 5, 6)]
    check_ins = (*low_week, *stable_before, mood(0, 2, ("anxiety",)))
    base = ctx(
        check_ins=check_ins,
        last_check_in_at=MORNING - timedelta(days=MISSED_CHECKIN_DAYS + 1),
        exercise_days=(MORNING.date() - timedelta(days=1),),
        exercise_ids_recent=("box-breathing",),
        open_thought_records=2,
    )
    merged: dict[str, object] = {
        "now": base.now,
        "check_ins": base.check_ins,
        "last_check_in_at": base.last_check_in_at,
        "exercise_days": base.exercise_days,
        "exercise_ids_recent": base.exercise_ids_recent,
        "open_thought_records": base.open_thought_records,
        "phq9_item9_flagged": base.phq9_item9_flagged,
        "preferences": base.preferences,
    }
    merged.update(overrides)
    return PlannerContext(**merged)  # type: ignore[arg-type]


SCENARIOS: list[tuple[str, PlannerContext]] = [
    ("new user", ctx()),
    ("everything fires", everything_context()),
    ("low mood week", everything_context(open_thought_records=0, exercise_days=())),
    (
        "quiet hours with signals",
        everything_context(
            preferences=PlanPreferences(
                quiet_hours_enabled=True, quiet_start=time(0, 0), quiet_end=time(23, 59)
            )
        ),
    ),
    ("item 9 flagged", everything_context(phq9_item9_flagged=True)),
    (
        "item 9 flagged during quiet hours",
        everything_context(
            phq9_item9_flagged=True,
            preferences=PlanPreferences(
                quiet_hours_enabled=True, quiet_start=time(0, 0), quiet_end=time(23, 59)
            ),
        ),
    ),
    (
        "rest day",
        ctx(
            exercise_days=tuple(MORNING.date() - timedelta(days=offset) for offset in (0, 1, 2)),
            check_ins=(mood(0, 4),),
            last_check_in_at=MORNING,
        ),
    ),
]


@pytest.mark.parametrize(("name", "context"), SCENARIOS, ids=[case[0] for case in SCENARIOS])
def test_plan_never_exceeds_three_items_and_kinds_are_unique(
    name: str, context: PlannerContext
) -> None:
    plan = build_daily_plan(context)
    assert len(plan.items) <= MAX_SUGGESTIONS
    kinds = [item.kind for item in plan.items]
    assert len(kinds) == len(set(kinds))


@pytest.mark.parametrize(("name", "context"), SCENARIOS, ids=[case[0] for case in SCENARIOS])
def test_every_suggestion_has_a_why(name: str, context: PlannerContext) -> None:
    plan = build_daily_plan(context)
    for item in plan.items:
        assert item.title.strip(), f"{name}: empty title"
        assert item.why.strip(), f"{name}: empty why"


def test_every_rule_output_has_a_title_and_a_why() -> None:
    """Direct scan of every rule builder across its firing scenarios."""
    for rule in RULES:
        for _, context, expected in RULE_TESTS:
            if expected != rule.id:
                continue
            suggestion = rule.build(context)
            assert suggestion is not None
            assert suggestion.title.strip()
            assert suggestion.why.strip()


def test_priority_order_keeps_three_distinct_kinds() -> None:
    plan = build_daily_plan(everything_context())
    assert [item.kind for item in plan.items] == [
        PlanItemKind.THOUGHT_RECORD,
        PlanItemKind.EXERCISE,
        PlanItemKind.CHECK_IN,
    ]


def test_item9_shows_support_and_nothing_else() -> None:
    plan = build_daily_plan(everything_context(phq9_item9_flagged=True))
    assert plan.support_only is True
    assert len(plan.items) == 1
    assert plan.items[0].kind == PlanItemKind.SUPPORT
    assert plan.items[0].ref is None
    assert "support" in plan.items[0].why.casefold()


def test_item9_support_survives_quiet_hours() -> None:
    plan = build_daily_plan(
        everything_context(
            phq9_item9_flagged=True,
            preferences=PlanPreferences(
                quiet_hours_enabled=True, quiet_start=time(0, 0), quiet_end=time(23, 59)
            ),
        )
    )
    assert plan.quiet_hours_active is True
    assert [item.kind for item in plan.items] == [PlanItemKind.SUPPORT]


def test_quiet_hours_suppress_routine_suggestions() -> None:
    plan = build_daily_plan(
        everything_context(
            preferences=PlanPreferences(
                quiet_hours_enabled=True, quiet_start=time(0, 0), quiet_end=time(23, 59)
            )
        )
    )
    assert plan.quiet_hours_active is True
    assert plan.items == ()


def test_quiet_hours_inactive_still_plans() -> None:
    plan = build_daily_plan(
        everything_context(
            preferences=PlanPreferences(
                quiet_hours_enabled=True, quiet_start=time(1, 0), quiet_end=time(2, 0)
            )
        )
    )
    assert plan.quiet_hours_active is False
    assert plan.items != ()


def test_rest_day_when_active_today_and_the_week_is_busy() -> None:
    plan = build_daily_plan(
        ctx(
            exercise_days=tuple(MORNING.date() - timedelta(days=offset) for offset in (0, 1, 2)),
            check_ins=(mood(0, 4),),
            last_check_in_at=MORNING,
        )
    )
    assert [item.kind for item in plan.items] == [PlanItemKind.REST]
    assert "rest" in plan.items[0].title.casefold()


def test_light_week_gets_an_empty_plan_not_a_rest_card() -> None:
    plan = build_daily_plan(
        ctx(
            exercise_days=(MORNING.date() - timedelta(days=1),),
            check_ins=(mood(0, 4),),
            last_check_in_at=MORNING,
        )
    )
    assert plan.items == ()


def test_time_of_day_changes_the_exercise_pick() -> None:
    low_mood_signals = tuple(
        [mood(d, v) for d, v in [(0, 1), (1, 1), (2, 2)]] + [mood(d, 4) for d in (3, 4, 5, 6)]
    )

    def exercise_pick(now: datetime) -> PlanSuggestion:
        plan = build_daily_plan(ctx(now=now, check_ins=low_mood_signals, last_check_in_at=now))
        return next(item for item in plan.items if item.kind == PlanItemKind.EXERCISE)

    assert exercise_pick(MORNING).ref == "small-action-planner"
    assert exercise_pick(AFTERNOON).ref == "emotion-labelling"
    assert exercise_pick(EVENING).ref == "body-scan"


def test_exercise_habit_suggests_an_evening_exercise_after_six() -> None:
    plan = build_daily_plan(
        ctx(
            now=EVENING,
            exercise_days=(
                EVENING.date() - timedelta(days=1),
                EVENING.date() - timedelta(days=2),
            ),
            exercise_ids_recent=("box-breathing",),
            check_ins=(mood(0, 4),),
            last_check_in_at=EVENING,
        )
    )
    exercises = [item for item in plan.items if item.kind == PlanItemKind.EXERCISE]
    assert [item.ref for item in exercises] == ["worry-time"]


def test_exercise_habit_afternoon_pick() -> None:
    plan = build_daily_plan(
        ctx(
            now=AFTERNOON,
            exercise_days=(
                AFTERNOON.date() - timedelta(days=1),
                AFTERNOON.date() - timedelta(days=2),
            ),
            exercise_ids_recent=("box-breathing",),
            check_ins=(mood(0, 4),),
            last_check_in_at=AFTERNOON,
        )
    )
    exercises = [item for item in plan.items if item.kind == PlanItemKind.EXERCISE]
    assert [item.ref for item in exercises] == ["box-breathing"]


def test_anxiety_in_the_evening_prefers_the_slow_exhale() -> None:
    plan = build_daily_plan(
        ctx(
            now=EVENING,
            check_ins=(mood(0, 3, ("anxiety",)),),
            last_check_in_at=EVENING,
        )
    )
    exercises = [item for item in plan.items if item.kind == PlanItemKind.EXERCISE]
    assert [item.ref for item in exercises] == ["slow-exhale-478"]


def test_preferred_tags_softly_reorder_exercise_choice() -> None:
    signals = (mood(1, 3, ("anxiety",)), mood(0, 4))
    plain = build_daily_plan(ctx(check_ins=signals, last_check_in_at=MORNING))
    breathing = build_daily_plan(
        ctx(
            check_ins=signals,
            last_check_in_at=MORNING,
            preferences=PlanPreferences(preferred_exercise_tags=("breathing",)),
        )
    )
    plain_exercise = next(item for item in plain.items if item.kind == PlanItemKind.EXERCISE)
    breathing_exercise = next(
        item for item in breathing.items if item.kind == PlanItemKind.EXERCISE
    )
    assert plain_exercise.ref == "grounding-54321"
    assert breathing_exercise.ref == "box-breathing"
    assert breathing_exercise.why.strip()


def test_missed_check_in_reports_the_gap() -> None:
    plan = build_daily_plan(ctx(last_check_in_at=MORNING - timedelta(days=5)))
    assert [item.kind for item in plan.items] == [PlanItemKind.CHECK_IN]
    assert "5 days ago" in plan.items[0].why


def test_rules_degrade_to_nothing_when_the_catalogue_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broken content must never produce a suggestion pointing at nothing."""
    monkeypatch.setattr(daily_plan, "get_catalog", lambda: ExerciseCatalog({}))

    low_mood = ctx(
        check_ins=tuple(
            [mood(d, v) for d, v in [(0, 1), (1, 1), (2, 2)]] + [mood(d, 4) for d in (3, 4, 5, 6)]
        ),
        last_check_in_at=MORNING,
    )
    anxious = ctx(check_ins=(mood(0, 3, ("anxiety",)),), last_check_in_at=MORNING)
    habit = ctx(
        exercise_days=(MORNING.date() - timedelta(days=1), MORNING.date() - timedelta(days=2)),
        exercise_ids_recent=("box-breathing",),
        check_ins=(mood(0, 4),),
        last_check_in_at=MORNING,
    )
    for context in (low_mood, anxious, habit):
        plan = build_daily_plan(context)
        assert all(item.kind != PlanItemKind.EXERCISE for item in plan.items)
