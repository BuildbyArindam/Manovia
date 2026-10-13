"""Unit tests for the Day 29 session-level risk accumulator (RT-S-MT-01, ADR 0030)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.safety.base import AssessmentContext, RiskAssessment, RiskLevel
from app.services.safety.session_risk import (
    CODE_ACCUMULATED_HIGH,
    CODE_ACCUMULATED_MEDIUM,
    WINDOW_USER_TURNS,
    accumulate,
    session_score,
)


@dataclass(frozen=True)
class Turn:
    role: str
    risk: int


def user(*risks: int) -> list[Turn]:
    """User turns with the given stored tiers, each followed by an assistant turn."""
    turns: list[Turn] = []
    for risk in risks:
        turns += [Turn("user", risk), Turn("assistant", risk)]
    return turns


def assessment(level: RiskLevel, **context: bool) -> RiskAssessment:
    return RiskAssessment(
        level=level, rationale_codes=("x.rule",), context=AssessmentContext(**context)
    )


def test_three_low_turns_raise_to_a_check_in() -> None:
    raised = accumulate(assessment(RiskLevel.LOW), user(1, 1))
    assert raised.level is RiskLevel.MEDIUM
    assert CODE_ACCUMULATED_MEDIUM in raised.rationale_codes
    assert "x.rule" in raised.rationale_codes


def test_a_check_in_then_two_more_signals_raise_to_high() -> None:
    raised = accumulate(assessment(RiskLevel.MEDIUM), user(1, 1, 2))
    assert raised.level is RiskLevel.HIGH
    assert CODE_ACCUMULATED_HIGH in raised.rationale_codes


def test_a_neutral_message_is_never_raised() -> None:
    """Changing the subject after a hard run must not earn a crisis card."""
    before = assessment(RiskLevel.NONE)
    assert accumulate(before, user(2, 2, 2, 2)) is before


def test_high_is_left_alone() -> None:
    before = assessment(RiskLevel.HIGH)
    assert accumulate(before, user(2, 2)) is before


def test_the_window_restarts_after_a_crisis_turn() -> None:
    """Once the crisis card was shown, what came before it is not counted again."""
    raised = accumulate(assessment(RiskLevel.MEDIUM), user(2, 2, 3, 1))
    # Only the turn after the crisis counts: 1 + 2 = 3 -> MEDIUM, not HIGH.
    assert raised.level is RiskLevel.MEDIUM


def test_only_the_last_window_of_user_turns_counts() -> None:
    old = [1] * 20
    score, _ = session_score(RiskLevel.LOW, user(*old))
    assert score == WINDOW_USER_TURNS  # current + (window - 1) prior LOW turns


def test_no_second_check_in_from_accumulation_alone() -> None:
    """Anti-nagging: a check-in already in the window blocks another MEDIUM raise."""
    before = assessment(RiskLevel.LOW)
    assert accumulate(before, user(1, 2)).level is RiskLevel.LOW


@pytest.mark.parametrize("flag", ["third_person", "fiction_frame"])
def test_messages_about_someone_else_are_not_raised(flag: str) -> None:
    before = assessment(RiskLevel.MEDIUM, **{flag: True})
    assert accumulate(before, user(2, 2, 2)) is before


def test_assistant_turns_do_not_score() -> None:
    history = [Turn("assistant", 2)] * 6
    assert accumulate(assessment(RiskLevel.LOW), history).level is RiskLevel.LOW
