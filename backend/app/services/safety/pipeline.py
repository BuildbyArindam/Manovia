"""One shared entry point for "assess this message": rules, then the ML raise cascade.

``POST /crisis/assess`` (Day 8/9) and the chat orchestrator (Day 11) must reach
the *same* verdict for the same text, so they call the same function. It used
to live as a private helper in the crisis API module; the chat service cannot
import from an API module without inverting the layers, so it moved here
unchanged.

The matchers are CPU-bound and synchronous, so the work runs in a worker thread
(one hop for rules and classifier together) instead of blocking the event loop.
Any classifier error degrades to rules-only: the ML model is a backstop, and a
backstop must never be the reason a message goes unassessed. A failure in the
*rules engine* is not swallowed — that propagates, and every caller must treat it
as "no safety verdict" and fail closed.
"""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from app.services.safety.base import RiskAssessment
from app.services.safety.ensemble import EnsembleDecision, apply_decision, combine
from app.services.safety.ml_classifier import MLPrediction, SafetyClassifier
from app.services.safety.rules import RuleEngine


async def run_ensemble(
    text: str,
    language: str | None,
    engine: RuleEngine,
    classifier: SafetyClassifier,
    *,
    min_confidence: float,
    crisis_mass_floor: float,
    suspicion_floor: float,
) -> tuple[RiskAssessment, EnsembleDecision]:
    """Rules first, then the ML raise cascade — both in one thread-pool hop."""

    def _score() -> tuple[RiskAssessment, EnsembleDecision]:
        assessment = engine.assess(text, language=language)
        prediction: MLPrediction | None = None
        if classifier.enabled:
            try:
                prediction = classifier.predict(text)
            except Exception:  # degrade to rules-only
                prediction = None
        decision = combine(
            assessment,
            prediction,
            min_confidence=min_confidence,
            crisis_mass_floor=crisis_mass_floor,
            suspicion_floor=suspicion_floor,
        )
        return apply_decision(assessment, decision), decision

    return await run_in_threadpool(_score)


__all__ = ["run_ensemble"]
