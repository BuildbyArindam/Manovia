"""Daily plan: a transparent, rule-based planner (Day 21).

The whole feature is one pure module, :mod:`app.services.plan.daily_plan`, and
the endpoints that serve it live in :mod:`app.api.v1.plan`. No model, no LLM:
up to three small suggestions a day, each with a "why this?" explanation.
"""

from app.services.plan.daily_plan import (
    ANXIETY_TAGS,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    HABIT_MIN_DAYS,
    LOOKBACK_DAYS,
    LOW_MEAN,
    MAX_SUGGESTIONS,
    MIN_DROP_POINTS,
    MIN_WINDOW,
    MISSED_CHECKIN_DAYS,
    PRIOR_DAYS,
    RECENT_DAYS,
    REST_ACTIVE_DAYS,
    RULES,
    DailyPlan,
    InvalidQuietTimeError,
    MoodSignal,
    PlannerContext,
    PlanPreferences,
    PlanSuggestion,
    Rule,
    build_daily_plan,
    day_part,
    is_quiet_hours,
    parse_quiet_time,
)

__all__ = [
    "ANXIETY_TAGS",
    "DEFAULT_QUIET_END",
    "DEFAULT_QUIET_START",
    "HABIT_MIN_DAYS",
    "LOOKBACK_DAYS",
    "LOW_MEAN",
    "MAX_SUGGESTIONS",
    "MIN_DROP_POINTS",
    "MIN_WINDOW",
    "MISSED_CHECKIN_DAYS",
    "PRIOR_DAYS",
    "RECENT_DAYS",
    "REST_ACTIVE_DAYS",
    "RULES",
    "DailyPlan",
    "InvalidQuietTimeError",
    "MoodSignal",
    "PlanPreferences",
    "PlanSuggestion",
    "PlannerContext",
    "Rule",
    "build_daily_plan",
    "day_part",
    "is_quiet_hours",
    "parse_quiet_time",
]
