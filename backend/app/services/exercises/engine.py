"""Validated, audio-free exercise content and deterministic recommendations.

Content is trusted, reviewable data, not model output. A broken file rejects the
whole catalogue at startup; it is never silently omitted. Personal ratings and
completion persistence live behind the authenticated API/repository boundary.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)
from yaml.nodes import MappingNode

from app.services.nlp.base import EMOTIONS

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,63}$")]
PlainText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1600)]
StepCue = Literal["instruction", "inhale", "hold-in", "exhale", "hold-out"]
Intensity = Literal["low", "moderate", "high"]


class ContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class LocalizedTitle(ContentModel):
    """English is the explicit fallback; translated titles do not imply translated steps."""

    en: PlainText
    hi: PlainText | None = None
    bn: PlainText | None = None


class ExerciseChoice(ContentModel):
    id: Identifier
    label: PlainText


class ExerciseStep(ContentModel):
    id: Identifier
    duration_seconds: int = Field(ge=1, le=600)
    instruction: PlainText
    cue: StepCue = "instruction"
    choices: list[ExerciseChoice] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_choices(self) -> Self:
        if len({choice.id for choice in self.choices}) != len(self.choices):
            raise ValueError("choice ids must be unique within a step")
        if self.choices and self.cue != "instruction":
            raise ValueError("choices belong to a text instruction, not a breathing cue")
        return self


class ExerciseDefinition(ContentModel):
    id: Identifier
    title: LocalizedTitle
    instructions_language: Literal["en"] = "en"
    description: PlainText
    duration_seconds: int = Field(ge=1, le=1800)
    steps: list[ExerciseStep] = Field(min_length=1, max_length=100)
    contraindications: list[PlainText] = Field(min_length=1, max_length=12)
    notes: list[PlainText] = Field(min_length=1, max_length=12)
    tags: list[Identifier] = Field(min_length=1, max_length=12)
    evidence_note: PlainText

    @model_validator(mode="after")
    def consistent_timing_and_ids(self) -> Self:
        if sum(step.duration_seconds for step in self.steps) != self.duration_seconds:
            raise ValueError("duration_seconds must equal the sum of the step timings")
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("step ids must be unique within an exercise")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags must be unique")
        if any(step.cue != "instruction" for step in self.steps) and "breathing" not in self.tags:
            raise ValueError("breathing cues require the breathing tag")
        return self

    @property
    def definition_sha256(self) -> str:
        """Provenance for history, independent of YAML whitespace or file order."""
        canonical = json.dumps(self.model_dump(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExerciseContentError(ValueError):
    """Missing, ambiguous, or invalid declarative exercise content."""


class _UniqueKeyLoader(yaml.SafeLoader):  # type: ignore[misc, unused-ignore]
    """SafeLoader otherwise silently overwrites a repeated YAML key."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[object] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise yaml.constructor.ConstructorError(
                        None, None, "duplicate YAML mapping key", key_node.start_mark
                    )
                seen.add(key)
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    None, None, "YAML mapping keys must be scalar", key_node.start_mark
                ) from exc
        return super().construct_mapping(node, deep=deep)  # type: ignore[no-any-return, unused-ignore]


class ExerciseCatalog:
    def __init__(self, definitions: Mapping[str, ExerciseDefinition]) -> None:
        self._definitions = dict(sorted(definitions.items()))

    def list(self) -> tuple[ExerciseDefinition, ...]:
        return tuple(self._definitions.values())

    def get(self, exercise_id: str) -> ExerciseDefinition | None:
        return self._definitions.get(exercise_id)


