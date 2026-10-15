"""Synthetic JITAI evaluation: constrained contextual bandits and logged-data OPE.

This module generates heterogeneous, fictional users in memory. Its outcomes
are bounded changes on a 1-5 pre/post self-rating scale, not a clinical measure.
No personal text or real-user data is accepted or written.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.services.plan.policy.actions import (
    CURATED_EXERCISE_IDS,
    POLICY_ACTION_IDS,
    get_curated_action,
)
from app.services.plan.policy.bandits import BanditConfig, ContextualBandit, PolicyAlgorithm
from app.services.plan.policy.features import InterventionContext
from app.services.plan.policy.reward import bounded_improvement_reward
from app.services.plan.policy.safety import SafeInterventionService
from app.services.safety.base import RiskLevel

from manovia_ml.hashing import DatasetRef
from manovia_ml.seeds import derive_seed
from manovia_ml.simulation.jitai_config import JITAIConfig


@dataclass(frozen=True, slots=True)
class UserProfile:
    """A synthetic preference/context group; no identity or natural-language data."""

    group: str
    best_action_id: str
    preferred_tags: tuple[str, ...]
    typical_hour: int
    typical_valence: int
    typical_energy: int
    emotion: str


PROFILES: tuple[UserProfile, ...] = (
    UserProfile(
        group="high-energy-evening",
        best_action_id="slow-exhale-478",
        preferred_tags=("breathing",),
        typical_hour=20,
        typical_valence=3,
        typical_energy=5,
        emotion="joy",
    ),
    UserProfile(
        group="low-energy-morning",
        best_action_id="small-action-planner",
        preferred_tags=("planning",),
        typical_hour=9,
        typical_valence=2,
        typical_energy=1,
        emotion="sadness",
    ),
    UserProfile(
        group="anxiety-afternoon",
        best_action_id="grounding-54321",
        preferred_tags=("grounding",),
        typical_hour=14,
        typical_valence=2,
        typical_energy=3,
        emotion="anxiety",
    ),
    UserProfile(
        group="reflective-evening",
        best_action_id="emotion-labelling",
        preferred_tags=("emotion-awareness", "labelling"),
        typical_hour=19,
        typical_valence=3,
        typical_energy=2,
        emotion="calm",
    ),
)
_PROFILE_BY_GROUP = {profile.group: profile for profile in PROFILES}


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    mean: float
    lower: float
    upper: float


def mean_ci(values: list[float]) -> ConfidenceInterval:
    """Normal-approximation 95% CI across independent seeded runs."""

    if not values:
        raise ValueError("confidence intervals need at least one observation")
    mean = statistics.fmean(values)
    if len(values) == 1:
        return ConfidenceInterval(mean, mean, mean)
    standard_error = statistics.stdev(values) / math.sqrt(len(values))
    half_width = 1.96 * standard_error
    return ConfidenceInterval(mean, mean - half_width, mean + half_width)


class _HashDigest(Protocol):
    def update(self, data: bytes, /) -> None: ...

    def hexdigest(self) -> str: ...


@dataclass
class SyntheticDataDigest:
    """Hash generated events without persisting them or logging synthetic user keys."""

    _digest: _HashDigest = field(default_factory=hashlib.sha256)
    rows: int = 0
    group_counts: dict[str, int] = field(default_factory=dict)

    def add(
        self,
        *,
        seed: int,
        policy: str,
        group: str,
        context: InterventionContext,
        action_id: str,
        reward: float,
        propensity: float,
    ) -> None:
        payload = {
            "seed": seed,
            "policy": policy,
            "group": group,
            "hour": context.local_hour,
            "valence": context.valence,
            "energy": context.energy,
            "emotions": context.emotion_labels,
            "recent_exercises": context.recent_exercise_ids,
            "preferences": context.preferred_tags,
            "action": action_id,
            "reward": round(reward, 8),
            "propensity": round(propensity, 8),
        }
        self._digest.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        self._digest.update(b"\n")
        self.rows += 1
        self.group_counts[group] = self.group_counts.get(group, 0) + 1

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


@dataclass(frozen=True, slots=True)
class OnlineSeedResult:
    policy: str
    seed: int
    cumulative_reward: float
    cumulative_regret: float
    early_mean_regret: float
    late_mean_regret: float
    group_mean_reward: dict[str, float]
    explanations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OPESeedResult:
    seed: int
    samples: int
    ground_truth: float
    ips: float
    doubly_robust: float
    behavior_value: float


@dataclass(frozen=True, slots=True)
class JITAIResults:
    config: JITAIConfig
    online: dict[str, tuple[OnlineSeedResult, ...]]
    ope: tuple[OPESeedResult, ...]
    dataset: DatasetRef
    explanation_examples: tuple[str, ...]


def policy_config(config: JITAIConfig) -> BanditConfig:
    return BanditConfig(
        epsilon=config.bandit.epsilon,
        linucb_alpha=config.bandit.linucb_alpha,
        thompson_draws=config.bandit.thompson_draws,
        population_prior_precision=config.bandit.population_prior_precision,
        user_prior_precision=config.bandit.user_prior_precision,
    )


def _user_key(group: str, index: int) -> str:
    """Stable internal simulation key; never sent to output or MLflow."""

    return f"synthetic:{group}:{index}"


def _make_users(users_per_group: int) -> tuple[tuple[str, UserProfile], ...]:
    return tuple(
        (_user_key(profile.group, index), profile)
        for profile in PROFILES
        for index in range(users_per_group)
    )


def _context_for(
    profile: UserProfile,
    rng: random.Random,
    recent_exercises: deque[str],
    *,
    jitter: int,
) -> InterventionContext:
    hour = max(0, min(23, profile.typical_hour + rng.randint(-jitter, jitter)))
    valence = max(1, min(5, profile.typical_valence + rng.choice((-1, 0, 0, 1))))
    energy = max(1, min(5, profile.typical_energy + rng.choice((-1, 0, 0, 1))))
    emotions: tuple[str, ...] = (profile.emotion,)
    if profile.emotion != "neutral" and rng.random() < 0.1:
        emotions = (profile.emotion, "neutral")
    return InterventionContext(
        local_hour=hour,
        valence=valence,
        energy=energy,
        emotion_labels=emotions,
        recent_exercise_ids=tuple(recent_exercises),
        preferred_tags=profile.preferred_tags,
    )


def _mean_improvement(profile: UserProfile, context: InterventionContext, action_id: str) -> float:
    """Synthetic preference response with real context heterogeneity, not a claim."""

    action = get_curated_action(action_id, include_support=False)
    if action is None:
        raise ValueError(f"unknown simulated arm: {action_id}")
    value = -0.12
    if action_id == profile.best_action_id:
        value += 0.50
    if set(action.tags) & set(context.preferred_tags):
        value += 0.08
    if context.local_hour >= 17 or context.local_hour < 5:
        if "breathing" in action.tags and profile.group == "high-energy-evening":
            value += 0.08
        if action_id == "emotion-labelling" and profile.group == "reflective-evening":
            value += 0.04
    if context.energy is not None and context.energy >= 4 and "breathing" in action.tags:
        value += 0.06
    if "anxiety" in context.emotion_labels and "grounding" in action.tags:
        value += 0.08
    if context.valence is not None and context.valence <= 2 and "planning" in action.tags:
        value += 0.05
    if action_id in context.recent_exercise_ids:
        value -= 0.10
    # The simulator's pre rating is 3/5, so this range remains measurable
    # without needing an unbounded or fabricated outcome.
    return max(-0.5, min(0.5, value))


def _expected_reward(
    profile: UserProfile,
    context: InterventionContext,
    action_id: str,
    config: JITAIConfig,
) -> float:
    """Expected generated rating reward, including rounding, noise and abandonment."""

    improvement = _mean_improvement(profile, context, action_id)
    noise = config.online.reward_noise_std
    if noise == 0.0:
        expected_rating_reward = (max(1, min(5, round(3 + 4 * improvement))) - 3) / 4.0
    else:
        thresholds = (-0.375, -0.125, 0.125, 0.375)
        probabilities = [_normal_cdf((threshold - improvement) / noise) for threshold in thresholds]
        rating_probabilities = (
            probabilities[0],
            probabilities[1] - probabilities[0],
            probabilities[2] - probabilities[1],
            probabilities[3] - probabilities[2],
            1.0 - probabilities[3],
        )
        expected_rating_reward = sum(
            probability * ((rating - 3) / 4.0)
            for rating, probability in zip(range(1, 6), rating_probabilities, strict=True)
        )
    abandon_rate = config.online.abandonment_probability
    return (
        1.0 - abandon_rate
    ) * expected_rating_reward - abandon_rate * config.bandit.abandonment_penalty


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _sample_outcome(
    profile: UserProfile,
    context: InterventionContext,
    action_id: str,
    rng: random.Random,
    config: JITAIConfig,
) -> tuple[float, bool, int | None]:
    """Generate an integer pre/post self-rating outcome using the shared reward function."""

    expected = _mean_improvement(profile, context, action_id)
    observed_change = rng.gauss(expected, config.online.reward_noise_std)
    post_rating = max(1, min(5, round(3 + 4 * observed_change)))
    abandoned = rng.random() < config.online.abandonment_probability
    reward = bounded_improvement_reward(
        3,
        None if abandoned else post_rating,
        started=True,
        abandoned=abandoned,
        abandonment_penalty=config.bandit.abandonment_penalty,
    )
    if reward is None:
        raise AssertionError("a started or abandoned synthetic session must have an outcome")
    return reward, abandoned, None if abandoned else post_rating


def run_online_seed(
    config: JITAIConfig,
    policy: str,
    seed: int,
    digest: SyntheticDataDigest | None = None,
) -> OnlineSeedResult:
    """One online simulation run; learned policies always pass through safety caps."""

    if policy not in config.algorithms:
        raise ValueError(f"policy {policy!r} is not configured")
    digest = digest or SyntheticDataDigest()
    context_rng = random.Random(derive_seed(seed, "contexts", policy))
    reward_rng = random.Random(derive_seed(seed, "rewards", policy))
    action_rng = random.Random(derive_seed(seed, "actions", policy))
    users = _make_users(config.online.users_per_group)
    recent_by_user: dict[str, deque[str]] = {
        user_key: deque(maxlen=14) for user_key, _profile in users
    }
    group_reward: dict[str, float] = defaultdict(float)
    group_count: dict[str, int] = defaultdict(int)
    step_regrets: list[float] = []
    cumulative_reward = 0.0
    cumulative_regret = 0.0

    service: SafeInterventionService | None = None
    if policy != "random":
        service = SafeInterventionService(
            algorithm=PolicyAlgorithm(policy),
            config=policy_config(config),
            seed=derive_seed(seed, "bandit", policy),
            max_exploration_rate=config.bandit.exploration_cap,
        )

    for _step in range(config.online.steps_per_user):
        step_regret = 0.0
        for user_key, profile in users:
            context = _context_for(
                profile,
                context_rng,
                recent_by_user[user_key],
                jitter=config.online.context_jitter,
            )
            if policy == "random":
                # Offline-only reference; it is never registered with the serving wrapper.
                action_id = action_rng.choice(POLICY_ACTION_IDS)
                propensity = 1.0 / len(POLICY_ACTION_IDS)
            else:
                assert service is not None
                decision = service.suggest(
                    user_key,
                    context,
                    risk_level=RiskLevel.LOW,
                    phq9_item9_response=False,
                )
                if decision is None or decision.propensity is None:
                    raise AssertionError("a safe synthetic context should yield a bandit decision")
                action_id = decision.action.action_id
                propensity = decision.propensity

            reward, abandoned, post_rating = _sample_outcome(
                profile,
                context,
                action_id,
                reward_rng,
                config,
            )
            means = {
                candidate: _expected_reward(profile, context, candidate, config)
                for candidate in POLICY_ACTION_IDS
            }
            oracle_mean = max(means.values())
            regret = max(0.0, oracle_mean - means[action_id])
            cumulative_reward += reward
            cumulative_regret += regret
            step_regret += regret
            group_reward[profile.group] += reward
            group_count[profile.group] += 1
            digest.add(
                seed=seed,
                policy=policy,
                group=profile.group,
                context=context,
                action_id=action_id,
                reward=reward,
                propensity=propensity,
            )

            if policy != "random":
                assert service is not None
                service.record_outcome(
                    user_key,
                    context,
                    action_id,
                    pre_rating=3,
                    post_rating=post_rating,
                    started=True,
                    abandoned=abandoned,
                    abandonment_penalty=config.bandit.abandonment_penalty,
                )
            if action_id in CURATED_EXERCISE_IDS:
                recent_by_user[user_key].append(action_id)
        step_regrets.append(step_regret / len(users))

    quarter = max(1, len(step_regrets) // 4)
    early_mean_regret = statistics.fmean(step_regrets[:quarter])
    late_mean_regret = statistics.fmean(step_regrets[-quarter:])
    group_means = {group: group_reward[group] / group_count[group] for group in sorted(group_count)}

    explanations: tuple[str, ...] = ()
    if policy == "thompson_sampling" and service is not None:
        examples = []
        for index in range(5):
            profile = PROFILES[index % len(PROFILES)]
            user_key = _user_key(profile.group, index % config.online.users_per_group)
            context = _context_for(
                profile,
                random.Random(derive_seed(seed, "explanation", str(index))),
                recent_by_user[user_key],
                jitter=0,
            )
            decision = service.suggest(
                user_key,
                context,
                risk_level=RiskLevel.LOW,
                phq9_item9_response=False,
            )
            if decision is not None:
                examples.append(decision.why_this)
        explanations = tuple(examples)

    return OnlineSeedResult(
        policy=policy,
        seed=seed,
        cumulative_reward=cumulative_reward,
        cumulative_regret=cumulative_regret,
        early_mean_regret=early_mean_regret,
        late_mean_regret=late_mean_regret,
        group_mean_reward=group_means,
        explanations=explanations,
    )


@dataclass(frozen=True, slots=True)
class LoggedEvent:
    """A synthetic, text-free logged decision for offline OPE."""

    user_key: str
    group: str
    context: InterventionContext
    action_id: str
    reward: float
    propensity: float


def run_ope_seed(
    config: JITAIConfig,
    seed: int,
    digest: SyntheticDataDigest | None = None,
) -> OPESeedResult:
    """Evaluate a greedy posterior-mean target on disjoint uniform-behavior logs."""

    digest = digest or SyntheticDataDigest()
    users = _make_users(config.offline.users_per_group)
    context_rng = random.Random(derive_seed(seed, "ope-context"))
    behavior_rng = random.Random(derive_seed(seed, "ope-behavior"))
    reward_rng = random.Random(derive_seed(seed, "ope-reward"))
    histories: dict[str, deque[str]] = {user_key: deque(maxlen=14) for user_key, _profile in users}
    train_logs: list[LoggedEvent] = []
    evaluation_logs: list[LoggedEvent] = []
    behavior_propensity = 1.0 / len(POLICY_ACTION_IDS)
    training_count = config.offline.train_events_per_user
    evaluation_count = config.offline.evaluation_events_per_user

    for user_key, profile in users:
        for event_index in range(training_count + evaluation_count):
            context = _context_for(
                profile,
                context_rng,
                histories[user_key],
                jitter=config.online.context_jitter,
            )
            action_id = behavior_rng.choice(POLICY_ACTION_IDS)
            reward, _abandoned, _post_rating = _sample_outcome(
                profile,
                context,
                action_id,
                reward_rng,
                config,
            )
            event = LoggedEvent(
                user_key=user_key,
                group=profile.group,
                context=context,
                action_id=action_id,
                reward=reward,
                propensity=behavior_propensity,
            )
            digest.add(
                seed=seed,
                policy="ope_uniform_behavior",
                group=profile.group,
                context=context,
                action_id=action_id,
                reward=reward,
                propensity=behavior_propensity,
            )
            if event_index < training_count:
                train_logs.append(event)
            else:
                evaluation_logs.append(event)
            if action_id in CURATED_EXERCISE_IDS:
                histories[user_key].append(action_id)

    target = ContextualBandit(
        PolicyAlgorithm.EPSILON_GREEDY,
        config=BanditConfig(
            epsilon=0.0,
            linucb_alpha=config.bandit.linucb_alpha,
            thompson_draws=config.bandit.thompson_draws,
            population_prior_precision=config.bandit.population_prior_precision,
            user_prior_precision=config.bandit.user_prior_precision,
        ),
        seed=derive_seed(seed, "ope-target"),
    )
    for event in train_logs:
        target.observe(event.user_key, event.context, event.action_id, event.reward)

    ips_sum = 0.0
    dr_sum = 0.0
    truth_sum = 0.0
    behavior_sum = 0.0
    for event in evaluation_logs:
        profile = _PROFILE_BY_GROUP[event.group]
        target_action = target.best_mean_action(event.user_key, event.context)
        q_target = target.predict_mean(event.user_key, event.context, target_action)
        q_logged = target.predict_mean(event.user_key, event.context, event.action_id)
        correction = (
            (event.reward - q_logged) / event.propensity
            if event.action_id == target_action
            else 0.0
        )
        ips_sum += event.reward / event.propensity if event.action_id == target_action else 0.0
        dr_sum += q_target + correction
        truth_sum += _expected_reward(profile, event.context, target_action, config)
        behavior_sum += _expected_reward(profile, event.context, event.action_id, config)

    sample_count = len(evaluation_logs)
    return OPESeedResult(
        seed=seed,
        samples=sample_count,
        ground_truth=truth_sum / sample_count,
        ips=ips_sum / sample_count,
        doubly_robust=dr_sum / sample_count,
        behavior_value=behavior_sum / sample_count,
    )


def run_evaluation(config: JITAIConfig) -> JITAIResults:
    """Run 20+ independent online and logged-data seeds in memory."""

    digest = SyntheticDataDigest()
    online_runs: dict[str, list[OnlineSeedResult]] = {policy: [] for policy in config.algorithms}
    ope_runs: list[OPESeedResult] = []
    examples: tuple[str, ...] = ()
    for seed_index in range(config.seeds):
        seed = derive_seed(config.seed, "replicate", str(seed_index))
        for policy in config.algorithms:
            policy_seed = derive_seed(seed, "online", policy)
            result = run_online_seed(config, policy, policy_seed, digest)
            online_runs[policy].append(result)
            if policy == "thompson_sampling" and seed_index == 0:
                examples = result.explanations
        ope_runs.append(run_ope_seed(config, derive_seed(seed, "offline-ope"), digest))

    dataset = DatasetRef(
        name="synthetic_jitai_interactions",
        path="generated-in-memory://jitai-synthetic-v1",
        sha256=digest.sha256,
        rows=digest.rows,
        labels=dict(sorted(digest.group_counts.items())),
    )
    return JITAIResults(
        config=config,
        online={policy: tuple(results) for policy, results in online_runs.items()},
        ope=tuple(ope_runs),
        dataset=dataset,
        explanation_examples=examples,
    )


def render_report(results: JITAIResults, *, mlflow_run_id: str | None = None) -> str:
    """Render concise research results; no synthetic event rows are included."""

    config = results.config
    lines = [
        "# Day 37 - Personalised intervention policy evaluation",
        "",
        "**Status:** synthetic research only; not clinically validated or enabled in the product.",
        "",
        f"- Config: `{config.name}`; seed base `{config.seed}`; "
        f"independent seeds `{config.seeds}`.",
        f"- Synthetic in-memory interactions: `{results.dataset.rows}`; "
        f"SHA-256 `{results.dataset.sha256}`.",
        f"- Curated policy arms: `{len(POLICY_ACTION_IDS)}`; "
        "safety cap: at most 20% exploratory suggestions, never consecutive.",
        "- Reward: bounded pre/post 1-5 rating improvement; a small penalty applies only to a "
        "started abandoned session. A skip is missing, not failure.",
        "- No message text, personal data, time-in-app or streak signal was used.",
        "",
        "## Online simulation - cumulative reward and regret",
        "",
        f"Each seed simulates {config.online.users_per_group * len(PROFILES)} users x "
        f"{config.online.steps_per_user} decisions per configured policy. CIs are 95% "
        "normal-approximation intervals across independent seeds.",
        "",
        (
            "| Policy | Cumulative reward, mean [95% CI] | "
            "Cumulative regret, mean [95% CI] | Regret / decision: early -> late |"
        ),
        "| --- | ---: | ---: | ---: |",
    ]
    for policy in config.algorithms:
        runs = results.online[policy]
        reward_ci = mean_ci([run.cumulative_reward for run in runs])
        regret_ci = mean_ci([run.cumulative_regret for run in runs])
        early = statistics.fmean(run.early_mean_regret for run in runs)
        late = statistics.fmean(run.late_mean_regret for run in runs)
        lines.append(
            f"| `{policy}` | {reward_ci.mean:.3f} "
            f"[{reward_ci.lower:.3f}, {reward_ci.upper:.3f}] | "
            f"{regret_ci.mean:.3f} [{regret_ci.lower:.3f}, {regret_ci.upper:.3f}] | "
            f"{early:.4f} -> {late:.4f} |"
        )
    lines.extend(
        [
            "",
            (
                "The `random` row is an **offline-only, unconstrained reference** and is not "
                "eligible for serving. The other three policies pass through the hard safety "
                "wrapper; exploratory suggestions are rate-limited per synthetic user."
            ),
            "",
            "## Offline evaluation on logged uniform-behavior data",
            "",
            (
                "The posterior-mean target is trained on a disjoint training portion. Held-out "
                "behavior logs record `p(a|x) = 1 / number_of_arms`; IPS and doubly robust (DR) "
                "are evaluated against the simulator's expected target-policy reward. "
                "Evaluation outcomes do not train the model."
            ),
            "",
            "| Estimate | Mean [95% CI] | Simulator ground truth | Absolute gap |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    ground = mean_ci([run.ground_truth for run in results.ope])
    ips = mean_ci([run.ips for run in results.ope])
    dr = mean_ci([run.doubly_robust for run in results.ope])
    lines.append(
        f"| IPS | {ips.mean:.4f} [{ips.lower:.4f}, {ips.upper:.4f}] | "
        f"{ground.mean:.4f} | {abs(ips.mean - ground.mean):.4f} |"
    )
    lines.append(
        f"| DR | {dr.mean:.4f} [{dr.lower:.4f}, {dr.upper:.4f}] | "
        f"{ground.mean:.4f} | {abs(dr.mean - ground.mean):.4f} |"
    )
    behavior = mean_ci([run.behavior_value for run in results.ope])
    lines.extend(
        [
            f"| Logged behavior value | {behavior.mean:.4f} "
            f"[{behavior.lower:.4f}, {behavior.upper:.4f}] | n/a | n/a |",
            "",
            "## Fairness by synthetic user type",
            "",
            (
                "Values are mean bounded reward per decision, averaged over seeds. `FLAG` means "
                "the maximum group gap exceeds the configured descriptive threshold; it is a "
                "review signal, not a fairness certification."
            ),
            "",
            (
                "| Policy | High-energy evening | Low-energy morning | Anxiety afternoon | "
                "Reflective evening | Max-min gap | Review |"
            ),
            "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    groups = [profile.group for profile in PROFILES]
    for policy in config.algorithms:
        runs = results.online[policy]
        group_means = {
            group: statistics.fmean(run.group_mean_reward[group] for run in runs)
            for group in groups
        }
        gap = max(group_means.values()) - min(group_means.values())
        review = "FLAG" if gap > config.fairness_gap_flag else "OK"
        lines.append(
            f"| `{policy}` | {group_means[groups[0]]:.3f} | {group_means[groups[1]]:.3f} | "
            f"{group_means[groups[2]]:.3f} | {group_means[groups[3]]:.3f} | "
            f"{gap:.3f} | {review} |"
        )
    lines.extend(["", "## Five 'why this?' lines for wording review", ""])
    for index, explanation in enumerate(results.explanation_examples[:5], start=1):
        lines.append(f"{index}. {explanation}")
    if len(results.explanation_examples) < 5:
        lines.append("Fewer than five model-generated examples were available in this run.")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            (
                "- Outcomes and four user groups are synthetic and hand-specified; the simulator "
                "is not evidence of real-user benefit, safety, or clinical effectiveness."
            ),
            (
                "- OPE is one-step contextual evaluation. Uniform synthetic behavior gives "
                "overlap, but real logs can be confounded, nonstationary, censored by overrides, "
                "and unsafe to explore."
            ),
            (
                "- Thompson action propensities use finite Monte Carlo draws and are exact only "
                "for that finite-draw randomized implementation."
            ),
            (
                "- The linear feature model cannot represent every interaction. The population "
                "prior is empirical-Bayes warm-starting, not a universal treatment effect."
            ),
            (
                "- State and exploration ledgers are process-local. Durable per-user state, "
                "privacy review, native-language copy review, and deployment integration are "
                "prerequisites for product use."
            ),
        ]
    )
    if mlflow_run_id:
        lines.extend(["", f"MLflow run id: `{mlflow_run_id}`."])
    return "\n".join(lines) + "\n"


def result_metrics(results: JITAIResults) -> dict[str, float]:
    """Flatten headline results for MLflow without storing event-level records."""

    metrics: dict[str, float] = {}
    for policy, runs in results.online.items():
        metrics[f"online.{policy}.cumulative_reward.mean"] = statistics.fmean(
            run.cumulative_reward for run in runs
        )
        metrics[f"online.{policy}.cumulative_regret.mean"] = statistics.fmean(
            run.cumulative_regret for run in runs
        )
        metrics[f"online.{policy}.early_regret_per_decision.mean"] = statistics.fmean(
            run.early_mean_regret for run in runs
        )
        metrics[f"online.{policy}.late_regret_per_decision.mean"] = statistics.fmean(
            run.late_mean_regret for run in runs
        )
        for profile in PROFILES:
            metrics[f"fairness.{policy}.{profile.group}.mean_reward"] = statistics.fmean(
                run.group_mean_reward[profile.group] for run in runs
            )
    for name, key in (
        ("ground_truth", "ground_truth"),
        ("ips", "ips"),
        ("doubly_robust", "doubly_robust"),
        ("behavior", "behavior_value"),
    ):
        metrics[f"ope.{name}.mean"] = statistics.fmean(getattr(run, key) for run in results.ope)
    return metrics


def result_payload(results: JITAIResults) -> dict[str, Any]:
    """Machine-readable aggregate metrics and provenance (no event rows)."""

    return {
        "config": results.config.model_dump(mode="json"),
        "dataset": results.dataset.as_dict(),
        "online": {
            policy: [
                {
                    "seed": run.seed,
                    "cumulative_reward": run.cumulative_reward,
                    "cumulative_regret": run.cumulative_regret,
                    "early_mean_regret": run.early_mean_regret,
                    "late_mean_regret": run.late_mean_regret,
                    "group_mean_reward": run.group_mean_reward,
                }
                for run in runs
            ]
            for policy, runs in results.online.items()
        },
        "ope": [
            {
                "seed": run.seed,
                "samples": run.samples,
                "ground_truth": run.ground_truth,
                "ips": run.ips,
                "doubly_robust": run.doubly_robust,
                "behavior_value": run.behavior_value,
            }
            for run in results.ope
        ],
        "explanation_examples": list(results.explanation_examples),
    }


def print_summary(results: JITAIResults, *, mlflow_run_id: str | None = None) -> str:
    """Print a human-readable summary and return the exact text for reports."""

    report = render_report(results, mlflow_run_id=mlflow_run_id)
    print(report, end="")
    return report


def main(argv: list[str] | None = None) -> int:
    """Run and log the full evaluation to local/default MLflow plus a text report."""

    import argparse

    from manovia_ml.seeds import seed_everything
    from manovia_ml.simulation.jitai_config import load_jitai_config
    from manovia_ml.tracking import tracked_run

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="jitai.yaml", help="YAML config in ml/configs")
    args = parser.parse_args(argv)
    config = load_jitai_config(args.config)
    seed_state = seed_everything(config.seed)
    results = run_evaluation(config)
    config_path = Path(args.config)
    if not config_path.is_absolute() and not config_path.is_file():
        config_path = Path(__file__).resolve().parents[3] / "configs" / args.config
    data_ref = results.dataset

    with tracked_run(
        config,
        phase="evaluation",
        data_refs=(data_ref,),
        seed_state=seed_state,
        params={
            "simulation.users_per_group": config.online.users_per_group,
            "simulation.steps_per_user": config.online.steps_per_user,
            "offline.train_events_per_user": config.offline.train_events_per_user,
            "offline.evaluation_events_per_user": config.offline.evaluation_events_per_user,
            "policy.action_count": len(POLICY_ACTION_IDS),
            "reward.kind": "bounded_pre_post_rating_improvement",
            "reward.abandonment_penalty": config.bandit.abandonment_penalty,
            "reward.skip_is_missing": True,
            "reward.engagement_terms": False,
        },
        tags={"manovia.research_only": "true", "manovia.personal_data": "false"},
        config_path=config_path,
    ) as run:
        report = render_report(results, mlflow_run_id=run.run_id)
        run.log_metrics(result_metrics(results))
        run.log_dict(
            {
                "dataset_sha256": data_ref.sha256,
                "dataset_rows": data_ref.rows,
                "confidence_interval": "normal_approximation_95_percent_across_seed_runs",
                "results": result_payload(results),
            },
            "jitai_metrics.json",
        )
        run.log_text(report, "jitai_evaluation.md")
        report_path = config.resolved_report_path()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")

    print(report, end="")
    print(f"MLflow run id: {run.run_id}")
    print(f"Report: {report_path}")
    return 0


__all__ = [
    "PROFILES",
    "ConfidenceInterval",
    "JITAIResults",
    "LoggedEvent",
    "OPESeedResult",
    "OnlineSeedResult",
    "SyntheticDataDigest",
    "mean_ci",
    "policy_config",
    "render_report",
    "result_metrics",
    "result_payload",
    "run_evaluation",
    "run_online_seed",
    "run_ope_seed",
]
