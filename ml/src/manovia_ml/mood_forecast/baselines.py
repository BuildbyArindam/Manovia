"""Four transparent daily-valence baselines for the synthetic benchmark."""

from __future__ import annotations

from datetime import date
from typing import Final, Literal, cast

import numpy as np

from manovia_ml.mood_forecast.data import FEATURE_INDEX, HORIZON_DAYS, WindowSample

BaselineName = Literal[
    "persistence", "moving_average_7d", "seasonal_naive", "exponential_smoothing"
]
BASELINE_NAMES: Final[tuple[BaselineName, ...]] = (
    "persistence",
    "moving_average_7d",
    "seasonal_naive",
    "exponential_smoothing",
)


def _observed_history(sample: WindowSample) -> tuple[np.ndarray, np.ndarray]:
    values = 3.0 + 2.0 * sample.features[:, FEATURE_INDEX["valence"]]
    observed = sample.features[:, FEATURE_INDEX["observed"]] > 0.5
    return values, observed


def _last_observed(values: np.ndarray, observed: np.ndarray) -> float:
    positions = np.flatnonzero(observed)
    return float(values[positions[-1]]) if len(positions) else 3.0


def predict_baseline(
    sample: WindowSample,
    kind: BaselineName,
    *,
    smoothing_alpha: float = 0.35,
) -> np.ndarray:
    """Return three point forecasts in original 1--5 valence units."""
    values, observed = _observed_history(sample)
    fallback = _last_observed(values, observed)
    if kind == "persistence":
        predictions: np.ndarray = np.full(HORIZON_DAYS, fallback, dtype=np.float64)
    elif kind == "moving_average_7d":
        recent = observed[-7:]
        mean = float(np.mean(values[-7:][recent])) if recent.any() else fallback
        predictions = np.full(HORIZON_DAYS, mean, dtype=np.float64)
    elif kind == "seasonal_naive":
        weekdays = sample.features[
            :, FEATURE_INDEX["day_of_week_0"] : FEATURE_INDEX["day_of_week_0"] + 7
        ]
        origin_weekday = int(np.argmax(weekdays[-1]))
        forecasts: list[float] = []
        for horizon in range(1, HORIZON_DAYS + 1):
            target_weekday = (origin_weekday + horizon) % 7
            candidates = np.flatnonzero(observed & (np.argmax(weekdays, axis=1) == target_weekday))
            if len(candidates):
                forecasts.append(float(values[candidates[-1]]))
            else:
                forecasts.append(fallback)
        predictions = np.asarray(forecasts, dtype=np.float64)
    elif kind == "exponential_smoothing":
        if not 0.0 < smoothing_alpha <= 1.0:
            raise ValueError("smoothing_alpha must be in (0, 1]")
        smoothed = fallback
        started = False
        for value, present in zip(values, observed, strict=True):
            if present:
                smoothed = (
                    float(value)
                    if not started
                    else (smoothing_alpha * float(value) + (1.0 - smoothing_alpha) * smoothed)
                )
                started = True
        predictions = np.full(HORIZON_DAYS, smoothed, dtype=np.float64)
    else:  # pragma: no cover - the Literal and config schema protect this branch
        raise ValueError(f"unknown baseline {kind!r}")
    return cast(np.ndarray, np.clip(predictions, 1.0, 5.0))


def baseline_quantiles(point_forecast: np.ndarray) -> np.ndarray:
    """Represent point baselines as degenerate 10/50/90 forecasts pre-calibration."""
    if point_forecast.shape != (HORIZON_DAYS,):
        raise ValueError(f"expected {HORIZON_DAYS} point forecasts, got {point_forecast.shape}")
    return cast(np.ndarray, np.repeat(point_forecast[:, None], repeats=3, axis=1))


def target_weekdays(sample: WindowSample) -> tuple[int, ...]:
    """Day-of-week for each forecast horizon (used only in diagnostics)."""
    return tuple(date.fromisoformat(value).weekday() for value in sample.target_days)


__all__ = [
    "BASELINE_NAMES",
    "BaselineName",
    "baseline_quantiles",
    "predict_baseline",
    "target_weekdays",
]
