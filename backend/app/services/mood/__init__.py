"""Mood check-ins: the vocabulary a check-in may use, the streak arithmetic, and insights.

Three things live here, all deliberately free of HTTP and of the database:

* :mod:`app.services.mood.taxonomy` — the closed vocabularies (nine emotion
  labels, eight context factors) and the caps that keep a check-in short.
* :mod:`app.services.mood.streak` — the forgiving streak rules, computed in the
  person's own timezone from raw timestamps.
* :mod:`app.services.mood.insights` — pure statistical functions over check-in
  data: rolling averages, factor associations, time-of-day/weekday patterns,
  and a localised weekly summary. No LLM, no causality claims.

The endpoint that ties them to the database and the safety engine is
``app.api.v1.mood``.
"""

from __future__ import annotations

from app.services.mood.insights import (
    BOOTSTRAP_RESAMPLES,
    MIN_ENTRIES_FOR_TREND,
    MIN_ENTRIES_PER_GROUP,
    PERMUTATION_RESAMPLES,
    FactorAssociation,
    RollingPoint,
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
from app.services.mood.streak import (
    CURRENT_RUN_GRACE_DAYS,
    FREEZE_WINDOW_DAYS,
    MAX_FREEZES_PER_WINDOW,
    StreakRun,
    StreakState,
    StreakSummary,
    UnknownTimezoneError,
    compute_streak,
    load_timezone,
    local_date,
    local_today,
    runs_of,
)
from app.services.mood.taxonomy import (
    EMOTION_TAGS,
    FACTOR_PRESENT,
    MAX_EMOTIONS,
    MAX_FACTORS,
    MOOD_FACTORS,
    NEUTRAL_TAG,
    NOTE_MAX_CHARS,
    SCALE_MAX,
    SCALE_MIN,
    factors_document,
    factors_from_document,
    unknown_emotions,
    unknown_factors,
)

__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "CURRENT_RUN_GRACE_DAYS",
    "EMOTION_TAGS",
    "FACTOR_PRESENT",
    "FREEZE_WINDOW_DAYS",
    "MAX_EMOTIONS",
    "MAX_FACTORS",
    "MAX_FREEZES_PER_WINDOW",
    "MIN_ENTRIES_FOR_TREND",
    "MIN_ENTRIES_PER_GROUP",
    "MOOD_FACTORS",
    "NEUTRAL_TAG",
    "NOTE_MAX_CHARS",
    "PERMUTATION_RESAMPLES",
    "SCALE_MAX",
    "SCALE_MIN",
    "Entry",
    "ExportRow",
    "FactorAssociation",
    "RollingPoint",
    "RollingWindowResult",
    "StreakRun",
    "StreakState",
    "StreakSummary",
    "TimePattern",
    "UnknownTimezoneError",
    "WeeklySummary",
    "compute_streak",
    "factor_association",
    "factor_associations_to_csv",
    "factors_document",
    "factors_from_document",
    "load_timezone",
    "local_date",
    "local_today",
    "mood_entries_to_csv",
    "rolling_average",
    "runs_of",
    "time_patterns",
    "time_patterns_to_csv",
    "unknown_emotions",
    "unknown_factors",
    "weekly_summary",
]
