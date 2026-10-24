"""Long-message reading, to the character (Day 26 mutation findings in ``rules.py``).

A risk phrase buried deep in a long journal entry is a red-team case (Day 29): the
engine reads a long message in overlapping windows. These tests pin the numbers
that make that safe, because a one-off error in a window size, an overlap or the
hard cap does not fail any "does it find the phrase" test with the phrase in the
middle of a window. It fails only when the phrase sits on an edge.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.services.safety.base import RiskLevel
from app.services.safety.rules import (
    CODE_TRUNCATED,
    CODE_WINDOWED,
    MAX_WINDOWED_CHARS,
    WINDOW_OVERLAP_CHARS,
    MessageFlags,
    Occurrence,
    RuleEngine,
)

PHRASE = "I want to kill myself"
FILLER = "and then the afternoon went by with ordinary things "


def _filler(length: int) -> str:
    return (FILLER * (length // len(FILLER) + 1))[:length]


def test_the_documented_window_numbers() -> None:
    assert WINDOW_OVERLAP_CHARS == 256
    assert MAX_WINDOWED_CHARS == 64_000


@pytest.mark.parametrize("offset", range(930, 1000, 5))
def test_a_phrase_straddling_the_first_window_edge_is_found(offset: int) -> None:
    engine = RuleEngine(max_chars=1000)
    text = _filler(offset) + " " + PHRASE + " " + _filler(1500)
    assessment = engine.assess(text)
    assert assessment.level >= RiskLevel.HIGH, f"missed at offset {offset}"
    assert CODE_WINDOWED in assessment.rationale_codes


def test_a_phrase_at_the_very_start_of_a_long_message_is_found() -> None:
    engine = RuleEngine(max_chars=1000)
    assert engine.assess(PHRASE + " " + _filler(5000)).level >= RiskLevel.HIGH


def test_a_phrase_at_the_very_end_of_a_long_message_is_found() -> None:
    engine = RuleEngine(max_chars=1000)
    assert engine.assess(_filler(5000) + " " + PHRASE).level >= RiskLevel.HIGH


def test_a_tiny_window_still_terminates_and_finds_the_phrase() -> None:
    # Overlap must shrink with the window, or the cursor would never advance.
    engine = RuleEngine(max_chars=100)
    assessment = engine.assess(_filler(1400) + " " + PHRASE + " " + _filler(100))
    assert assessment.level >= RiskLevel.HIGH


def test_exactly_one_window_is_not_reported_as_windowed() -> None:
    engine = RuleEngine(max_chars=1000)
    exact = engine.assess(_filler(1000))
    over = engine.assess(_filler(1001))
    assert CODE_WINDOWED not in exact.rationale_codes
    assert CODE_WINDOWED in over.rationale_codes


def test_the_hard_cap_is_exact() -> None:
    engine = RuleEngine(max_chars=4000)
    at_cap = engine.assess(_filler(MAX_WINDOWED_CHARS))
    beyond = engine.assess(_filler(MAX_WINDOWED_CHARS + 1))
    assert at_cap.context.truncated is False
    assert CODE_TRUNCATED not in at_cap.rationale_codes
    assert beyond.context.truncated is True
    assert CODE_TRUNCATED in beyond.rationale_codes


def test_text_just_inside_the_cap_is_still_read() -> None:
    engine = RuleEngine(max_chars=4000)
    text = _filler(MAX_WINDOWED_CHARS - len(PHRASE) - 1) + " " + PHRASE
    assert len(text) <= MAX_WINDOWED_CHARS
    assert engine.assess(text).level >= RiskLevel.HIGH


def test_the_input_cap_must_be_a_positive_number() -> None:
    RuleEngine(max_chars=1)
    with pytest.raises(ValueError, match="at least 1"):
        RuleEngine(max_chars=0)


def test_flags_and_occurrences_are_immutable() -> None:
    for cls in (MessageFlags, Occurrence):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is True  # type: ignore[union-attr]


# ---- quotation handling: "only inside quotes" must not leak to other messages ---------- #


def test_plain_first_person_wording_is_never_treated_as_quoted() -> None:
    assessment = RuleEngine().assess("I want to kill myself")
    assert assessment.context.quoted is False
    assert assessment.level >= RiskLevel.HIGH


def test_empty_quotation_marks_do_not_hide_wording_outside_them() -> None:
    assessment = RuleEngine().assess('I want to kill myself ""')
    assert assessment.context.quoted is False
    assert assessment.level >= RiskLevel.HIGH


def test_a_harmless_quotation_does_not_hide_wording_outside_it() -> None:
    assessment = RuleEngine().assess('My teacher said "hello" and I want to kill myself')
    assert assessment.context.quoted is False
    assert assessment.level >= RiskLevel.HIGH


def test_a_message_with_nothing_risky_is_not_reported_as_quoted() -> None:
    assessment = RuleEngine().assess("I had a quiet day and cooked dinner")
    assert assessment.context.quoted is False
    assert assessment.level == RiskLevel.NONE


def test_punctuation_only_quotation_marks_do_not_hide_wording_outside_them() -> None:
    assessment = RuleEngine().assess('I want to kill myself "..."')
    assert assessment.context.quoted is False
    assert assessment.level >= RiskLevel.HIGH


def test_a_one_word_phrase_on_the_first_character_of_a_long_message_is_found() -> None:
    engine = RuleEngine(max_chars=1000)
    short = engine.assess("suicidal")
    assert short.level > RiskLevel.NONE
    long = engine.assess("suicidal " + _filler(5000))
    assert long.level == short.level, "dropping the first character must not change the verdict"


def test_the_language_hint_is_recorded_trimmed_and_lowercased() -> None:
    engine = RuleEngine()
    assert engine.assess("a quiet day", language="  HI ").context.language == "hi"
    assert engine.assess("a quiet day", language="   ").context.language is None
    assert engine.assess("a quiet day", language=None).context.language is None
