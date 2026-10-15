"""Explicit, reviewed action allow-list for the contextual intervention policy.

The exercise definitions remain the source of copy and tags; this module is the
additional allow-list the learner must pass through. Adding a new exercise to
the YAML catalogue does *not* automatically make it learnable. The support
option is a deterministic safety response and is deliberately excluded from the
policy arms.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from app.models.enums import PlanItemKind
from app.services.exercises.engine import ExerciseCatalog, get_catalog

CURATED_EXERCISE_IDS: Final[tuple[str, ...]] = (
    "body-scan",
    "box-breathing",
    "emotion-labelling",
    "grounding-54321",
    "muscle-relaxation",
    "slow-exhale-478",
    "small-action-planner",
    "worry-time",
)

#: Existing daily-plan actions that are safe, bounded choices. ``support`` is
#: kept separate below because safety support is never an arm for exploration.
CURATED_PLAN_ACTIONS: Final[tuple[tuple[str, PlanItemKind, str, tuple[str, ...]], ...]] = (
    ("plan-check-in", PlanItemKind.CHECK_IN, "A gentle check-in", ("emotion-awareness",)),
    (
        "plan-thought-record",
        PlanItemKind.THOUGHT_RECORD,
        "A thought record",
        ("planning", "emotion-awareness"),
    ),
    ("plan-rest", PlanItemKind.REST, "A rest day", ("mindfulness", "body-awareness")),
)

SUPPORT_ACTION_ID: Final[str] = "support-options"
POLICY_ACTION_IDS: Final[tuple[str, ...]] = CURATED_EXERCISE_IDS + tuple(
    action_id for action_id, _kind, _title, _tags in CURATED_PLAN_ACTIONS
)
PUBLIC_CURATED_ACTION_IDS: Final[tuple[str, ...]] = (*POLICY_ACTION_IDS, SUPPORT_ACTION_ID)


@dataclass(frozen=True, slots=True)
class CuratedAction:
    """One reviewed action exposed to the safe policy wrapper."""

    action_id: str
    kind: PlanItemKind
    title: str
    ref: str | None
    tags: tuple[str, ...]


@lru_cache(maxsize=1)
def curated_actions(*, include_support: bool = False) -> tuple[CuratedAction, ...]:
    """Return only the explicit allow-list, validating every exercise ref."""

    catalog: ExerciseCatalog = get_catalog()
    actions: list[CuratedAction] = []
    for exercise_id in CURATED_EXERCISE_IDS:
        definition = catalog.get(exercise_id)
        if definition is None:
            raise RuntimeError(f"curated exercise {exercise_id!r} is missing from the catalogue")
        actions.append(
            CuratedAction(
                action_id=exercise_id,
                kind=PlanItemKind.EXERCISE,
                title=definition.title.en,
                ref=exercise_id,
                tags=tuple(definition.tags),
            )
        )
    actions.extend(
        CuratedAction(
            action_id=action_id,
            kind=kind,
            title=title,
            ref=None,
            tags=tags,
        )
        for action_id, kind, title, tags in CURATED_PLAN_ACTIONS
    )
    if include_support:
        actions.append(
            CuratedAction(
                action_id=SUPPORT_ACTION_ID,
                kind=PlanItemKind.SUPPORT,
                title="Support options are here",
                ref=None,
                tags=("support",),
            )
        )
    return tuple(actions)


def curated_action_ids(*, include_support: bool = False) -> tuple[str, ...]:
    """Stable allow-list ids; unknown catalog additions are never included."""

    ids = PUBLIC_CURATED_ACTION_IDS if include_support else POLICY_ACTION_IDS
    return ids


def get_curated_action(action_id: str, *, include_support: bool = True) -> CuratedAction | None:
    """Look up a reviewed action, optionally including deterministic support."""

    return next(
        (
            action
            for action in curated_actions(include_support=include_support)
            if action.action_id == action_id
        ),
        None,
    )


@lru_cache(maxsize=1)
def curated_preference_tags() -> frozenset[str]:
    """Closed-vocabulary preferences accepted by the feature encoder."""

    return frozenset(tag for action in curated_actions() for tag in action.tags if tag != "support")


def intersect_curated_actions(action_ids: Iterable[str] | None) -> tuple[str, ...]:
    """Restrict an optional caller candidate set; it can never expand the allow-list."""

    if action_ids is None:
        return POLICY_ACTION_IDS
    requested = frozenset(action_ids)
    return tuple(action_id for action_id in POLICY_ACTION_IDS if action_id in requested)
