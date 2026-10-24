"""Tests for edges the Day 26 mutation run found unguarded in escalation and session risk."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from app.content.crisis import load_helplines
from app.services.safety.base import RiskAssessment, RiskLevel
from app.services.safety.escalation import (
    INTERNATIONAL_EMERGENCY_NUMBER,
    EscalationPlan,
    EscalationPolicy,
    Escalator,
    build_escalator,
)
from app.services.safety.session_risk import (
    WINDOW_USER_TURNS,
    accumulate,
    session_score,
)


@dataclass(frozen=True)
class Turn:
    role: str
    risk: int


def _users(risk: int, count: int) -> list[Turn]:
    return [Turn("user", risk) for _ in range(count)]


# ---- session_score: exact points and the window edge ------------------------------ #


@pytest.mark.parametrize(
    ("level", "expected"),
    [(RiskLevel.NONE, 0), (RiskLevel.LOW, 1), (RiskLevel.MEDIUM, 2)],
)
def test_the_current_message_scores_its_own_points(level: RiskLevel, expected: int) -> None:
    assert session_score(level, []) == (expected, False)


@pytest.mark.parametrize(
    ("stored", "points", "check_in"), [(0, 0, False), (1, 1, False), (2, 2, True)]
)
def test_a_stored_turn_scores_its_tier(stored: int, points: int, check_in: bool) -> None:
    assert session_score(RiskLevel.NONE, _users(stored, 1)) == (points, check_in)


def test_the_window_is_exactly_the_last_eight_user_turns_including_this_one() -> None:
    assert WINDOW_USER_TURNS == 8
    score, _ = session_score(RiskLevel.LOW, _users(1, 20))
    assert score == 1 + (WINDOW_USER_TURNS - 1), "current + seven earlier, nothing older"


def test_a_stored_crisis_turn_closes_the_window() -> None:
    history = [Turn("user", 1), Turn("user", 3), Turn("user", 1)]
    assert session_score(RiskLevel.LOW, history) == (2, False)


# ---- accumulate: a raise to the level it already has is not a raise ----------------- #


def test_accumulation_that_only_reaches_the_current_level_changes_nothing() -> None:
    before = RiskAssessment(level=RiskLevel.MEDIUM, rationale_codes=("x.rule",))
    # 2 (this MEDIUM) + 1 + 1 = 4: enough for the check-in tier, which is MEDIUM already.
    assert accumulate(before, _users(1, 2)) is before


# ---- escalation: immutability, defaults, and a catalogue with no emergency entry ----- #


def test_plans_and_policies_cannot_be_edited_after_construction() -> None:
    policy = Escalator().policy_for(RiskLevel.HIGH)
    with pytest.raises(ValidationError):
        policy.level = RiskLevel.NONE  # type: ignore[misc]
    plan = Escalator().plan(RiskAssessment(level=RiskLevel.HIGH), region="IN", locale="en")
    with pytest.raises(ValidationError):
        plan.region = "US"  # type: ignore[misc]
    assert isinstance(policy, EscalationPolicy)


def test_a_bare_plan_claims_no_fallback_and_no_supporter_framing() -> None:
    plan = EscalationPlan(
        assessment=RiskAssessment(level=RiskLevel.NONE),
        policy=Escalator().policy_for(RiskLevel.NONE),
    )
    assert plan.locale == "en"
    assert plan.region == "DEFAULT"
    assert plan.locale_fallback_used is False
    assert plan.region_fallback_used is False
    assert plan.about_someone_else is False


def test_a_real_plan_reports_its_fallbacks_and_framing_honestly() -> None:
    escalator = Escalator()
    ordinary = escalator.plan(RiskAssessment(level=RiskLevel.HIGH), region="IN", locale="en")
    assert (ordinary.locale_fallback_used, ordinary.region_fallback_used) == (False, False)
    assert ordinary.about_someone_else is False
    unknown = escalator.plan(RiskAssessment(level=RiskLevel.HIGH), region="ZZ", locale="xx")
    assert (unknown.locale_fallback_used, unknown.region_fallback_used) == (True, True)


def test_a_catalogue_without_an_emergency_entry_still_produces_a_plan() -> None:
    shipped = load_helplines()
    without = shipped.model_copy(
        update={"resources": [r for r in shipped.resources if r.kind != "emergency"]}
    )
    plan = Escalator(without).plan(RiskAssessment(level=RiskLevel.HIGH), region="IN", locale="en")
    assert plan.emergency is None
    assert plan.emergency_number is None
    assert plan.message is not None, "the message is still rendered, with the international number"
    instruction = plan.message.emergency_instruction
    assert instruction is not None
    assert INTERNATIONAL_EMERGENCY_NUMBER in instruction


# ---- build_escalator: shared by default, fresh when content is injected ------------------ #


def test_the_default_escalator_is_shared_and_injected_content_gets_its_own() -> None:
    first = build_escalator()
    assert build_escalator() is first
    injected = build_escalator(load_helplines())
    assert injected is not first
    assert build_escalator() is first, "injecting content must not replace the shared one"
    from app.content.i18n import load_all_localisations

    assert build_escalator(localisations=load_all_localisations()) is not first
