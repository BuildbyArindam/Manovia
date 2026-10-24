"""Boundary and bookkeeping tests that the Day 26 mutation run showed were missing.

The mutation runner (``scripts/mutation_safety.py``) flips one comparison or flag
at a time in ``ensemble.py`` and re-runs the tests. The property test proves the
ensemble never lowers a level; it cannot notice a threshold that moved by one
step, because every mutated threshold still satisfies "never lower". Each test
below pins one of those edges to an exact value, so a moved boundary is a red
build rather than a silent change to who sees a crisis card.

Probabilities are binary-exact (0.5, 0.25, 0.75) so ``>=`` edges are tested with
no floating-point doubt.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.services.safety.base import RiskAssessment, RiskLevel
from app.services.safety.ensemble import (
    CODE_ML_CRISIS_MASS,
    CODE_ML_RAISED,
    SOURCE_ML,
    SOURCE_RULES,
    SOURCE_UNCERTAIN,
    EnsembleDecision,
    apply_decision,
    combine,
)

from .test_ensemble import _assessment, _prediction

OFF = 2.0  # no probability can reach it: switches a case off


def _combine(
    assessment: RiskAssessment,
    prediction: object,
    *,
    confidence: float = OFF,
    crisis: float = OFF,
    suspicion: float = OFF,
) -> EnsembleDecision:
    return combine(
        assessment,
        prediction,  # type: ignore[arg-type]
        min_confidence=confidence,
        crisis_mass_floor=crisis,
        suspicion_floor=suspicion,
    )


# ---- Case 1: confident top class ------------------------------------------- #


def test_confidence_exactly_at_the_floor_raises() -> None:
    prediction = _prediction(0.0, 0.0, 0.25, 0.75, 0.0)
    decision = _combine(_assessment(RiskLevel.NONE), prediction, confidence=0.75)
    assert decision.level == RiskLevel.HIGH
    assert decision.source == SOURCE_ML
    assert decision.extra_codes == (CODE_ML_RAISED,)


def test_confidence_just_under_the_floor_does_not_raise() -> None:
    prediction = _prediction(0.0, 0.0, 0.25, 0.75, 0.0)
    decision = _combine(_assessment(RiskLevel.NONE), prediction, confidence=0.7500001)
    assert decision.level == RiskLevel.NONE
    assert decision.source == SOURCE_RULES


def test_a_model_that_agrees_with_the_rules_changes_nothing() -> None:
    prediction = _prediction(0.0, 0.0, 0.0, 1.0, 0.0)
    decision = _combine(_assessment(RiskLevel.HIGH), prediction, confidence=0.5)
    assert decision.level == RiskLevel.HIGH
    assert decision.source == SOURCE_RULES, "equal levels are not a raise"
    assert decision.extra_codes == ()


# ---- Case 2: crisis mass ---------------------------------------------------- #


def test_crisis_mass_exactly_at_the_floor_raises_to_high() -> None:
    prediction = _prediction(0.0, 0.0, 0.5, 0.25, 0.25)
    decision = _combine(_assessment(RiskLevel.NONE), prediction, crisis=0.5)
    assert decision.level == RiskLevel.HIGH
    assert decision.extra_codes == (CODE_ML_CRISIS_MASS,)
    assert decision.source == SOURCE_ML


def test_crisis_mass_just_under_the_floor_does_not_raise() -> None:
    prediction = _prediction(0.0, 0.0, 0.5, 0.25, 0.25)
    decision = _combine(_assessment(RiskLevel.NONE), prediction, crisis=0.5000001)
    assert decision.level == RiskLevel.NONE


def test_crisis_mass_never_re_labels_a_rules_high_as_an_ml_raise() -> None:
    prediction = _prediction(0.0, 0.0, 0.5, 0.25, 0.25)
    decision = _combine(_assessment(RiskLevel.HIGH), prediction, crisis=0.5)
    assert decision.level == RiskLevel.HIGH
    assert decision.source == SOURCE_RULES
    assert decision.extra_codes == ()


@pytest.mark.parametrize("field", ["negated", "figurative"])
def test_one_discounted_phrase_is_enough_to_block_a_crisis_mass_raise(field: str) -> None:
    prediction = _prediction(0.0, 0.0, 0.5, 0.25, 0.25)
    assessment = _assessment(RiskLevel.NONE, **{field: 1})
    decision = _combine(assessment, prediction, crisis=0.5)
    assert decision.level == RiskLevel.NONE, "the rules' discount is evidence, not noise"
    assert decision.source == SOURCE_RULES


@pytest.mark.parametrize("field", ["negated", "figurative"])
def test_one_discounted_phrase_is_enough_to_block_a_confident_raise(field: str) -> None:
    prediction = _prediction(0.0, 0.0, 0.0, 1.0, 0.0)
    decision = _combine(_assessment(RiskLevel.NONE, **{field: 1}), prediction, confidence=0.5)
    assert decision.level == RiskLevel.NONE


# ---- Case 3: the gentle check-in ------------------------------------------- #


def test_suspicion_exactly_at_the_floor_asks_gently() -> None:
    prediction = _prediction(0.5, 0.0, 0.25, 0.25, 0.0)
    decision = _combine(_assessment(RiskLevel.NONE), prediction, suspicion=0.25)
    assert decision.level == RiskLevel.MEDIUM
    assert decision.source == SOURCE_UNCERTAIN
    assert decision.uncertain_checkin is True
    assert decision.ml_high_mass == 0.25


def test_suspicion_just_under_the_floor_stays_quiet() -> None:
    prediction = _prediction(0.5, 0.0, 0.25, 0.25, 0.0)
    decision = _combine(_assessment(RiskLevel.NONE), prediction, suspicion=0.2500001)
    assert decision.level == RiskLevel.NONE
    assert decision.uncertain_checkin is False


def test_the_checkin_does_not_re_fire_when_rules_already_say_medium() -> None:
    prediction = _prediction(0.5, 0.0, 0.25, 0.25, 0.0)
    decision = _combine(_assessment(RiskLevel.MEDIUM), prediction, suspicion=0.25)
    assert decision.level == RiskLevel.MEDIUM
    assert decision.source == SOURCE_RULES
    assert decision.uncertain_checkin is False


# ---- Bookkeeping: the audit fields -------------------------------------------- #


def test_every_path_reports_whether_a_model_took_part_and_what_it_said() -> None:
    raised = _combine(
        _assessment(RiskLevel.NONE), _prediction(0.0, 0.0, 0.0, 1.0, 0.0), confidence=0.5
    )
    mass = _combine(_assessment(RiskLevel.NONE), _prediction(0.0, 0.0, 0.5, 0.25, 0.25), crisis=0.5)
    uncertain = _combine(
        _assessment(RiskLevel.NONE), _prediction(0.5, 0.0, 0.25, 0.25, 0.0), suspicion=0.25
    )
    unchanged = _combine(_assessment(RiskLevel.NONE), _prediction(1.0, 0.0, 0.0, 0.0, 0.0))
    for decision in (raised, mass, uncertain, unchanged):
        assert decision.ml_used is True
        assert decision.ml_level is not None
        assert decision.ml_confidence is not None
        assert decision.ml_high_mass is not None
    assert [d.uncertain_checkin for d in (raised, mass, uncertain, unchanged)] == [
        False,
        False,
        True,
        False,
    ]
    assert raised.ml_high_mass == 1.0
    assert mass.ml_high_mass == 0.5
    assert unchanged.ml_high_mass == 0.0


def test_without_a_prediction_nothing_claims_a_model_took_part() -> None:
    decision = _combine(_assessment(RiskLevel.LOW), None)
    assert decision.ml_used is False
    assert decision.ml_level is None
    assert decision.ml_confidence is None
    assert decision.ml_high_mass is None
    assert decision.uncertain_checkin is False


def test_only_a_model_raise_is_attributed_to_the_model() -> None:
    raised = _combine(
        _assessment(RiskLevel.NONE), _prediction(0.0, 0.0, 0.0, 1.0, 0.0), confidence=0.5
    )
    uncertain = _combine(
        _assessment(RiskLevel.NONE), _prediction(0.5, 0.0, 0.25, 0.25, 0.0), suspicion=0.25
    )
    rules = _combine(_assessment(RiskLevel.LOW), None)
    assert raised.raised_by_ml is True
    assert uncertain.raised_by_ml is False
    assert rules.raised_by_ml is False


def test_a_decision_cannot_be_edited_after_the_fact() -> None:
    decision = _combine(_assessment(RiskLevel.LOW), None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.level = RiskLevel.NONE  # type: ignore[misc]


# ---- apply_decision --------------------------------------------------------------- #


def _decision(level: RiskLevel, codes: tuple[str, ...]) -> EnsembleDecision:
    return EnsembleDecision(
        level=level,
        source=SOURCE_ML,
        ml_used=True,
        ml_level=level,
        ml_confidence=1.0,
        ml_high_mass=0.0,
        uncertain_checkin=False,
        extra_codes=codes,
    )


def test_same_level_but_new_codes_still_produces_an_updated_assessment() -> None:
    original = _assessment(RiskLevel.MEDIUM)
    updated = apply_decision(original, _decision(RiskLevel.MEDIUM, ("ml.note",)))
    assert updated is not original
    assert "ml.note" in updated.rationale_codes


def test_a_higher_level_without_codes_still_produces_an_updated_assessment() -> None:
    original = _assessment(RiskLevel.LOW)
    updated = apply_decision(original, _decision(RiskLevel.HIGH, ()))
    assert updated.level == RiskLevel.HIGH
    assert updated is not original
