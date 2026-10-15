"""Small, deterministic contextual-bandit implementations for curated actions.

The learning layer only ranks the action ids it is given. It has no risk-level
or PHQ-9 argument; the separate safety wrapper owns those decisions. Linear
posteriors are shared by action and user: user models start from the current
population posterior mean, then learn only from that user's bounded rating
outcomes.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.services.plan.policy.actions import (
    POLICY_ACTION_IDS,
    CuratedAction,
    get_curated_action,
)
from app.services.plan.policy.features import (
    FEATURE_NAMES,
    EncodedFeatures,
    InterventionContext,
    encode_context,
)


class PolicyAlgorithm(StrEnum):
    """The three supported contextual-bandit algorithms."""

    EPSILON_GREEDY = "epsilon_greedy"
    LINUCB = "linucb"
    THOMPSON_SAMPLING = "thompson_sampling"


@dataclass(frozen=True, slots=True)
class BanditConfig:
    """Auditable algorithm settings; the outer safety wrapper caps exploration."""

    epsilon: float = 0.2
    linucb_alpha: float = 0.35
    thompson_draws: int = 48
    population_prior_precision: float = 1.0
    user_prior_precision: float = 2.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.epsilon <= 1.0:
            raise ValueError("epsilon must be in [0, 1]")
        if self.linucb_alpha < 0.0 or not math.isfinite(self.linucb_alpha):
            raise ValueError("linucb_alpha must be finite and non-negative")
        if not 16 <= self.thompson_draws <= 512:
            raise ValueError("thompson_draws must be in 16..512")
        if self.population_prior_precision <= 0.0 or not math.isfinite(
            self.population_prior_precision
        ):
            raise ValueError("population_prior_precision must be finite and positive")
        if self.user_prior_precision <= 0.0 or not math.isfinite(self.user_prior_precision):
            raise ValueError("user_prior_precision must be finite and positive")


@dataclass(frozen=True, slots=True)
class BanditProposal:
    """A learner's candidate plus its exact/estimated selection propensities."""

    chosen_action_id: str
    exploitation_action_id: str
    probabilities: tuple[tuple[str, float], ...]
    exploratory: bool

    def probability_for(self, action_id: str) -> float:
        return next(
            (
                probability
                for candidate, probability in self.probabilities
                if candidate == action_id
            ),
            0.0,
        )


class LinearPosterior:
    """Bayesian linear regression with unit observation noise and rank-one updates."""

    def __init__(
        self,
        *,
        prior_mean: tuple[float, ...] | None = None,
        prior_precision: float = 1.0,
    ) -> None:
        dimension = len(FEATURE_NAMES)
        if prior_mean is None:
            prior_mean = (0.0,) * dimension
        if len(prior_mean) != dimension:
            raise ValueError("prior_mean dimension does not match the feature version")
        if prior_precision <= 0.0 or not math.isfinite(prior_precision):
            raise ValueError("prior_precision must be finite and positive")
        self._precision = prior_precision
        self._inverse = [
            [1.0 / prior_precision if row == column else 0.0 for column in range(dimension)]
            for row in range(dimension)
        ]
        self._response = [prior_precision * value for value in prior_mean]
        self._mean = list(prior_mean)
        self.observations = 0

    @property
    def mean(self) -> tuple[float, ...]:
        return tuple(self._mean)

    def predict(self, features: EncodedFeatures) -> tuple[float, float]:
        """Return posterior predictive mean and parameter-uncertainty variance."""

        self._check_dimension(features.values)
        mean = _dot(self._mean, features.values)
        inverse_times_x = _matrix_vector(self._inverse, features.values)
        variance = max(0.0, _dot(features.values, inverse_times_x))
        return mean, variance

    def update(self, features: EncodedFeatures, reward: float) -> None:
        """Apply an exact Sherman-Morrison rank-one posterior update."""

        self._check_dimension(features.values)
        if not math.isfinite(reward) or not -1.0 <= reward <= 1.0:
            raise ValueError("reward must be finite and bounded in [-1, 1]")
        inverse_times_x = _matrix_vector(self._inverse, features.values)
        denominator = 1.0 + _dot(features.values, inverse_times_x)
        if denominator <= 0.0 or not math.isfinite(denominator):
            raise ArithmeticError("linear posterior lost positive definiteness")
        for row in range(len(self._inverse)):
            for column in range(len(self._inverse)):
                self._inverse[row][column] -= (
                    inverse_times_x[row] * inverse_times_x[column] / denominator
                )
        for index, value in enumerate(features.values):
            self._response[index] += reward * value
        self._mean = _matrix_vector(self._inverse, self._response)
        self.observations += 1

    def _check_dimension(self, features: tuple[float, ...]) -> None:
        if len(features) != len(self._mean):
            raise ValueError("feature dimension does not match the posterior")


