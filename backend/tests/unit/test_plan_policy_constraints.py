from __future__ import annotations

import itertools
import logging
from collections.abc import Sequence

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.plan.policy.actions import (
    POLICY_ACTION_IDS,
    PUBLIC_CURATED_ACTION_IDS,
    SUPPORT_ACTION_ID,
)
from app.services.plan.policy.bandits import BanditProposal, PolicyAlgorithm
from app.services.plan.policy.features import InterventionContext
from app.services.plan.policy.safety import (
    MAX_EXPLORATION_RATE,
    ExplorationLedger,
    SafeInterventionService,
)
from app.services.safety.base import RiskLevel

CONTEXT = InterventionContext(
    local_hour=20,
    valence=3,
    energy=4,
    emotion_labels=("anxiety",),
    recent_exercise_ids=("grounding-54321",),
    preferred_tags=("breathing",),
)


class SpyEngine:
    """A controllable learner used to prove the outer guard owns constraints."""

    name = "spy"

    def __init__(self, *, always_explore: bool = False, rogue: bool = False) -> None:
        self.calls = 0
        self.always_explore = always_explore
        self.rogue = rogue
        self.observed: list[tuple[str, float]] = []

    def propose(
        self,
        user_key: str,
        context: InterventionContext,
        allowed_action_ids: tuple[str, ...],
    ) -> BanditProposal:
        self.calls += 1
        del user_key, context
        if self.rogue:
            return BanditProposal(
                chosen_action_id="unreviewed-action",
                exploitation_action_id="unreviewed-action",
                probabilities=tuple(
                    (action_id, 1.0 / len(allowed_action_ids)) for action_id in allowed_action_ids
                ),
                exploratory=False,
            )
        exploitation = allowed_action_ids[0]
        chosen = (
            allowed_action_ids[1]
            if self.always_explore and len(allowed_action_ids) > 1
            else exploitation
        )
        if chosen == exploitation:
            probabilities = tuple(
                (action_id, float(action_id == exploitation)) for action_id in allowed_action_ids
            )
        else:
            probabilities = tuple(
                (
                    action_id,
                    0.8 if action_id == exploitation else 0.2 if action_id == chosen else 0.0,
                )
                for action_id in allowed_action_ids
            )
        return BanditProposal(
            chosen_action_id=chosen,
            exploitation_action_id=exploitation,
            probabilities=probabilities,
            exploratory=chosen != exploitation,
        )

    def observe(
        self,
        user_key: str,
        context: InterventionContext,
        action_id: str,
        reward: float,
    ) -> None:
        del user_key, context
        self.observed.append((action_id, reward))

    def explain(self, user_key: str, context: InterventionContext, action_id: str) -> str:
        del user_key, context
        return (
            f"Why this? The model feature profile ranks {action_id}. "
            "You can choose another option or skip."
        )


def test_medium_or_higher_risk_never_calls_policy() -> None:
    engine = SpyEngine()
    service = SafeInterventionService(engine)

    for risk in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.IMMINENT):
        decision = service.suggest(
            "internal-test-key",
            CONTEXT,
            risk_level=risk,
            phq9_item9_response=False,
        )
        assert decision is not None
        assert decision.action.action_id == SUPPORT_ACTION_ID
        assert decision.deterministic_support is True
        assert decision.policy_consulted is False
        assert decision.propensity is None
        assert decision.safety_reason == "risk_at_least_medium"
    assert engine.calls == 0


def test_phq9_item_9_response_never_calls_policy() -> None:
    engine = SpyEngine()
    service = SafeInterventionService(engine)
    decision = service.suggest(
        "internal-test-key",
        CONTEXT,
        risk_level=RiskLevel.LOW,
        phq9_item9_response=True,
    )
    assert decision is not None
    assert decision.action.action_id == SUPPORT_ACTION_ID
    assert decision.policy_consulted is False
    assert decision.safety_reason == "phq9_item9_response"
    assert engine.calls == 0


@given(
    risk=st.sampled_from((RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.IMMINENT)),
    item9=st.booleans(),
)
@settings(max_examples=40, deadline=None)
def test_property_risk_gate_precedes_any_policy_call(risk: RiskLevel, item9: bool) -> None:
    engine = SpyEngine()
    decision = SafeInterventionService(engine).suggest(
        "property-user",
        CONTEXT,
        risk_level=risk,
        phq9_item9_response=item9,
    )
    assert decision is not None
    assert decision.action.action_id == SUPPORT_ACTION_ID
    assert not decision.policy_consulted
    assert engine.calls == 0


def test_exploration_cap_is_at_most_twenty_percent_and_never_consecutive() -> None:
    ledger = ExplorationLedger()
    emitted: list[bool] = []
    for _ in range(250):
        exploratory = ledger.can_explore()
        ledger.record(exploratory)
        emitted.append(exploratory)
        assert not (len(emitted) > 1 and emitted[-1] and emitted[-2])
        recent = emitted[-5:]
        assert sum(recent) <= MAX_EXPLORATION_RATE * len(recent)
    assert sum(emitted) <= int(len(emitted) * MAX_EXPLORATION_RATE)


