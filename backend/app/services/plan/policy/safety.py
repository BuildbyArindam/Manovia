"""Hard, non-learned guardrails around contextual policy decisions."""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.services.plan.policy.actions import (
    POLICY_ACTION_IDS,
    CuratedAction,
    get_curated_action,
    intersect_curated_actions,
)
from app.services.plan.policy.bandits import (
    BanditConfig,
    BanditProposal,
    ContextualBandit,
    PolicyAlgorithm,
)
from app.services.plan.policy.features import FEATURE_VERSION, InterventionContext
from app.services.plan.policy.reward import (
    DEFAULT_ABANDONMENT_PENALTY,
    bounded_improvement_reward,
)
from app.services.safety.base import RiskLevel

MAX_EXPLORATION_RATE = 0.20
_EXPLORATION_WINDOW = 5
_LOGGER = logging.getLogger("manovia.plan.policy")


class PolicyEngine(Protocol):
    """Learning seam. Safety state is intentionally not part of this protocol."""

    @property
    def name(self) -> str: ...

    def propose(
        self,
        user_key: str,
        context: InterventionContext,
        allowed_action_ids: tuple[str, ...],
    ) -> BanditProposal: ...

    def observe(
        self,
        user_key: str,
        context: InterventionContext,
        action_id: str,
        reward: float,
    ) -> None: ...

    def explain(self, user_key: str, context: InterventionContext, action_id: str) -> str: ...


