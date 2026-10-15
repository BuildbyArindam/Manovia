from __future__ import annotations

from app.services.plan.policy.actions import POLICY_ACTION_IDS
from app.services.plan.policy.bandits import BanditConfig, ContextualBandit, PolicyAlgorithm
from app.services.plan.policy.features import InterventionContext
from app.services.plan.policy.safety import SafeInterventionService
from app.services.safety.base import RiskLevel

CONTEXT = InterventionContext(
    local_hour=20,
    valence=2,
    energy=5,
    emotion_labels=("anxiety",),
    recent_exercise_ids=("grounding-54321",),
    preferred_tags=("breathing", "grounding"),
)


def _decision_sequence(seed: int) -> list[tuple[str, float | None, str]]:
    service = SafeInterventionService(
        algorithm=PolicyAlgorithm.THOMPSON_SAMPLING,
        config=BanditConfig(thompson_draws=32),
        seed=seed,
    )
    result: list[tuple[str, float | None, str]] = []
    for index in range(18):
        context = InterventionContext(
            local_hour=20 if index % 2 == 0 else 9,
            valence=2 + index % 3,
            energy=3 + index % 3,
            emotion_labels=("anxiety",) if index % 2 == 0 else ("sadness",),
            recent_exercise_ids=(POLICY_ACTION_IDS[index % len(POLICY_ACTION_IDS)],)
            if index % len(POLICY_ACTION_IDS) < 8
            else (),
            preferred_tags=("breathing",) if index % 2 == 0 else ("planning",),
        )
        decision = service.suggest(
            "same-internal-user",
            context,
            risk_level=RiskLevel.LOW,
            phq9_item9_response=False,
        )
        assert decision is not None
        result.append((decision.action.action_id, decision.propensity, decision.why_this))
        service.record_outcome(
            "same-internal-user",
            context,
            decision.action.action_id,
            pre_rating=2,
            post_rating=4 if index % 3 else 3,
            started=True,
        )
    return result


def test_bandit_selection_is_deterministic_with_a_seed() -> None:
    assert _decision_sequence(3701) == _decision_sequence(3701)
    assert _decision_sequence(3701) != _decision_sequence(3702)


def test_new_user_gets_a_stable_shared_prior_warm_start_not_a_random_arm() -> None:
    bandit = ContextualBandit(PolicyAlgorithm.THOMPSON_SAMPLING, seed=7)
    first = bandit.propose("new-user", CONTEXT, POLICY_ACTION_IDS)
    second = ContextualBandit(PolicyAlgorithm.THOMPSON_SAMPLING, seed=999).propose(
        "another-new-user", CONTEXT, POLICY_ACTION_IDS
    )
    assert first.chosen_action_id == POLICY_ACTION_IDS[0]
    assert first.probability_for(POLICY_ACTION_IDS[0]) == 1.0
    assert second.chosen_action_id == first.chosen_action_id


def test_population_posterior_warm_starts_a_new_users_arm() -> None:
    bandit = ContextualBandit(
        PolicyAlgorithm.EPSILON_GREEDY,
        config=BanditConfig(epsilon=0.0),
        seed=11,
    )
    action_id = "grounding-54321"
    for _ in range(8):
        bandit.observe("experienced-user", CONTEXT, action_id, 0.75)
    warm_mean = bandit.predict_mean("new-user", CONTEXT, action_id)
    cold_mean = bandit.predict_mean("new-user", CONTEXT, "body-scan")
    proposal = bandit.propose("new-user", CONTEXT, POLICY_ACTION_IDS)
    assert bandit.population_observations == 8
    assert bandit.observations_for("new-user", action_id) == 0
    assert warm_mean > cold_mean
    assert proposal.exploitation_action_id == action_id
    assert proposal.chosen_action_id == action_id


def test_user_and_population_posteriors_both_update_only_from_observed_reward() -> None:
    bandit = ContextualBandit(PolicyAlgorithm.EPSILON_GREEDY, seed=2)
    assert bandit.population_observations == 0
    bandit.observe("internal-user", CONTEXT, "slow-exhale-478", 0.5)
    assert bandit.population_observations == 1
    assert bandit.observations_for("internal-user", "slow-exhale-478") == 1
    assert bandit.observations_for("internal-user", "body-scan") == 0


def test_explanation_uses_top_model_feature_contributions_without_user_text() -> None:
    context = InterventionContext(
        local_hour=9,
        valence=5,
        energy=5,
        emotion_labels=("joy",),
        recent_exercise_ids=("slow-exhale-478",),
        preferred_tags=("breathing",),
    )
    bandit = ContextualBandit(PolicyAlgorithm.EPSILON_GREEDY)
    bandit.observe("private-user-key", context, "slow-exhale-478", 0.8)

    explanation = bandit.explain("private-user-key", context, "slow-exhale-478")

    assert explanation.startswith("Why this? Your recorded outcomes favor")
    assert "when" in explanation
    assert any(
        term in explanation
        for term in ("it is morning", "your energy rating is high", "your valence rating is higher")
    )
    assert "private-user-key" not in explanation
    assert "choose another option or skip" in explanation


def test_unreviewed_action_cannot_be_learned_or_scored() -> None:
    bandit = ContextualBandit()
    try:
        bandit.observe("internal-user", CONTEXT, "unreviewed-action", 0.5)
    except ValueError as error:
        assert "curated" in str(error)
    else:
        raise AssertionError("an action outside the reviewed allow-list was accepted")

    try:
        bandit.predict_mean("internal-user", CONTEXT, "support-options")
    except ValueError as error:
        assert "curated" in str(error)
    else:
        raise AssertionError("deterministic support must not be a learned arm")
