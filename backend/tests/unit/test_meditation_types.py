"""Day 45: the meditation request/response models.

The models are the first safety gate: closed vocabularies mean an unknown
theme, tag or context is a 422, never a silent default — a silent default
is how a closed-eye script would end up for somebody driving.
"""

from __future__ import annotations

import typing

import pytest
from pydantic import ValidationError

from app.services.meditation.types import (
    MAX_DURATION_SECONDS,
    MAX_VOICE_SPEED,
    MIN_DURATION_SECONDS,
    MIN_VOICE_SPEED,
    PREFERENCE_TAGS,
    AudioRequest,
    MeditationRequest,
    MeditationScript,
    ScriptSafety,
    ScriptSection,
)


def _tag_literals() -> set[str]:
    """Extract the Literal members of ``preferences`` without duplication.

    The point of the test is that the frozenset used by the generator and
    the Literal the model validates against are the same set — a drift would
    let one side accept a tag the other silently drops.
    """
    ann = MeditationRequest.model_fields["preferences"].annotation
    origin = typing.get_origin(ann)
    assert origin is list
    literal = typing.get_args(ann)[0]
    assert typing.get_origin(literal) is typing.Literal
    return set(typing.get_args(literal))


def test_preference_tags_match_the_request_literal() -> None:
    assert _tag_literals() == PREFERENCE_TAGS


def test_defaults_are_a_full_request() -> None:
    request = MeditationRequest()
    assert request.theme == "auto"
    assert request.duration_seconds == 180
    assert request.voice_speed == 1.0
    assert request.language == "en"
    assert request.context == "unknown"
    assert request.eyes_open is True
    assert request.preferences == []
    assert request.seed is None
    assert request.hour_of_day is None


@pytest.mark.parametrize("duration", [MIN_DURATION_SECONDS - 1, MAX_DURATION_SECONDS + 1])
def test_duration_bounds(duration: int) -> None:
    with pytest.raises(ValidationError):
        MeditationRequest(duration_seconds=duration)


@pytest.mark.parametrize("speed", [MIN_VOICE_SPEED - 0.01, MAX_VOICE_SPEED + 0.01])
def test_speed_bounds(speed: float) -> None:
    with pytest.raises(ValidationError):
        MeditationRequest(voice_speed=speed)


@pytest.mark.parametrize("theme", ["relax", "MORNING", ""])
def test_theme_is_closed(theme: str) -> None:
    with pytest.raises(ValidationError):
        MeditationRequest(theme=theme)  # type: ignore[arg-type]


@pytest.mark.parametrize("hour", [-1, 24])
def test_hour_bounds(hour: int) -> None:
    with pytest.raises(ValidationError):
        MeditationRequest(hour_of_day=hour)  # type: ignore[arg-type]


def test_unknown_preference_tag_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MeditationRequest(preferences=["gentle"])


def test_more_than_four_preferences_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MeditationRequest(preferences=["quiet", "nature", "body", "warmth", "rain"])


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        MeditationRequest(mood="tired")  # type: ignore[call-arg]


@pytest.mark.parametrize("context", ["walking", "driving"])
def test_closed_eyes_require_a_still_body(context: str) -> None:
    with pytest.raises(ValidationError):
        MeditationRequest(context=context, eyes_open=False)  # type: ignore[arg-type]


def test_closed_eyes_are_fine_seated() -> None:
    request = MeditationRequest(context="seated", eyes_open=False)
    assert request.eyes_open is False


def test_seed_bounds() -> None:
    with pytest.raises(ValidationError):
        MeditationRequest(seed=-1)
    with pytest.raises(ValidationError):
        MeditationRequest(seed=2**31)
    assert MeditationRequest(seed=2**31 - 1).seed == 2**31 - 1


def test_request_is_frozen() -> None:
    request = MeditationRequest()
    with pytest.raises(ValidationError):
        request.theme = "calm"  # type: ignore[misc]


def test_script_sections_must_end_after_start() -> None:
    with pytest.raises(ValidationError):
        ScriptSection(
            id="passage-01",
            role="passage",
            text="hello",
            source="curated",
            pause_before_ms=0,
            start_s=10.0,
            end_s=5.0,
        )


def test_safety_booleans_are_closed() -> None:
    safety = ScriptSafety(
        safety_preface=True,
        stop_cue=True,
        eyes_open=True,
        driving_warning=False,
        eyes_closed_allowed=False,
    )
    assert safety.driving_warning is False


def test_audio_request_round_trips_a_script() -> None:
    script = MeditationScript(
        id="abc",
        content_hash="h" * 64,
        theme="calm",
        resolved_theme="calm",
        language="en",
        duration_seconds=180,
        voice_speed=1.0,
        context="seated",
        eyes_open=True,
        preferences=[],
        seed=1,
        word_count=10,
        estimated_duration_s=180.0,
        sections=[
            ScriptSection(
                id="preface-01",
                role="preface",
                text="hello there friend",
                source="curated",
                pause_before_ms=0,
                start_s=0.0,
                end_s=3.0,
            )
        ],
        transcript="hello there friend",
        safety=ScriptSafety(
            safety_preface=True, stop_cue=True, eyes_open=True, driving_warning=False,
            eyes_closed_allowed=False,
        ),
    )
    request = AudioRequest(script=script)
    assert request.script.transcript == script.transcript
    dumped = request.model_dump()
    rebuilt = AudioRequest.model_validate(dumped)
    assert rebuilt.script.content_hash == script.content_hash
