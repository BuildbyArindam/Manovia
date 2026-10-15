"""Strict, YAML-backed config for Day-36 synthetic mood forecasting runs."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from manovia_ml.config import ConfigError, StrictModel, TrackingConfig
from manovia_ml.paths import config_dir, resolve


class SyntheticDataConfig(StrictModel):
    """Generator inputs; these and the seed fully identify the panel."""

    users: int = Field(default=72, ge=12, le=2_000)
    days: int = Field(default=210, ge=90, le=2_000)
    start_date: str = "2025-01-06"
    logging_probability: float = Field(default=0.82, gt=0.4, le=1.0)

    @field_validator("start_date")
    @classmethod
    def _iso_start_date(cls, value: str) -> str:
        from datetime import date

        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("start_date must be an ISO YYYY-MM-DD date") from exc
        return value


class SplitConfig(StrictModel):
    """User-disjoint groups and a chronological cutoff within each group."""

    train_users_fraction: float = Field(default=0.6, gt=0.0, lt=0.8)
    calibration_users_fraction: float = Field(default=0.2, gt=0.0, lt=0.5)
    train_time_fraction: float = Field(default=0.62, gt=0.4, lt=0.8)
    calibration_time_fraction: float = Field(default=0.82, gt=0.6, lt=0.95)
    train_fit_fraction: float = Field(default=0.78, gt=0.5, lt=0.95)

    @model_validator(mode="after")
    def _valid_user_fractions(self) -> SplitConfig:
        if self.train_users_fraction + self.calibration_users_fraction >= 1.0:
            raise ValueError("train and calibration user fractions must leave held-out test users")
        return self


class SequenceModelConfig(StrictModel):
    """Small CPU/GPU-ready sequence-model settings."""

    lookback_days: int = Field(default=28, ge=28, le=28)
    horizon_days: int = Field(default=3, ge=3, le=3)
    hidden_size: int = Field(default=32, ge=8, le=256)
    transformer_heads: int = Field(default=4, ge=1, le=8)
    transformer_layers: int = Field(default=1, ge=1, le=4)
    dropout: float = Field(default=0.1, ge=0.0, lt=0.8)
    quantiles: tuple[float, float, float] = (0.1, 0.5, 0.9)
    kinds: tuple[Literal["gru", "lstm", "transformer"], ...] = (
        "gru",
        "lstm",
        "transformer",
    )

    @field_validator("quantiles")
    @classmethod
    def _fixed_quantile_levels(
        cls, value: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        if value != (0.1, 0.5, 0.9):
            raise ValueError("the Day-36 contract fixes quantiles at 0.1, 0.5 and 0.9")
        return value


class TrainingConfig(StrictModel):
    """Training and few-shot update parameters."""

    epochs: int = Field(default=8, ge=1, le=200)
    batch_size: int = Field(default=128, ge=8, le=512)
    learning_rate: float = Field(default=0.001, gt=0.0, le=0.1)
    weight_decay: float = Field(default=0.0001, ge=0.0, le=1.0)
    gradient_clip: float = Field(default=1.0, gt=0.0, le=10.0)
    require_torch: bool = True
    few_shot_steps: int = Field(default=30, ge=1, le=500)
    few_shot_learning_rate: float = Field(default=0.005, gt=0.0, le=0.1)
    few_shot_l2: float = Field(default=0.1, ge=0.0, le=100.0)
    few_shot_max_update_norm: float = Field(default=0.25, gt=0.0, le=10.0)
    few_shot_max_validation_loss_increase: float = Field(default=0.1, ge=0.0, le=10.0)
    few_shot_min_support: int = Field(default=3, ge=3, le=100)


class EvaluationConfig(StrictModel):
    """Frozen held-out evaluation, calibration and feature ablation settings."""

    nominal_coverage: float = Field(default=0.8, gt=0.5, lt=1.0)
    smoothing_alpha: float = Field(default=0.35, gt=0.0, le=1.0)
    run_ablation: bool = True
    ablation_groups: tuple[Literal["context", "day_of_week", "energy"], ...] = (
        "context",
        "day_of_week",
        "energy",
    )


class RunConfig(StrictModel):
    """Whether the run is a non-quotable smoke or a research evaluation."""

    phase: Literal["smoke", "evaluation"] = "evaluation"


class ReportConfig(StrictModel):
    """A text report contains metrics only; never a dataset or checkpoint."""

    output: str = "reports/day36-mood-forecasting.md"


class MoodForecastConfig(StrictModel):
    """The full immutable config logged with every Day-36 run."""

    name: str = Field(min_length=1)
    seed: int = Field(ge=0, lt=2**32)
    notes: str | None = None
    data: SyntheticDataConfig = SyntheticDataConfig()
    split: SplitConfig = SplitConfig()
    model: SequenceModelConfig = SequenceModelConfig()
    training: TrainingConfig = TrainingConfig()
    evaluation: EvaluationConfig = EvaluationConfig()
    run: RunConfig = RunConfig()
    report: ReportConfig = ReportConfig()
    tracking: TrackingConfig = TrackingConfig(experiment="manovia-mood-forecasting")
    threads: int | None = Field(default=1, ge=1)

    def resolved_report_path(self) -> Path:
        """Resolve the configured report relative to the ML project root."""
        return resolve(self.report.output)


def load_mood_forecast_config(path: str | Path) -> MoodForecastConfig:
    """Load YAML and reject unknown keys, malformed files and missing configs."""
    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.is_file():
        candidate = config_dir() / candidate
    if not candidate.is_file():
        raise ConfigError(f"mood-forecast config not found: {path} (looked at {candidate})")
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{candidate}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{candidate}: config must be a mapping at the top level")
    try:
        config = MoodForecastConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"{candidate}: invalid mood-forecast config\n{exc}") from exc
    if config.model.hidden_size % config.model.transformer_heads != 0:
        raise ConfigError("model.hidden_size must be divisible by model.transformer_heads")
    if config.split.calibration_time_fraction <= config.split.train_time_fraction:
        raise ConfigError(
            "split.calibration_time_fraction must be greater than split.train_time_fraction"
        )
    return config


__all__ = ["MoodForecastConfig", "load_mood_forecast_config"]