class ContextualBandit:
    """Per-user Bayesian linear models with a shared empirical-Bayes warm start."""

    def __init__(
        self,
        algorithm: PolicyAlgorithm | str = PolicyAlgorithm.EPSILON_GREEDY,
        *,
        config: BanditConfig | None = None,
        seed: int = 0,
    ) -> None:
        self.algorithm = PolicyAlgorithm(algorithm)
        self.config = config or BanditConfig()
        self._rng = random.Random(seed)
        self._population: dict[str, LinearPosterior] = {
            action_id: LinearPosterior(
                prior_precision=self.config.population_prior_precision,
            )
            for action_id in POLICY_ACTION_IDS
        }
        self._users: dict[str, dict[str, LinearPosterior]] = {}
        self._population_observations = 0

    @property
    def name(self) -> str:
        return self.algorithm.value

    @property
    def population_observations(self) -> int:
        return self._population_observations

    def propose(
        self,
        user_key: str,
        context: InterventionContext,
        allowed_action_ids: tuple[str, ...],
    ) -> BanditProposal:
        """Propose one allowed arm and return propensities for every eligible arm."""

        self._validate_user_key(user_key)
        arms = self._validate_arms(allowed_action_ids)
        means: dict[str, float] = {}
        variances: dict[str, float] = {}
        for action_id in arms:
            posterior = self._user_posterior(user_key, action_id)
            features = encode_context(context, self._action(action_id))
            means[action_id], variances[action_id] = posterior.predict(features)
        exploitation = _stable_argmax(arms, means)

        if self.algorithm == PolicyAlgorithm.EPSILON_GREEDY:
            probabilities = _epsilon_distribution(arms, exploitation, self.config.epsilon)
            chosen = _sample(self._rng, probabilities)
        elif self.algorithm == PolicyAlgorithm.LINUCB:
            upper_confidence = {
                action_id: means[action_id]
                + self.config.linucb_alpha * math.sqrt(variances[action_id])
                for action_id in arms
            }
            ucb_action = _stable_argmax(arms, upper_confidence)
            probabilities = _epsilon_distribution(arms, ucb_action, self.config.epsilon)
            chosen = _sample(self._rng, probabilities)
        else:
            user_has_data = any(
                self._user_posterior(user_key, action_id).observations > 0 for action_id in arms
            )
            # A brand-new population has no evidence to sample from. Stable
            # tie-breaking is the non-random fallback until shared evidence exists.
            if self._population_observations == 0 and not user_has_data:
                probabilities = _point_distribution(arms, exploitation)
            else:
                probabilities = self._thompson_distribution(arms, means, variances)
            chosen = _sample(self._rng, probabilities)

        return BanditProposal(
            chosen_action_id=chosen,
            exploitation_action_id=exploitation,
            probabilities=tuple((action_id, probabilities[action_id]) for action_id in arms),
            exploratory=chosen != exploitation,
        )

    def observe(
        self,
        user_key: str,
        context: InterventionContext,
        action_id: str,
        reward: float,
    ) -> None:
        """Update the user's posterior and the shared population posterior."""

        self._validate_user_key(user_key)
        if action_id not in POLICY_ACTION_IDS:
            raise ValueError(f"action {action_id!r} is not in the curated policy allow-list")
        action = self._action(action_id)
        features = encode_context(context, action)
        self._user_posterior(user_key, action_id).update(features, reward)
        self._population[action_id].update(features, reward)
        self._population_observations += 1

    def predict_mean(self, user_key: str, context: InterventionContext, action_id: str) -> float:
        """The per-user posterior mean used by the policy and by offline evaluation."""

        self._validate_user_key(user_key)
        if action_id not in POLICY_ACTION_IDS:
            raise ValueError(f"action {action_id!r} is not in the curated policy allow-list")
        features = encode_context(context, self._action(action_id))
        return self._user_posterior(user_key, action_id).predict(features)[0]

    def best_mean_action(
        self,
        user_key: str,
        context: InterventionContext,
        allowed_action_ids: tuple[str, ...] = POLICY_ACTION_IDS,
    ) -> str:
        """Deterministic warm-start/exploitation arm with stable catalogue tie-breaking."""

        self._validate_user_key(user_key)
        arms = self._validate_arms(allowed_action_ids)
        means = {action_id: self.predict_mean(user_key, context, action_id) for action_id in arms}
        return _stable_argmax(arms, means)

    def explain(
        self,
        user_key: str,
        context: InterventionContext,
        action_id: str,
    ) -> str:
        """Build a plain-language explanation from the selected arm's top features."""

        self._validate_user_key(user_key)
        action = self._action(action_id)
        encoded = encode_context(context, action)
        posterior = self._user_posterior(user_key, action_id)
        contributions = [
            (coefficient * value, term)
            for coefficient, value, term in zip(
                posterior.mean, encoded.values, encoded.explanation_terms, strict=True
            )
            if term is not None
            and coefficient * value > 1e-9
            and term != "the shared starting point"
        ]
        contributions.sort(key=lambda row: row[0], reverse=True)
        terms: list[str] = []
        for _contribution, term in contributions:
            if term not in terms:
                terms.append(term)
            if len(terms) == 2:
                break
        if not terms:
            why = (
                f"Why this? The model's baseline feature currently ranks {action.title.lower()} "
                "among the curated options. It is only a suggestion; you can choose another "
                "option or skip."
            )
        elif posterior.observations:
            joined = _join_terms(terms)
            why = (
                f"Why this? Your recorded outcomes favor {action.title.lower()} when {joined}. "
                "This is an association, not a promise of benefit; you can choose another "
                "option or skip."
            )
        else:
            joined = _join_terms(terms)
            why = (
                f"Why this? The shared population starting point favors {action.title.lower()} "
                f"when {joined}. It is only a starting suggestion; you can choose another "
                "option or skip."
            )
        return why

    def observations_for(self, user_key: str, action_id: str) -> int:
        """Observation count for tests and diagnostics; no user identifier is emitted."""

        self._validate_user_key(user_key)
        if action_id not in POLICY_ACTION_IDS:
            raise ValueError(f"action {action_id!r} is not in the curated policy allow-list")
        state = self._users.get(user_key, {})
        posterior = state.get(action_id)
        return posterior.observations if posterior is not None else 0

    def _thompson_distribution(
        self,
        arms: tuple[str, ...],
        means: dict[str, float],
        variances: dict[str, float],
    ) -> dict[str, float]:
        counts = dict.fromkeys(arms, 0)
        for _ in range(self.config.thompson_draws):
            sampled = {
                action_id: means[action_id]
                + math.sqrt(variances[action_id]) * self._rng.gauss(0.0, 1.0)
                for action_id in arms
            }
            counts[_stable_argmax(arms, sampled)] += 1
        probabilities = {
            action_id: counts[action_id] / self.config.thompson_draws for action_id in arms
        }
        # Monte-Carlo Thompson propensities can be zero for a low-probability
        # action. That is an honest finite-sample policy, not an invented floor.
        return probabilities

    def _user_posterior(self, user_key: str, action_id: str) -> LinearPosterior:
        user_state = self._users.setdefault(user_key, {})
        if action_id not in user_state:
            user_state[action_id] = LinearPosterior(
                prior_mean=self._population[action_id].mean,
                prior_precision=self.config.user_prior_precision,
            )
        return user_state[action_id]

    @staticmethod
    def _action(action_id: str) -> CuratedAction:
        action = get_curated_action(action_id, include_support=False)
        if action is None:
            raise ValueError(f"action {action_id!r} is not in the curated policy allow-list")
        return action

    @staticmethod
    def _validate_user_key(user_key: str) -> None:
        if not user_key or len(user_key) > 128:
            raise ValueError("user_key must be a non-empty internal key of at most 128 characters")

    @staticmethod
    def _validate_arms(action_ids: tuple[str, ...]) -> tuple[str, ...]:
        if not action_ids:
            raise ValueError("at least one curated action must be available")
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("available action ids must be unique")
        if any(action_id not in POLICY_ACTION_IDS for action_id in action_ids):
            raise ValueError("available actions must be from the curated policy allow-list")
        return action_ids


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _matrix_vector(matrix: list[list[float]], vector: Sequence[float]) -> list[float]:
    return [sum(value * vector[index] for index, value in enumerate(row)) for row in matrix]


