"""Strict YAML, content invariants, rules, and non-causal rating summaries."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.services.exercises.engine import (
    ExerciseContentError,
    ExerciseDefinition,
    Intensity,
    get_catalog,
    load_catalog,
    rating_summary,
    recommend,
)
from app.services.nlp.base import EMOTIONS

DURATIONS = {
    "body-scan": 120,
    "box-breathing": 120,
    "emotion-labelling": 90,
    "grounding-54321": 180,
    "muscle-relaxation": 180,
    "slow-exhale-478": 120,
    "small-action-planner": 120,
    "worry-time": 120,
}


def definition() -> dict[str, Any]:
    exercise = get_catalog().get("body-scan")
    assert exercise is not None
    return copy.deepcopy(exercise.model_dump())


def test_all_eight_files_validate_and_have_exact_timings() -> None:
    exercises = load_catalog().list()
    assert {item.id: item.duration_seconds for item in exercises} == DURATIONS
    for item in exercises:
        assert sum(step.duration_seconds for step in item.steps) == item.duration_seconds
        assert item.title.en and item.title.hi and item.title.bn
        assert item.instructions_language == "en"
        assert all(step.instruction.strip() for step in item.steps)
        assert item.contraindications and item.notes and item.evidence_note
        assert len(item.definition_sha256) == 64
    assert get_catalog() is get_catalog()
    assert get_catalog().get("not-an-exercise") is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "not a slug"),
        ("title", {}),
        ("title", {"en": "   "}),
        ("duration_seconds", 0),
        ("duration_seconds", "120"),
        ("duration_seconds", True),
        ("duration_seconds", 119),
        ("steps", []),
        ("contraindications", []),
        ("notes", []),
        ("tags", ["attention", "attention"]),
        ("tags", ["bad tag"]),
        ("evidence_note", " "),
        ("instructions_language", "hi"),
    ],
)
def test_invalid_content_is_not_coerced(field: str, value: object) -> None:
    raw = definition()
    raw[field] = value
    with pytest.raises(ValidationError):
        ExerciseDefinition.model_validate(raw)


@pytest.mark.parametrize("value", [0, -1, 601, "15", True, 1.5])
def test_step_timing_is_a_positive_strict_integer(value: object) -> None:
    raw = definition()
    raw["steps"][0]["duration_seconds"] = value
    with pytest.raises(ValidationError):
        ExerciseDefinition.model_validate(raw)


@pytest.mark.parametrize("field", ["instruction", "cue", "id"])
def test_bad_step_fields_are_rejected(field: str) -> None:
    raw = definition()
    raw["steps"][0][field] = "" if field != "cue" else "play-audio"
    with pytest.raises(ValidationError):
        ExerciseDefinition.model_validate(raw)


def test_duplicate_steps_and_unknown_fields_are_rejected() -> None:
    raw = definition()
    raw["steps"][1]["id"] = raw["steps"][0]["id"]
    with pytest.raises(ValidationError, match="step ids must be unique"):
        ExerciseDefinition.model_validate(raw)
    raw = definition()
    raw["audio_url"] = "ignored-fields-are-not-okay"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ExerciseDefinition.model_validate(raw)


def test_breathing_cues_require_a_breathing_tag() -> None:
    raw = definition()
    raw["steps"][0]["cue"] = "inhale"
    with pytest.raises(ValidationError, match="breathing tag"):
        ExerciseDefinition.model_validate(raw)


def test_choices_are_unique_and_only_on_text_steps() -> None:
    raw = definition()
    raw["steps"][0]["choices"] = [{"id": "water", "label": "Water"}] * 2
    with pytest.raises(ValidationError, match="choice ids must be unique"):
        ExerciseDefinition.model_validate(raw)
    raw["steps"][0]["choices"] = [{"id": "water", "label": "Water"}]
    raw["steps"][0]["cue"] = "inhale"
    with pytest.raises(ValidationError, match="not a breathing cue"):
        ExerciseDefinition.model_validate(raw)


def test_a_bad_yaml_file_rejects_the_whole_directory(tmp_path: Path) -> None:
    raw = definition()
    (tmp_path / "body-scan.yaml").write_text(yaml.safe_dump(raw))
    raw["duration_seconds"] = 999
    (tmp_path / "broken.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ExerciseContentError, match=r"broken\.yaml"):
        load_catalog(tmp_path)


@pytest.mark.parametrize(
    "text", ["id: one\nid: two\n", "[unterminated", "!!python/object/apply:os.system ['false']"]
)
def test_ambiguous_invalid_and_unsafe_yaml_are_rejected(tmp_path: Path, text: str) -> None:
    (tmp_path / "body-scan.yaml").write_text(text)
    with pytest.raises(ExerciseContentError):
        load_catalog(tmp_path)


def test_empty_directory_and_filename_mismatch_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ExerciseContentError, match="no exercise YAML"):
        load_catalog(tmp_path)
    (tmp_path / "wrong-name.yaml").write_text(yaml.safe_dump(definition()))
    with pytest.raises(ExerciseContentError, match="filename must match"):
        load_catalog(tmp_path)


def test_duplicate_file_ids_are_rejected(tmp_path: Path) -> None:
    text = yaml.safe_dump(definition())
    (tmp_path / "body-scan.yaml").write_text(text)
    (tmp_path / "body-scan.yml").write_text(text)
    with pytest.raises(ExerciseContentError, match="duplicate exercise id"):
        load_catalog(tmp_path)


def test_invalid_catalogue_prevents_application_startup(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> None:
        raise ExerciseContentError("fixture: bad exercise file")

    monkeypatch.setattr("app.main.get_catalog", broken)
    with pytest.raises(ExerciseContentError, match="bad exercise file"):
        create_app(settings)


@pytest.mark.parametrize("emotion", EMOTIONS)
@pytest.mark.parametrize("intensity", ["low", "moderate", "high"])
def test_every_rule_returns_existing_exercises_with_a_why(
    emotion: str, intensity: Intensity
) -> None:
    suggestions = recommend(emotion, intensity)
    assert len(suggestions) == 2
    assert len({suggestion.exercise_id for suggestion in suggestions}) == 2
    for suggestion in suggestions:
        assert get_catalog().get(suggestion.exercise_id) is not None
        assert suggestion.why.startswith(f"You selected {intensity} {emotion}.")
        assert len(suggestion.why) > 50


@pytest.mark.parametrize(
    ("emotion", "intensity", "expected"),
    [
        ("anxiety", "high", ["grounding-54321", "box-breathing"]),
        ("fear", "high", ["grounding-54321", "box-breathing"]),
        ("anxiety", "low", ["slow-exhale-478", "worry-time"]),
        ("anxiety", "moderate", ["slow-exhale-478", "worry-time"]),
        ("sadness", "high", ["small-action-planner", "emotion-labelling"]),
        ("loneliness", "moderate", ["small-action-planner", "emotion-labelling"]),
        ("anger", "high", ["muscle-relaxation", "emotion-labelling"]),
        ("shame", "low", ["emotion-labelling", "grounding-54321"]),
        ("neutral", "moderate", ["body-scan", "small-action-planner"]),
    ],
)
def test_recommendation_rules_are_explicit(
    emotion: str, intensity: Intensity, expected: list[str]
) -> None:
    assert [suggestion.exercise_id for suggestion in recommend(emotion, intensity)] == expected


def test_unknown_emotion_and_intensity_are_not_silently_replaced() -> None:
    with pytest.raises(ValueError, match="unknown emotion"):
        recommend("diagnosis")
    with pytest.raises(ValueError, match="unknown intensity"):
        recommend("anxiety", "urgent")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("pre", "post", "change", "words"),
    [
        (2, 4, 2, "2 points higher"),
        (4, 2, -2, "2 points lower"),
        (3, 4, 1, "1 point higher"),
        (4, 3, -1, "1 point lower"),
        (3, 3, 0, "same rating"),
    ],
)
def test_rating_comparisons_are_associative(pre: int, post: int, change: int, words: str) -> None:
    delta, summary = rating_summary(pre, post)
    assert delta == change
    assert words in summary
    assert "not proof that the exercise caused a change" in summary


@pytest.mark.parametrize(("pre", "post"), [(None, None), (None, 4), (2, None)])
def test_missing_ratings_are_not_zero(pre: int | None, post: int | None) -> None:
    change, summary = rating_summary(pre, post)
    assert change is None
    assert "Both optional ratings" in summary
