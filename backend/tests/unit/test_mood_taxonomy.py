"""The mood vocabulary: closed, shared with the analyser, and enforced by tests.

Two of these tests are drift guards rather than behaviour tests, and they are the
reason this file exists: a check-in's "sadness" and a chat message analysed as
"sadness" must stay the same label, and the frontend's chips must stay in the set
the server accepts. A silent divergence there would show up as a 422 in
production, in the middle of somebody's worst week.
"""

from __future__ import annotations

from app.services.mood import taxonomy
from app.services.nlp.base import EMOTION_DIMENSIONS, EMOTIONS


def test_the_emotion_tags_are_the_analysers_taxonomy() -> None:
    assert taxonomy.EMOTION_TAGS == EMOTIONS
    assert tuple(taxonomy.EMOTION_TAGS) == tuple(EMOTIONS)
    assert len(taxonomy.EMOTION_TAGS) == 9


def test_every_emotion_has_a_dimension_anchor() -> None:
    """A label without a (valence, arousal) anchor is a label nobody can place."""
    assert set(taxonomy.EMOTION_TAGS) <= set(EMOTION_DIMENSIONS)


def test_a_neutral_option_exists() -> None:
    assert taxonomy.NEUTRAL_TAG in taxonomy.EMOTION_TAGS
    assert taxonomy.NEUTRAL_TAG == "neutral"


def test_the_factor_vocabulary_is_the_agreed_one() -> None:
    expected = ("sleep", "study", "work", "family", "friends", "health", "money", "weather")

    assert expected == taxonomy.MOOD_FACTORS
    assert len(set(taxonomy.MOOD_FACTORS)) == len(taxonomy.MOOD_FACTORS)


def test_tags_are_wire_safe() -> None:
    """Lowercase ASCII means no locale-sensitive comparison and no encoding trap."""
    for tag in (*taxonomy.EMOTION_TAGS, *taxonomy.MOOD_FACTORS):
        assert tag == tag.casefold()
        assert tag.isascii()
        assert " " not in tag


def test_the_caps_are_bounded_and_small() -> None:
    assert taxonomy.MAX_EMOTIONS == 3
    assert taxonomy.MAX_FACTORS == 5
    assert taxonomy.SCALE_MIN == 1
    assert taxonomy.SCALE_MAX == 5
    assert 0 < taxonomy.NOTE_MAX_CHARS <= 2000
    assert len(taxonomy.EMOTION_TAGS) > taxonomy.MAX_EMOTIONS
    assert len(taxonomy.MOOD_FACTORS) > taxonomy.MAX_FACTORS


def test_unknown_values_are_reported_not_dropped() -> None:
    assert taxonomy.unknown_emotions(["joy", "confusion"]) == ["confusion"]
    assert taxonomy.unknown_emotions(["joy", "sadness"]) == []
    assert taxonomy.unknown_factors(["sleep", "moon"]) == ["moon"]
    assert taxonomy.unknown_factors([]) == []


def test_the_vocabulary_has_no_personality_verdicts() -> None:
    """Vocabulary review as a test: no factor may read as a judgement of character.

    The list is short on purpose, and this is the check that keeps it that way —
    a future "laziness" or "discipline" chip would fail here and have to be argued
    about in a review instead of slipping in with a UI change.
    """
    forbidden = {
        "lazy",
        "laziness",
        "discipline",
        "willpower",
        "failure",
        "fault",
        "excuses",
        "overweight",
        "diet",
    }

    assert not (set(taxonomy.MOOD_FACTORS) & forbidden)
    assert not (set(taxonomy.EMOTION_TAGS) & forbidden)