def _stable_argmax(action_ids: tuple[str, ...], scores: dict[str, float]) -> str:
    """First allow-listed arm wins exact ties, including the cold-start tie."""

    return max(action_ids, key=lambda action_id: scores[action_id])


def _epsilon_distribution(
    action_ids: tuple[str, ...], greedy_action_id: str, epsilon: float
) -> dict[str, float]:
    exploration = epsilon / len(action_ids)
    probabilities = {action_id: exploration for action_id in action_ids}
    probabilities[greedy_action_id] += 1.0 - epsilon
    return probabilities


def _point_distribution(action_ids: tuple[str, ...], chosen_action_id: str) -> dict[str, float]:
    return {action_id: float(action_id == chosen_action_id) for action_id in action_ids}


def _sample(rng: random.Random, probabilities: dict[str, float]) -> str:
    target = rng.random()
    cumulative = 0.0
    last_action = next(reversed(probabilities))
    for action_id, probability in probabilities.items():
        cumulative += probability
        if target < cumulative:
            return action_id
    return last_action


def _join_terms(terms: list[str]) -> str:
    if len(terms) == 1:
        return terms[0]
    return f"{terms[0]} and {terms[1]}"


DEFAULT_FEATURE_COUNT: Final[int] = len(FEATURE_NAMES)
