from __future__ import annotations

from app.services.plan.policy.actions import POLICY_ACTION_IDS, SUPPORT_ACTION_ID
from app.services.plan.policy.bandits import BanditConfig, ContextualBandit, PolicyAlgorithm
from app.services.plan.policy.features import InterventionContext
from app.services.plan.policy.safety import SafeInterventionService
from app.services.safety.base import RiskLevel


def test_policy_suggest_rating_update_then_safety_override() -> None:
    engine = ContextualBandit(
        PolicyAlgorithm.EPSILON_GREEDY,
        config=BanditConfig(epsilon=0.0),
        seed=37,
    )
    service = SafeInterventionService(engine)
    context = InterventionContext(
        local_hour=20,
        valence=2,
        energy=5,
        emotion_labels=("anxiety",),
        preferred_tags=("breathing",),
    )

    suggestion = service.suggest(
        "integration-user",
        context,
        risk_level=RiskLevel.LOW,
        phq9_item9_response=False,
    )
    assert suggestion is not None
    assert suggestion.action.action_id in POLICY_ACTION_IDS
    assert suggestion.propensity == 1.0
    assert suggestion.why_this.startswith("Why this?")

    reward = service.record_outcome(
        "integration-user",
        context,
        suggestion.action.action_id,
        pre_rating=2,
        post_rating=4,
        started=True,
    )
    assert reward == 0.5
    assert engine.observations_for("integration-user", suggestion.action.action_id) == 1

    support = service.suggest(
        "integration-user",
        context,
        risk_level=RiskLevel.MEDIUM,
        phq9_item9_response=False,
    )
    assert support is not None
    assert support.action.action_id == SUPPORT_ACTION_ID
    assert not support.policy_consulted
    assert engine.observations_for("integration-user", suggestion.action.action_id) == 1
