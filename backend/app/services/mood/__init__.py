"""Mood check-ins: the vocabulary a check-in may use, and the streak arithmetic.

Two things live here, both deliberately free of HTTP and of the database:

* :mod:`app.services.mood.taxonomy` — the closed vocabularies (nine emotion
  labels, eight context factors) and the caps that keep a check-in short.
* :mod:`app.services.mood.streak` — the forgiving streak rules, computed in the
  person's own timezone from raw timestamps.

The endpoint that ties them to the database and the safety engine is
``app.api.v1.mood``.
"""

from __future__ import annotations

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
    "CURRENT_RUN_GRACE_DAYS",
    "EMOTION_TAGS",
    "FACTOR_PRESENT",
    "FREEZE_WINDOW_DAYS",
    "MAX_EMOTIONS",
    "MAX_FACTORS",
    "MAX_FREEZES_PER_WINDOW",
    "MOOD_FACTORS",
    "NEUTRAL_TAG",
    "NOTE_MAX_CHARS",
    "SCALE_MAX",
    "SCALE_MIN",
    "StreakRun",
    "StreakState",
    "StreakSummary",
    "UnknownTimezoneError",
    "compute_streak",
    "factors_document",
    "factors_from_document",
    "load_timezone",
    "local_date",
    "local_today",
    "runs_of",
    "unknown_emotions",
    "unknown_factors",
]
