"""Combine the rules engine and the ML classifier into one decision.

The single invariant this module exists to enforce (Day 9, ADR 0009):

    **final level = max(rules level, ML level if confident).**

The ML classifier can RAISE an assessment; it can never LOWER one. If the
rules engine found HIGH, the model cannot talk the system out of the crisis
card — a probability is not something to put between a person in crisis and a
helpline. The model's job is to catch what the rules missed: the indirect
phrasings, the messages with none of the obvious words.

Three outcomes, all visible in the returned :class:`EnsembleDecision`:

* ``source == "rules"`` — the rules level stood. Either the model agreed, was
  less confident than ``min_confidence``, or wanted to lower (which it may
  not).
* ``source == "ml"`` — the model was confident enough and asked for a higher
  level. The assessment is raised to it.
* ``source == "ensemble.uncertain"`` — the model was *not* confident, but its
  HIGH+IMMINENT probability mass reached ``suspicion_floor`` while the rules
  stayed below MEDIUM. The policy then treats the message as MEDIUM: respond
  normally, append the soft check-in and resources — never a crisis card, and
  never silence. Uncertainty resolves toward noticing, not toward dismissing.

Privacy note, same as the rest of the package: this module takes assessments
and numbers in and gives metadata out. It never sees the message text, and it
has no logging calls to get wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.services.safety.base import RiskAssessment, RiskLevel
from app.services.safety.ml_classifier import MLPrediction, SafetyClassifier

#: Rationale codes the ensemble adds. Both pass the pattern-id shape check in
#: ``base.RiskAssessment`` (dotted, lowercase, no prose) and name the *policy*
#: that fired, never the words that triggered it.
CODE_ML_RAISED: Final = "ml.raised"
CODE_ML_UNCERTAIN_CHECKIN: Final = "ml.uncertain.checkin"

#: Source vocabulary for the decision. Maps onto ``SafetyEventSource`` at the
#: API layer: ``rules`` -> RULES, both ML outcomes -> ML.
SOURCE_RULES: Final = "rules"
SOURCE_ML: Final = "ml"
SOURCE_UNCERTAIN: Final = "ensemble.uncertain"

#: What the uncertain-check-in outcome means as a level: respond normally,
#: append the soft check-in and resources (the MEDIUM policy does exactly
#: this), and write a metadata-only audit row.
UNCERTAIN_LEVEL: Final = RiskLevel.MEDIUM


@dataclass(frozen=True)
class EnsembleDecision:
    """The combined verdict and everything needed to explain and audit it.

    All fields are metadata: levels, probabilities, and stable codes. Nothing
    here is derived text, so the whole object is safe to surface in a response
    and to store on a ``safety_events`` row.
    """

    #: The level the product must respond with. Never below the rules level.
    level: RiskLevel
    #: Who set it: ``rules``, ``ml`` or ``ensemble.uncertain``.
    source: str
    #: True when a real model prediction participated.
    ml_used: bool
    #: The model's argmax level, if one was available.
    ml_level: RiskLevel | None
    #: The model's top-class calibrated probability, if available.
    ml_confidence: float | None
    #: P(HIGH) + P(IMMINENT) from the model — the "uncertain" policy input.
    ml_high_mass: float | None
    #: True exactly when the uncertain -> MEDIUM check-in policy fired.
    uncertain_checkin: bool
    #: Extra rationale codes to append to the assessment.
    extra_codes: tuple[str, ...] = ()

    @property
    def raised_by_ml(self) -> bool:
        return self.source == SOURCE_ML


def combine(
    assessment: RiskAssessment,
    prediction: MLPrediction | None,
    *,
    min_confidence: float,
    suspicion_floor: float,
) -> EnsembleDecision:
    """Apply the ensemble rule to one rules assessment and one ML prediction.

    ``prediction`` is ``None`` when the classifier is disabled or has no
    artifact; the decision is then the rules level unchanged.

    The invariant ``decision.level >= assessment.level`` holds by construction
    — every branch either keeps the rules level or replaces it with a higher
    one — and a hypothesis property test pins that from the outside.
    """
    rules_level = assessment.level

    if prediction is None:
        return EnsembleDecision(
            level=rules_level,
            source=SOURCE_RULES,
            ml_used=False,
            ml_level=None,
            ml_confidence=None,
            ml_high_mass=None,
            uncertain_checkin=False,
        )

    ml_level = prediction.level
    confidence = prediction.confidence
    high_mass = prediction.mass_at_or_above(RiskLevel.HIGH)

    # Case 1: the model is confident. It may raise, never lower.
    if confidence >= min_confidence:
        if ml_level > rules_level:
            return EnsembleDecision(
                level=ml_level,
                source=SOURCE_ML,
                ml_used=True,
                ml_level=ml_level,
                ml_confidence=confidence,
                ml_high_mass=high_mass,
                uncertain_checkin=False,
                extra_codes=(CODE_ML_RAISED,),
            )
        return EnsembleDecision(
            level=rules_level,
            source=SOURCE_RULES,
            ml_used=True,
            ml_level=ml_level,
            ml_confidence=confidence,
            ml_high_mass=high_mass,
            uncertain_checkin=False,
        )

    # Case 2: the model is not confident. If it still puts meaningful mass on
    # HIGH+IMMINENT while the rules saw less than MEDIUM, play safe: treat as
    # MEDIUM, gentle check-in. Never above MEDIUM on unconfident evidence — a
    # crisis card on a hunch is its own harm.
    if high_mass >= suspicion_floor and rules_level < UNCERTAIN_LEVEL:
        return EnsembleDecision(
            level=UNCERTAIN_LEVEL,
            source=SOURCE_UNCERTAIN,
            ml_used=True,
            ml_level=ml_level,
            ml_confidence=confidence,
            ml_high_mass=high_mass,
            uncertain_checkin=True,
            extra_codes=(CODE_ML_UNCERTAIN_CHECKIN,),
        )

    # Case 3: nothing to change.
    return EnsembleDecision(
        level=rules_level,
        source=SOURCE_RULES,
        ml_used=True,
        ml_level=ml_level,
        ml_confidence=confidence,
        ml_high_mass=high_mass,
        uncertain_checkin=False,
    )


def apply_decision(assessment: RiskAssessment, decision: EnsembleDecision) -> RiskAssessment:
    """Return the assessment the escalator should plan against.

    If the ensemble changed nothing, the original object is returned as-is;
    otherwise a new assessment carries the final level and the ensemble's
    rationale codes alongside the rules engine's. Categories and context are
    the rules engine's findings in both cases — the model adds no categories
    of its own, so a reviewer still audits one vocabulary.
    """
    if decision.level == assessment.level and not decision.extra_codes:
        return assessment
    codes = tuple(assessment.rationale_codes) + decision.extra_codes
    return RiskAssessment(
        level=decision.level,
        matched_categories=assessment.matched_categories,
        rationale_codes=codes,
        context=assessment.context,
    )


def decide(
    assessment: RiskAssessment,
    classifier: SafetyClassifier,
    *,
    text: str,
    min_confidence: float,
    suspicion_floor: float,
) -> EnsembleDecision:
    """One-call helper: score with the classifier (if enabled), then combine.

    Kept in this module rather than the API layer so the property tests and the
    eval harness exercise the exact same path the request path does. A
    classifier failure degrades to rules-only: the ensemble must never be the
    reason a message goes unanswered.
    """
    prediction: MLPrediction | None = None
    if classifier.enabled:
        try:
            prediction = classifier.predict(text)
        except Exception:  # noqa: BLE001 - degrade to rules-only on any model error
            prediction = None
    return combine(
        assessment,
        prediction,
        min_confidence=min_confidence,
        suspicion_floor=suspicion_floor,
    )


__all__ = [
    "CODE_ML_RAISED",
    "CODE_ML_UNCERTAIN_CHECKIN",
    "SOURCE_ML",
    "SOURCE_RULES",
    "SOURCE_UNCERTAIN",
    "UNCERTAIN_LEVEL",
    "EnsembleDecision",
    "apply_decision",
    "combine",
    "decide",
]
