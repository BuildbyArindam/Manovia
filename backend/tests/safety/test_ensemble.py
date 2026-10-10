"""The ensemble's contract, with a property test at the centre of it.

The one invariant Day 9 must never break: **the ML classifier can RAISE the
level the rules engine found, but it can never LOWER it.** ``combine`` is the
only place the two detectors meet, so the property is tested there, over
arbitrary rules assessments and arbitrary calibrated probability vectors.

The rest pins the documented branches: confident raises, the crisis-mass
raise, the uncertain -> MEDIUM gentle check-in, the pragmatics gate, and the
rules-only degradation when there is no prediction at all.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.safety.base import AssessmentContext, RiskAssessment, RiskCategory, RiskLevel
from app.services.safety.ensemble import (
    CODE_ML_CRISIS_MASS,
    CODE_ML_RAISED,
    CODE_ML_UNCERTAIN_CHECKIN,
    SOURCE_ML,
    SOURCE_RULES,
    SOURCE_UNCERTAIN,
    UNCERTAIN_LEVEL,
    apply_decision,
    combine,
)
from app.services.safety.ml_classifier import LEVEL_LABELS, MLPrediction

#: The shipped operating point, exercised explicitly. The property below must
#: hold for ANY thresholds, so it parametrises over them instead.
SHIPPED_MIN_CONFIDENCE = 0.70
SHIPPED_CRISIS_MASS_FLOOR = 0.30
SHIPPED_SUSPICION_FLOOR = 0.25


def _assessment(level: RiskLevel, *, negated: int = 0, figurative: int = 0) -> RiskAssessment:
    return RiskAssessment(
        level=level,
        matched_categories=(RiskCategory.SUICIDAL_IDEATION,) if level >= RiskLevel.HIGH else (),
        context=AssessmentContext(negated_hits=negated, figurative_hits=figurative),
    )


def _prediction(
    none: float, low: float, medium: float, high: float, imminent: float
) -> MLPrediction:
    """Build an MLPrediction the way the classifier would (argmax upward)."""
    probabilities = {"none": none, "low": low, "medium": medium, "high": high, "imminent": imminent}
    best = max(probabilities.values())
    level = RiskLevel.NONE
    for candidate in reversed(tuple(RiskLevel)):
        if probabilities[candidate.label] >= best - 1e-12:
            level = candidate
            break
    return MLPrediction(
        probabilities=probabilities,
        level=level,
        confidence=probabilities[level.label],
        version="test",
    )


# --------------------------------------------------------------------------- #
# The property: ML can raise, never lower                                     #
# --------------------------------------------------------------------------- #

#: A probability vector over the five labels: five positive floats, normalised.
_probability_vector = st.tuples(
    st.floats(min_value=1e-6, max_value=1.0),
    st.floats(min_value=1e-6, max_value=1.0),
    st.floats(min_value=1e-6, max_value=1.0),
    st.floats(min_value=1e-6, max_value=1.0),
    st.floats(min_value=1e-6, max_value=1.0),
).map(lambda raw: {label: value / sum(raw) for label, value in zip(LEVEL_LABELS, raw, strict=True)})

_thresholds = st.fixed_dictionaries(
    {
        "min_confidence": st.floats(min_value=0.01, max_value=1.0),
        "crisis_mass_floor": st.floats(min_value=0.01, max_value=1.0),
        "suspicion_floor": st.floats(min_value=0.01, max_value=1.0),
    }
)


@given(
    rules_level=st.sampled_from(list(RiskLevel)),
    probabilities=_probability_vector,
    thresholds=_thresholds,
)
@settings(max_examples=800, deadline=None)
def test_the_ensemble_never_lowers_a_rules_result(
    rules_level: RiskLevel, probabilities: dict[str, float], thresholds: dict[str, float]
) -> None:
    """For ANY prediction and ANY thresholds, final >= rules level.

    This is the invariant the Day 9 design is built on: a probability is not
    something to put between a person in crisis and a helpline, so no model
    output may talk the system out of a level the rules engine already found.
    """
    if thresholds["crisis_mass_floor"] < thresholds["suspicion_floor"]:
        # Settings validation forbids that ordering; the property is only
        # promised for valid configurations.
        return
    assessment = _assessment(rules_level)
    decision = combine(
        assessment,
        _prediction(**probabilities),
        min_confidence=thresholds["min_confidence"],
        crisis_mass_floor=thresholds["crisis_mass_floor"],
        suspicion_floor=thresholds["suspicion_floor"],
    )
    assert decision.level >= rules_level, (
        f"ensemble lowered {rules_level.name} to {decision.level.name} "
        f"with probabilities {probabilities} at thresholds {thresholds}"
    )


@given(rules_level=st.sampled_from(list(RiskLevel)))
@settings(max_examples=50, deadline=None)
def test_no_prediction_is_exactly_the_rules_result(rules_level: RiskLevel) -> None:
    """A disabled/degraded classifier must not change anything."""
    assessment = _assessment(rules_level)
    decision = combine(
        assessment,
        None,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=SHIPPED_CRISIS_MASS_FLOOR,
        suspicion_floor=SHIPPED_SUSPICION_FLOOR,
    )
    assert decision.level == rules_level
    assert decision.source == SOURCE_RULES
    assert decision.ml_used is False
    assert decision.extra_codes == ()


# --------------------------------------------------------------------------- #
# The branches                                                                #
# --------------------------------------------------------------------------- #


def test_a_confident_model_raises_a_low_rules_level() -> None:
    assessment = _assessment(RiskLevel.LOW)
    prediction = _prediction(0.05, 0.05, 0.05, 0.15, 0.70)
    decision = combine(
        assessment,
        prediction,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=0.99,  # keep the mass path out of it
        suspicion_floor=0.99,
    )
    assert decision.level == RiskLevel.IMMINENT
    assert decision.source == SOURCE_ML
    assert decision.extra_codes == (CODE_ML_RAISED,)


def test_a_confident_model_cannot_lower() -> None:
    """The model says NONE with confidence; the rules said HIGH. Rules win."""
    assessment = _assessment(RiskLevel.HIGH)
    prediction = _prediction(0.96, 0.01, 0.01, 0.01, 0.01)
    decision = combine(
        assessment,
        prediction,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=0.99,
        suspicion_floor=0.99,
    )
    assert decision.level == RiskLevel.HIGH
    assert decision.source == SOURCE_RULES


def test_crisis_mass_raises_when_the_model_is_torn_between_the_crisis_levels() -> None:
    """P(high)+P(imminent) high, but no single confident class -> HIGH."""
    assessment = _assessment(RiskLevel.NONE)
    prediction = _prediction(0.10, 0.05, 0.15, 0.35, 0.35)
    decision = combine(
        assessment,
        prediction,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=0.60,
        suspicion_floor=0.20,
    )
    assert decision.level == RiskLevel.HIGH
    assert decision.source == SOURCE_ML
    assert decision.extra_codes == (CODE_ML_CRISIS_MASS,)
    assert decision.ml_high_mass == pytest.approx(0.70)


def test_uncertain_model_falls_back_to_the_medium_checkin() -> None:
    """Not confident, crisis mass modest but real -> MEDIUM, gentle check-in."""
    assessment = _assessment(RiskLevel.NONE)
    prediction = _prediction(0.20, 0.20, 0.20, 0.20, 0.20)
    decision = combine(
        assessment,
        prediction,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=0.90,
        suspicion_floor=0.30,
    )
    assert decision.level == UNCERTAIN_LEVEL
    assert decision.source == SOURCE_UNCERTAIN
    assert decision.uncertain_checkin is True
    assert decision.extra_codes == (CODE_ML_UNCERTAIN_CHECKIN,)


def test_the_checkin_never_fires_above_medium() -> None:
    """Uncertainty resolves to MEDIUM at most — never to a crisis card."""
    assessment = _assessment(RiskLevel.HIGH)
    prediction = _prediction(0.20, 0.20, 0.20, 0.20, 0.20)
    decision = combine(
        assessment,
        prediction,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=0.90,
        suspicion_floor=0.10,
    )
    assert decision.level == RiskLevel.HIGH
    assert decision.uncertain_checkin is False


def test_negated_words_block_an_ml_raise() -> None:
    """The rules discounted the words (negation); ML may not undo that."""
    assessment = _assessment(RiskLevel.LOW, negated=1)
    prediction = _prediction(0.02, 0.03, 0.05, 0.10, 0.80)
    decision = combine(
        assessment,
        prediction,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=0.30,
        suspicion_floor=0.10,
    )
    # The confident raise and the mass raise are both blocked; what remains is
    # the suspicion band: at most the gentle MEDIUM check-in.
    assert decision.level <= UNCERTAIN_LEVEL
    assert decision.source != SOURCE_ML


def test_figurative_words_block_an_ml_raise() -> None:
    assessment = _assessment(RiskLevel.NONE, figurative=2)
    prediction = _prediction(0.02, 0.03, 0.05, 0.10, 0.80)
    decision = combine(
        assessment,
        prediction,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=0.30,
        suspicion_floor=0.95,  # keep the check-in band out of it
    )
    assert decision.level == RiskLevel.NONE
    assert decision.source == SOURCE_RULES


# --------------------------------------------------------------------------- #
# apply_decision                                                              #
# --------------------------------------------------------------------------- #


def test_apply_decision_keeps_the_original_when_nothing_changed() -> None:
    assessment = _assessment(RiskLevel.MEDIUM)
    decision = combine(
        assessment,
        None,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=SHIPPED_CRISIS_MASS_FLOOR,
        suspicion_floor=SHIPPED_SUSPICION_FLOOR,
    )
    assert apply_decision(assessment, decision) is assessment


def test_apply_decision_carries_the_codes_and_keeps_the_rules_metadata() -> None:
    assessment = RiskAssessment(
        level=RiskLevel.NONE,
        matched_categories=(RiskCategory.SEVERE_HOPELESSNESS,),
        rationale_codes=("si.after_im_gone",),
        context=AssessmentContext(first_person=True),
    )
    prediction = _prediction(0.10, 0.05, 0.05, 0.40, 0.40)
    decision = combine(
        assessment,
        prediction,
        min_confidence=SHIPPED_MIN_CONFIDENCE,
        crisis_mass_floor=0.60,
        suspicion_floor=0.20,
    )
    rebuilt = apply_decision(assessment, decision)
    assert rebuilt.level == RiskLevel.HIGH
    assert rebuilt.rationale_codes == ("si.after_im_gone", CODE_ML_CRISIS_MASS)
    # Categories and context remain the rules engine's findings.
    assert rebuilt.matched_categories == assessment.matched_categories
    assert rebuilt.context is assessment.context


def test_masses_sum_to_one() -> None:
    prediction = _prediction(0.2, 0.2, 0.2, 0.2, 0.2)
    assert math.isclose(prediction.mass_at_or_above(RiskLevel.NONE), 1.0)
    assert math.isclose(prediction.mass_at_or_above(RiskLevel.HIGH), 0.4)
    assert math.isclose(prediction.mass_at_or_above(RiskLevel.IMMINENT), 0.2)


def test_prediction_ties_break_upward() -> None:
    prediction = _prediction(0.2, 0.2, 0.2, 0.2, 0.2)
    assert prediction.level == RiskLevel.IMMINENT
    assert prediction.confidence == pytest.approx(0.2)
