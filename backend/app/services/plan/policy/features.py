"""Closed-vocabulary context encoding and feature-level explanation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.services.nlp.base import EMOTIONS
from app.services.plan.policy.actions import (
    CURATED_EXERCISE_IDS,
    CuratedAction,
    curated_preference_tags,
)

FEATURE_VERSION: Final[int] = 1
EMOTION_LABELS: Final[tuple[str, ...]] = tuple(EMOTIONS)
FEATURE_NAMES: Final[tuple[str, ...]] = (
    "baseline",
    "time_morning",
    "time_afternoon",
    "time_evening",
    "valence_centered",
    "valence_missing",
    "energy_centered",
    "energy_missing",
    *(f"emotion_{label}" for label in EMOTION_LABELS),
    "preference_match",
    "recent_exercise",
)


@dataclass(frozen=True, slots=True)
class InterventionContext:
    """Safe, numeric and closed-vocabulary features; this type has no text field."""

    local_hour: int
    valence: int | None = None
    energy: int | None = None
    emotion_labels: tuple[str, ...] = ()
    recent_exercise_ids: tuple[str, ...] = ()
    preferred_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.local_hour <= 23:
            raise ValueError("local_hour must be in 0..23")
        if self.valence is not None and not 1 <= self.valence <= 5:
            raise ValueError("valence must be None or an integer in 1..5")
        if self.energy is not None and not 1 <= self.energy <= 5:
            raise ValueError("energy must be None or an integer in 1..5")
        if not set(self.emotion_labels) <= set(EMOTION_LABELS):
            raise ValueError("emotion_labels must use the reviewed emotion vocabulary")
        if len(self.emotion_labels) != len(set(self.emotion_labels)):
            raise ValueError("emotion_labels must be unique")
        if len(self.recent_exercise_ids) > 14:
            raise ValueError("recent_exercise_ids is limited to the latest 14 exercises")
        if not set(self.recent_exercise_ids) <= set(CURATED_EXERCISE_IDS):
            raise ValueError("recent_exercise_ids must contain curated exercise ids only")
        if not set(self.preferred_tags) <= curated_preference_tags():
            raise ValueError("preferred_tags must use the curated action-tag vocabulary")
        if len(self.preferred_tags) != len(set(self.preferred_tags)):
            raise ValueError("preferred_tags must be unique")


@dataclass(frozen=True, slots=True)
class EncodedFeatures:
    """A named feature vector plus reviewable, context-derived explanation terms."""

    values: tuple[float, ...]
    names: tuple[str, ...]
    explanation_terms: tuple[str | None, ...]

    def __post_init__(self) -> None:
        if len(self.values) != len(self.names) or len(self.values) != len(self.explanation_terms):
            raise ValueError("feature values, names and explanations must have equal lengths")


def _day_part(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    return "evening"


def encode_context(context: InterventionContext, action: CuratedAction) -> EncodedFeatures:
    """Encode current context for one arm; action-match features are arm-specific."""

    part = _day_part(context.local_hour)
    valence_value = 0.0 if context.valence is None else (context.valence - 3.0) / 2.0
    energy_value = 0.0 if context.energy is None else (context.energy - 3.0) / 2.0
    emotion_set = set(context.emotion_labels)
    preferred = bool(set(context.preferred_tags) & set(action.tags))
    recently_used = action.action_id in context.recent_exercise_ids

    values: list[float] = [
        1.0,
        float(part == "morning"),
        float(part == "afternoon"),
        float(part == "evening"),
        valence_value,
        float(context.valence is None),
        energy_value,
        float(context.energy is None),
    ]
    values.extend(float(label in emotion_set) for label in EMOTION_LABELS)
    values.extend((float(preferred), float(recently_used)))

    terms: list[str | None] = [
        "the shared starting point",
        "it is morning",
        "it is afternoon",
        "it is evening",
        "your valence rating is higher" if valence_value > 0 else "your valence rating is lower",
        None,
        "your energy rating is high" if energy_value > 0 else "your energy rating is low",
        None,
    ]
    terms.extend(
        f"your emotion labels include {label}" if label in emotion_set else None
        for label in EMOTION_LABELS
    )
    terms.extend(
        (
            "it matches your chosen activity preferences" if preferred else None,
            "you have tried this activity recently" if recently_used else None,
        )
    )
    return EncodedFeatures(tuple(values), FEATURE_NAMES, tuple(terms))
