"""The check-in request schema, validated where the developer can read the error.

The HTTP envelope deliberately says only "Request validation failed" (validation
details can echo user input, see ``app.core.errors``), which is right for the wire
and useless for debugging. So the *useful* error messages are asserted here, on
the schema itself: a client author who sends four emotions must be told which one
was wrong and what the allowed set is, without shipping a stack trace.

These tests also pin the two behaviours that protect the data rather than the
caller: tags are folded (never silently dropped *and* never rejected for a double
tap) and a whitespace-only note becomes no note at all.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.v1.mood import MoodCheckInIn
from app.services.mood import EMOTION_TAGS, MAX_EMOTIONS, MAX_FACTORS, MOOD_FACTORS, NOTE_MAX_CHARS


def valid(**overrides: object) -> MoodCheckInIn:
    body: dict[str, object] = {"valence": 3, "energy": 3}
    body.update(overrides)
    return MoodCheckInIn(**body)


@pytest.mark.parametrize("field", ["valence", "energy"])
@pytest.mark.parametrize("value", [0, -1, 6, 99])
def test_scale_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError) as caught:
        valid(**{field: value})

    assert field in str(caught.value)


def test_the_scales_accept_their_whole_range() -> None:
    for value in (1, 2, 3, 4, 5):
        assert valid(valence=value, energy=value).valence == value


def test_too_many_emotions_names_the_field() -> None:
    with pytest.raises(ValidationError) as caught:
        valid(emotions=list(EMOTION_TAGS[: MAX_EMOTIONS + 1]))

    message = str(caught.value)
    assert "emotions" in message
    assert str(MAX_EMOTIONS) in message


def test_too_many_factors_names_the_field() -> None:
    with pytest.raises(ValidationError) as caught:
        valid(factors=list(MOOD_FACTORS[: MAX_FACTORS + 1]))

    assert "factors" in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("emotions", ["confusion"]),
        ("emotions", ["joy", "joyful"]),
        ("factors", ["moon"]),
        ("factors", ["work", "deadlines"]),
    ],
)
def test_an_unknown_tag_is_refused_with_the_allowed_set(field: str, value: list[str]) -> None:
    with pytest.raises(ValidationError) as caught:
        valid(**{field: value})

    message = str(caught.value)
    for offending in value:
        if offending not in (EMOTION_TAGS if field == "emotions" else MOOD_FACTORS):
            assert offending in message
    allowed = "emotions" if field == "emotions" else "factors"
    # The message lists what is allowed, so a client author can fix it in one go.
    assert "allowed" in message
    assert ("sleep" if allowed == "factors" else "joy") in message


def test_every_taxonomy_label_is_accepted() -> None:
    """A vocabulary entry the schema rejects would be a chip that always 422s."""
    body = valid(emotions=list(EMOTION_TAGS[:MAX_EMOTIONS]))

    assert body.emotions == list(EMOTION_TAGS[:MAX_EMOTIONS])
    for tag in EMOTION_TAGS:
        assert valid(emotions=[tag]).emotions == [tag]
    for factor in MOOD_FACTORS:
        assert valid(factors=[factor]).factors == [factor]


def test_tags_are_folded_not_rejected_for_a_double_tap() -> None:
    body = valid(emotions=[" Anger ", "anger", "calm"], factors=["Work", "work"])

    assert body.emotions == ["anger", "calm"]
    assert body.factors == ["work"]


def test_the_order_somebody_chose_is_kept() -> None:
    body = valid(emotions=["loneliness", "anger"], factors=["friends", "money"])

    assert body.emotions == ["loneliness", "anger"]
    assert body.factors == ["friends", "money"]


def test_blank_tags_are_dropped() -> None:
    body = valid(emotions=["", "  ", "calm"])

    assert body.emotions == ["calm"]


def test_an_empty_check_in_is_valid() -> None:
    """Two scales are a complete check-in; nothing else is required."""
    body = valid()

    assert body.emotions == []
    assert body.factors == []
    assert body.note is None


def test_a_blank_note_is_no_note() -> None:
    assert valid(note="   \n\t ").note is None
    assert valid(note="  a real thought  ").note == "a real thought"
    assert valid(note=None).note is None


def test_a_note_is_bounded_in_characters() -> None:
    assert valid(note="x" * NOTE_MAX_CHARS).note is not None
    with pytest.raises(ValidationError):
        valid(note="x" * (NOTE_MAX_CHARS + 1))


def test_an_emoji_note_is_measured_in_characters_not_bytes() -> None:
    """A 1000-character note of emoji must not fail because it is 4000 bytes."""
    note = "\U0001f642" * 500

    assert valid(note=note).note == note


def test_the_timezone_field_is_optional_and_bounded() -> None:
    assert valid().tz is None
    assert valid(tz="Asia/Kolkata").tz == "Asia/Kolkata"
    with pytest.raises(ValidationError):
        valid(tz="A" * 65)