def load_catalog(directory: Path | None = None) -> ExerciseCatalog:
    """Load every YAML file with safe constructors and strict Pydantic validation."""
    root = directory if directory is not None else Path(str(files("app.content") / "exercises"))
    paths = sorted([*root.glob("*.yaml"), *root.glob("*.yml")])
    if not paths:
        raise ExerciseContentError(f"no exercise YAML files in {root}")
    definitions: dict[str, ExerciseDefinition] = {}
    for path in paths:
        try:
            with path.open(encoding="utf-8") as handle:
                # This loader inherits only SafeLoader constructors; Python tags
                # and duplicate mapping keys are rejected.
                raw = yaml.load(handle, Loader=_UniqueKeyLoader)
            exercise = ExerciseDefinition.model_validate(raw)
        except (OSError, yaml.YAMLError, ValidationError) as exc:
            raise ExerciseContentError(f"invalid exercise file {path.name}: {exc}") from exc
        if path.stem != exercise.id:
            raise ExerciseContentError(f"{path.name}: filename must match the exercise id")
        if exercise.id in definitions:
            raise ExerciseContentError(f"{path.name}: duplicate exercise id {exercise.id}")
        definitions[exercise.id] = exercise
    return ExerciseCatalog(definitions)


@lru_cache(maxsize=1)
def get_catalog() -> ExerciseCatalog:
    return load_catalog()


class Recommendation(ContentModel):
    exercise_id: Identifier
    why: PlainText


def recommend(emotion: str, intensity: Intensity = "moderate") -> list[Recommendation]:
    """Small, auditable rules; never a diagnosis or a prediction of benefit.

    Intensity is a person's choice, not an inferred clinical severity score.
    There is no model call, message analysis, or persistence in this function.
    """
    if emotion not in EMOTIONS:
        raise ValueError("unknown emotion")
    if intensity not in ("low", "moderate", "high"):
        raise ValueError("unknown intensity")

    if emotion in ("anxiety", "fear"):
        if intensity == "high":
            choices = (
                (
                    "grounding-54321",
                    "Noticing your surroundings offers an outside focus "
                    "without changing your breathing.",
                ),
                (
                    "box-breathing",
                    "Counting offers a steady pace to follow. Skip holds or "
                    "choose grounding if breathing feels uncomfortable.",
                ),
            )
        else:
            choices = (
                (
                    "slow-exhale-478",
                    "A gentle, longer out-breath offers a rhythm to try. "
                    "The hold is optional; normal breathing is always fine.",
                ),
                (
                    "worry-time",
                    "A short, bounded pause offers space to separate a "
                    "worry from one possible next step.",
                ),
            )
    elif emotion == "anger":
        choices = (
            (
                "muscle-relaxation",
                "Noticing gentle tension and release is one way to "
                "check in with your body; skip sore areas.",
            ),
            (
                "emotion-labelling",
                "Putting a tentative name to a feeling offers a way to "
                "describe it without judging it.",
            ),
        )
    elif emotion in ("sadness", "loneliness"):
        choices = (
            (
                "small-action-planner",
                "Choosing one small, manageable action does not require feeling motivated first.",
            ),
            (
                "emotion-labelling",
                "You can name what is here without needing to change it or explain it perfectly.",
            ),
        )
    elif emotion == "shame":
        choices = (
            ("emotion-labelling", "A tentative label describes a feeling, not who you are."),
            (
                "grounding-54321",
                "An outside focus is available if looking inward feels uncomfortable.",
            ),
        )
    else:
        choices = (
            (
                "body-scan",
                "A brief check-in offers time to notice ordinary "
                "sensations, with no need to find anything special.",
            ),
            (
                "small-action-planner",
                "One small action can fit the time and energy you have right now.",
            ),
        )
    prefix = f"You selected {intensity} {emotion}. "
    return [
        Recommendation(exercise_id=exercise_id, why=prefix + reason)
        for exercise_id, reason in choices
    ]


def rating_summary(pre_rating: int | None, post_rating: int | None) -> tuple[int | None, str]:
    """Report association only. Missing ratings are not treated as zero."""
    if pre_rating is None or post_rating is None:
        return (
            None,
            "Completed. Both optional ratings are needed for a before-and-after comparison.",
        )
    change = post_rating - pre_rating
    if change == 0:
        observation = f"You reported the same rating before and after ({pre_rating} of 5)."
    else:
        direction = "higher" if change > 0 else "lower"
        points = "point" if abs(change) == 1 else "points"
        observation = (
            f"You reported feeling {abs(change)} {points} {direction} afterwards "
            f"({pre_rating} to {post_rating} of 5)."
        )
    return (
        change,
        observation + " This is an observation, not proof that the exercise caused a change.",
    )