class ExplorationLedger:
    """Per-user rolling 5-suggestion budget: at most one exploration, never consecutive."""

    def __init__(self, max_exploration_rate: float = MAX_EXPLORATION_RATE) -> None:
        if not math.isfinite(max_exploration_rate) or max_exploration_rate < 0.0:
            raise ValueError("max_exploration_rate must be finite and non-negative")
        # The caller may tighten the ceiling, never loosen the hard 20% cap.
        self._rate = min(max_exploration_rate, MAX_EXPLORATION_RATE)
        self._history: deque[bool] = deque(maxlen=_EXPLORATION_WINDOW)

    @property
    def recent_decisions(self) -> tuple[bool, ...]:
        return tuple(self._history)

    def can_explore(self) -> bool:
        """Whether adding an exploratory suggestion satisfies the rolling cap."""

        if self._history and self._history[-1]:
            return False
        window_length = len(self._history) + 1
        allowed = math.floor(self._rate * window_length + 1e-12)
        return sum(self._history) < allowed

    def record(self, exploratory: bool) -> None:
        """Record a suggestion; reject callers that bypassed the exploration cap."""

        if exploratory and not self.can_explore():
            raise ValueError("exploration would violate the 20% rolling cap")
        self._history.append(exploratory)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """A safe suggestion, its OPE propensity, and the user's unrestricted choice."""

    action: CuratedAction
    why_this: str
    propensity: float | None
    action_propensities: tuple[tuple[str, float], ...]
    alternatives: tuple[str, ...]
    algorithm: str
    exploratory: bool
    policy_consulted: bool
    deterministic_support: bool
    exploration_blocked: bool = False
    model_rejected: bool = False
    safety_reason: str | None = None
    feature_version: int = FEATURE_VERSION
    can_choose_another: bool = True
    can_skip: bool = True

    def __post_init__(self) -> None:
        if not self.why_this.strip():
            raise ValueError("every suggestion needs a why-this line")
        if self.policy_consulted:
            if self.propensity is None or not 0.0 < self.propensity <= 1.0:
                raise ValueError("a policy decision needs a valid selected-action propensity")
            if not math.isclose(
                sum(probability for _action, probability in self.action_propensities),
                1.0,
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                raise ValueError("policy action propensities must sum to one")
        elif self.propensity is not None or self.action_propensities:
            raise ValueError("deterministic support decisions must not carry bandit propensities")


class SafeInterventionService:
    """Apply deterministic safety gates before consulting a learning algorithm."""

    def __init__(
        self,
        engine: PolicyEngine | None = None,
        *,
        algorithm: PolicyAlgorithm | str = PolicyAlgorithm.EPSILON_GREEDY,
        config: BanditConfig | None = None,
        seed: int = 0,
        max_exploration_rate: float = MAX_EXPLORATION_RATE,
    ) -> None:
        self._engine: PolicyEngine = engine or ContextualBandit(
            algorithm=algorithm,
            config=config,
            seed=seed,
        )
        self._max_exploration_rate = min(max_exploration_rate, MAX_EXPLORATION_RATE)
        self._ledgers: dict[str, ExplorationLedger] = {}

    @property
    def engine(self) -> PolicyEngine:
        """Read-only policy seam, primarily for an outcome/update integration."""

        return self._engine

    def suggest(
        self,
        user_key: str,
        context: InterventionContext,
        *,
        risk_level: RiskLevel,
        phq9_item9_response: bool,
        available_action_ids: Sequence[str] | None = None,
    ) -> PolicyDecision | None:
        """Return a curated suggestion, or deterministic support without a model call.

        ``phq9_item9_response`` means any answer to item 9, not only a positive
        value. The safety gate is evaluated before reading the user key, building
        model features, or invoking the policy.
        """

        if risk_level >= RiskLevel.MEDIUM:
            decision = self._support_decision("risk_at_least_medium")
            self._log_decision(decision)
            return decision
        if phq9_item9_response:
            decision = self._support_decision("phq9_item9_response")
            self._log_decision(decision)
            return decision

        allowed = intersect_curated_actions(available_action_ids)
        if not allowed:
            return None

        proposal = self._engine.propose(user_key, context, allowed)
        if not self._valid_proposal(proposal, allowed):
            decision = self._fallback_decision(allowed)
            self._log_decision(decision)
            return decision

        chosen_action = proposal.chosen_action_id
        exploitation_action = proposal.exploitation_action_id
        exploratory = chosen_action != exploitation_action
        ledger = self._ledgers.setdefault(
            user_key,
            ExplorationLedger(self._max_exploration_rate),
        )
        exploration_blocked = exploratory and not ledger.can_explore()
        if exploration_blocked:
            chosen_action = exploitation_action
            probabilities = _point_propensities(allowed, chosen_action)
            exploratory = False
        else:
            probabilities = proposal.probabilities
        ledger.record(exploratory)

        action = get_curated_action(chosen_action, include_support=False)
        if action is None:  # the allow-list check above makes this unreachable; fail closed.
            decision = self._fallback_decision(allowed)
            self._log_decision(decision)
            return decision
        try:
            why = self._engine.explain(user_key, context, chosen_action)
        except Exception:  # a model explanation must not become user-facing free text.
            why = "Why this? The model's baseline feature ranks this curated option. "
            "You can choose another option or skip."
        if not why.strip():
            why = (
                "Why this? The model's baseline feature ranks this curated option. "
                "You can choose another option or skip."
            )
        probability = _probability_for(probabilities, chosen_action)
        decision = PolicyDecision(
            action=action,
            why_this=why,
            propensity=probability,
            action_propensities=probabilities,
            alternatives=tuple(action_id for action_id in allowed if action_id != chosen_action),
            algorithm=self._engine.name,
            exploratory=exploratory,
            policy_consulted=True,
            deterministic_support=False,
            exploration_blocked=exploration_blocked,
        )
        self._log_decision(decision)
        return decision

    def record_outcome(
        self,
        user_key: str,
        context: InterventionContext,
        action_id: str,
        *,
        pre_rating: int | None,
        post_rating: int | None,
        started: bool,
        abandoned: bool = False,
        skipped: bool = False,
        abandonment_penalty: float = DEFAULT_ABANDONMENT_PENALTY,
    ) -> float | None:
        """Learn from a completed/abandoned curated action; skips remain missing."""

        if action_id not in POLICY_ACTION_IDS:
            raise ValueError("only a curated policy action can update a posterior")
        reward = bounded_improvement_reward(
            pre_rating,
            post_rating,
            started=started,
            abandoned=abandoned,
            skipped=skipped,
            abandonment_penalty=abandonment_penalty,
        )
        if reward is not None:
            self._engine.observe(user_key, context, action_id, reward)
        return reward

    @staticmethod
    def _valid_proposal(proposal: BanditProposal, allowed: tuple[str, ...]) -> bool:
        proposed_ids = tuple(action_id for action_id, _probability in proposal.probabilities)
        if (
            proposal.chosen_action_id not in allowed
            or proposal.exploitation_action_id not in allowed
        ):
            return False
        if proposed_ids != allowed:
            return False
        if any(
            not math.isfinite(probability) or probability < 0.0
            for _action_id, probability in proposal.probabilities
        ):
            return False
        if not math.isclose(
            sum(probability for _action_id, probability in proposal.probabilities),
            1.0,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            return False
        return _probability_for(proposal.probabilities, proposal.chosen_action_id) > 0.0

    @staticmethod
    def _support_decision(reason: str) -> PolicyDecision:
        action = get_curated_action("support-options", include_support=True)
        if action is None:
            raise RuntimeError("deterministic support action is missing from its allow-list")
        return PolicyDecision(
            action=action,
            why_this=(
                "Why this? Safety support options are shown instead of a learned suggestion. "
                "You can choose what feels right."
            ),
            propensity=None,
            action_propensities=(),
            alternatives=(),
            algorithm="deterministic_safety_support",
            exploratory=False,
            policy_consulted=False,
            deterministic_support=True,
            safety_reason=reason,
        )

    @staticmethod
    def _fallback_decision(allowed: tuple[str, ...]) -> PolicyDecision:
        action_id = allowed[0]
        action = get_curated_action(action_id, include_support=False)
        if action is None:
            raise RuntimeError("curated fallback action is missing from its allow-list")
        return PolicyDecision(
            action=action,
            why_this=(
                f"Why this? {action.title} is a curated option for this moment. "
                "This is only a suggestion; you can choose another option or skip."
            ),
            propensity=1.0,
            action_propensities=_point_propensities(allowed, action_id),
            alternatives=tuple(candidate for candidate in allowed if candidate != action_id),
            algorithm="safe_deterministic_fallback",
            exploratory=False,
            policy_consulted=True,
            deterministic_support=False,
            model_rejected=True,
        )

    @staticmethod
    def _log_decision(decision: PolicyDecision) -> None:
        """Log OPE fields only: no user key, context, rating, title or free text."""

        event = {
            "event": "intervention_policy_decision",
            "algorithm": decision.algorithm,
            "action_id": decision.action.action_id,
            "propensity": decision.propensity,
            "action_propensities": dict(decision.action_propensities),
            "exploratory": decision.exploratory,
            "policy_consulted": decision.policy_consulted,
            "safety_reason": decision.safety_reason,
            "feature_version": decision.feature_version,
        }
        _LOGGER.info("intervention_policy_decision", extra={"policy_event": event})


def _probability_for(probabilities: tuple[tuple[str, float], ...], action_id: str) -> float:
    return next(
        (probability for candidate, probability in probabilities if candidate == action_id), 0.0
    )


def _point_propensities(allowed: tuple[str, ...], action_id: str) -> tuple[tuple[str, float], ...]:
    return tuple((candidate, float(candidate == action_id)) for candidate in allowed)
