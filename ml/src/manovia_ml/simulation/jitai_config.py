"""Strict, YAML-backed configuration for the Day-37 JITAI simulation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError, model_validator

from manovia_ml.config import ConfigError, StrictModel, TrackingConfig
from manovia_ml.paths import config_dir, resolve


class BanditRunConfig(StrictModel):
    """Hyperparameters shared with the backend's tested policy implementation."""

    epsilon: float = Field(default=0.2, ge=0.0, le=1.0)
    linucb_alpha: float = Field(default=0.35, ge=0.0, le=10.0)
    thompson_draws: int = Field(default=48, ge=16, le=512)
    population_prior_precision: float = Field(default=1.0, gt=0.0, le=100.0)
    user_prior_precision: float = Field(default=2.0, gt=0.0, le=100.0)
    exploration_cap: float = Field(default=0.2, ge=0.0, le=0.2)
    abandonment_penalty: float = Field(default=0.1, ge=0.0, le=0.2)


class OnlineSimulationConfig(StrictModel):
    """Synthetic online workload; all users and outcomes are generated in memory."""

    users_per_group: int = Field(default=3, ge=1, le=100)
    steps_per_user: int = Field(default=60, ge=10, le=2_000)
    reward_noise_std: float = Field(default=0.16, ge=0.0, le=1.0)
    abandonment_probability: float = Field(default=0.04, ge=0.0, le=0.5)
    context_jitter: int = Field(default=1, ge=0, le=2)


class OfflineEvaluationConfig(StrictModel):
    """Logged-data OPE workload with disjoint train and evaluation episodes."""

    users_per_group: int = Field(default=4, ge=1, le=100)
    train_events_per_user: int = Field(default=80, ge=10, le=5_000)
    evaluation_events_per_user: int = Field(default=80, ge=10, le=5_000)
    behavior_policy: Literal["uniform"] = "uniform"


class JITAIConfig(StrictModel):
    """Full resolved simulation config; unknown YAML keys are errors."""

    name: str = Field(min_length=1)
    seed: int = Field(ge=0, lt=2**32)
    seeds: int = Field(default=20, ge=20, le=200)
    algorithms: tuple[Literal["random", "epsilon_greedy", "linucb", "thompson_sampling"], ...] = (
        "random",
        "epsilon_greedy",
        "linucb",
        "thompson_sampling",
    )
    bandit: BanditRunConfig = BanditRunConfig()
    online: OnlineSimulationConfig = OnlineSimulationConfig()
    offline: OfflineEvaluationConfig = OfflineEvaluationConfig()
    fairness_gap_flag: float = Field(default=0.15, ge=0.0, le=1.0)
    report_output: str = "reports/day37-jitai.md"
    tracking: TrackingConfig = TrackingConfig(experiment="manovia-jitai")

    @model_validator(mode="after")
    def _unique_algorithms(self) -> JITAIConfig:
        if len(self.algorithms) != len(set(self.algorithms)):
            raise ValueError("algorithms must be unique")
        required = {"random", "epsilon_greedy", "linucb", "thompson_sampling"}
        if set(self.algorithms) != required:
            raise ValueError(
                "the verification run must include random and all three bandit policies"
            )
        return self

    def resolved_report_path(self) -> Path:
        return resolve(self.report_output)


def load_jitai_config(path: str | Path = "jitai.yaml") -> JITAIConfig:
    """Load a config from the ML project config directory and reject unknown keys."""

    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.is_file():
        candidate = config_dir() / candidate
    if not candidate.is_file():
        raise ConfigError(f"JITAI config not found: {path} (looked at {candidate})")
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{candidate}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("JITAI config must be a mapping at the top level")
    try:
        return JITAIConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{candidate}: invalid JITAI config\n{exc}") from exc


__all__ = ["JITAIConfig", "load_jitai_config"]