@given(proposed=st.lists(st.booleans(), min_size=0, max_size=300))
@settings(max_examples=80, deadline=None)
def test_property_exploration_cap_under_arbitrary_proposals(proposed: list[bool]) -> None:
    ledger = ExplorationLedger()
    emitted: list[bool] = []
    for wants_exploration in proposed:
        exploratory = wants_exploration and ledger.can_explore()
        ledger.record(exploratory)
        emitted.append(exploratory)
    assert sum(emitted) <= int(len(emitted) * MAX_EXPLORATION_RATE)
    assert all(sum(emitted[index : index + 5]) <= 1 for index in range(len(emitted)))
    assert all(not (left and right) for left, right in itertools.pairwise(emitted))


def test_exploration_cap_is_enforced_outside_learning_code() -> None:
    engine = SpyEngine(always_explore=True)
    service = SafeInterventionService(engine)
    decisions = [
        service.suggest(
            "one-user",
            CONTEXT,
            risk_level=RiskLevel.LOW,
            phq9_item9_response=False,
        )
        for _ in range(30)
    ]
    emitted = [decision.exploratory for decision in decisions if decision is not None]
    assert sum(emitted) <= 6
    assert all(not (left and right) for left, right in itertools.pairwise(emitted))
    assert all(decision is not None for decision in decisions)
    assert sum(decision.exploration_blocked for decision in decisions if decision is not None) > 0


@given(requested=st.lists(st.text(max_size=40), max_size=30))
@settings(max_examples=80, deadline=None)
def test_only_curated_actions_are_ever_returned(requested: list[str]) -> None:
    engine = SpyEngine(rogue=True)
    decision = SafeInterventionService(engine).suggest(
        "property-user",
        CONTEXT,
        risk_level=RiskLevel.LOW,
        phq9_item9_response=False,
        available_action_ids=requested,
    )
    if decision is not None:
        assert decision.action.action_id in PUBLIC_CURATED_ACTION_IDS
        assert decision.action.action_id != SUPPORT_ACTION_ID
        assert set(decision.alternatives) <= set(POLICY_ACTION_IDS)
        assert decision.model_rejected


def test_only_curated_actions_reach_the_engine() -> None:
    class InspectingEngine(SpyEngine):
        received: tuple[str, ...] = ()

        def propose(
            self,
            user_key: str,
            context: InterventionContext,
            allowed_action_ids: tuple[str, ...],
        ) -> BanditProposal:
            self.received = allowed_action_ids
            return super().propose(user_key, context, allowed_action_ids)

    engine = InspectingEngine()
    requested: Sequence[str] = (*POLICY_ACTION_IDS, "unknown", SUPPORT_ACTION_ID)
    decision = SafeInterventionService(engine).suggest(
        "internal-test-key",
        CONTEXT,
        risk_level=RiskLevel.LOW,
        phq9_item9_response=False,
        available_action_ids=requested,
    )
    assert decision is not None
    assert engine.received == POLICY_ACTION_IDS
    assert SUPPORT_ACTION_ID not in engine.received


def test_user_may_choose_another_action_or_skip() -> None:
    decision = SafeInterventionService(SpyEngine()).suggest(
        "internal-test-key",
        CONTEXT,
        risk_level=RiskLevel.NONE,
        phq9_item9_response=False,
    )
    assert decision is not None
    assert decision.can_choose_another is True
    assert decision.can_skip is True
    assert decision.action.action_id not in decision.alternatives
    assert decision.alternatives


def test_policy_logs_propensities_without_context_user_key_or_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="manovia.plan.policy")
    service = SafeInterventionService(SpyEngine())
    service.suggest(
        "must-not-be-logged-user-id",
        CONTEXT,
        risk_level=RiskLevel.LOW,
        phq9_item9_response=False,
    )
    record = next(record for record in caplog.records if hasattr(record, "policy_event"))
    event = record.policy_event
    assert event["propensity"] is not None
    assert sum(event["action_propensities"].values()) == pytest.approx(1.0)
    assert "must-not-be-logged-user-id" not in record.getMessage()
    assert not hasattr(event, "context")
    assert "anxiety" not in str(event)


@pytest.mark.parametrize("algorithm", list(PolicyAlgorithm))
def test_each_policy_returns_a_normalized_curated_propensity(algorithm: PolicyAlgorithm) -> None:
    service = SafeInterventionService(algorithm=algorithm, seed=37)
    decision = service.suggest(
        "internal-test-key",
        CONTEXT,
        risk_level=RiskLevel.LOW,
        phq9_item9_response=False,
    )
    assert decision is not None
    assert decision.policy_consulted
    assert decision.action.action_id in POLICY_ACTION_IDS
    assert decision.propensity is not None
    assert 0.0 < decision.propensity <= 1.0
    assert sum(probability for _, probability in decision.action_propensities) == pytest.approx(1.0)
    assert set(action_id for action_id, _ in decision.action_propensities) == set(POLICY_ACTION_IDS)
