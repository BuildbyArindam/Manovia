"""Strict experiment configuration; all training knobs live in YAML."""

from __future__ import annotations

from pydantic import Field, model_validator

from manovia_ml.config import StrictModel, TrackingConfig


class FederatedConfig(StrictModel):
    name: str = "day38-federated-smoke"
    seed: int = Field(default=38, ge=0, lt=2**32)
    clients: int = Field(default=50, ge=3)
    days: int = Field(default=40, ge=35)
    rounds: int = Field(default=4, ge=1)
    local_steps: int = Field(default=2, ge=1)
    learning_rate: float = Field(default=0.05, gt=0)
    hidden: int = Field(default=8, ge=1)
    proximal_mu: float = Field(default=0.1, gt=0)
    dropout: tuple[float, ...] = (0.0, 0.3)
    noise: tuple[float, ...] = (0.8, 1.6, 3.2)
    clipping: tuple[float, ...] = (0.5, 1.0)
    delta: float = Field(default=1e-5, gt=0, lt=1)
    batch_size: int = Field(default=64, ge=1)
    test_fraction: float = Field(default=0.3, gt=0, lt=1)
    tolerance: float = Field(default=1e-6, gt=0)
    report_output: str = "reports/day38-federated.md"
    tracking: TrackingConfig = TrackingConfig(experiment="manovia-federated")

    @model_validator(mode="after")
    def valid_sweeps(self) -> FederatedConfig:
        if not self.noise or not self.clipping or not self.dropout:
            raise ValueError("sweeps must not be empty")
        if any(v <= 0 for v in (*self.noise, *self.clipping)):
            raise ValueError("noise and clipping must be positive")
        if any(not 0 <= v < 1 for v in self.dropout):
            raise ValueError("dropout must be in [0, 1)")
        return self
