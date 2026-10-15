"""Masked quantile metrics and held-out split conformal interval expansion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, cast

import numpy as np

from manovia_ml.mood_forecast.data import HORIZON_DAYS, WindowSample

QUANTILE_LEVELS: Final[tuple[float, float, float]] = (0.1, 0.5, 0.9)


@dataclass(frozen=True)
class IntervalCalibrator:
    """Non-negative, horizon-specific conformal expansion in mood points."""

    nominal_coverage: float
    corrections: tuple[float, float, float]
    calibration_counts: tuple[int, int, int]


def fit_interval_calibrator(
    samples: tuple[WindowSample, ...] | list[WindowSample],
    predictions: np.ndarray,
    *,
    nominal_coverage: float,
) -> IntervalCalibrator:
    """Fit split-conformal corrections using calibration rows only.

    Scores are ``max(lower - y, y - upper, 0)``. The standard split-conformal
    order-statistic rank ``ceil((n + 1) * coverage)`` expands both sides of the
    predicted interval. Calibration users are disjoint from train and test users;
    test labels are never used to choose the correction. Callers must still assess
    exchangeability at the score unit before claiming formal coverage: overlapping
    rolling-origin windows are dependent, so this experiment makes no such claim.
    """
    _validate_prediction_shape(samples, predictions)
    if not 0.5 < nominal_coverage < 1.0:
        raise ValueError("nominal_coverage must be in (0.5, 1.0)")
    corrections: list[float] = []
    counts: list[int] = []
    for horizon in range(HORIZON_DAYS):
        scores: list[float] = []
        for row, sample in enumerate(samples):
            if not sample.target_mask[horizon]:
                continue
            lower = float(predictions[row, horizon, 0])
            upper = float(predictions[row, horizon, 2])
            actual = float(sample.targets[horizon])
            scores.append(max(lower - actual, actual - upper, 0.0))
        if not scores:
            raise ValueError(f"no observed calibration outcomes at horizon {horizon + 1}")
        ordered = np.sort(np.asarray(scores, dtype=np.float64))
        rank = min(len(ordered), math.ceil((len(ordered) + 1) * nominal_coverage))
        corrections.append(float(ordered[rank - 1]))
        counts.append(len(scores))
    return IntervalCalibrator(
        nominal_coverage=nominal_coverage,
        corrections=cast(tuple[float, float, float], tuple(corrections)),
        calibration_counts=cast(tuple[int, int, int], tuple(counts)),
    )


def apply_interval_calibrator(
    predictions: np.ndarray, calibrator: IntervalCalibrator
) -> np.ndarray:
    """Expand only the lower/upper quantiles; keep the median unchanged."""
    if predictions.ndim != 3 or predictions.shape[1:] != (HORIZON_DAYS, 3):
        raise ValueError(f"expected (n, {HORIZON_DAYS}, 3) predictions, got {predictions.shape}")
    calibrated = np.array(predictions, dtype=np.float64, copy=True)
    for horizon, correction in enumerate(calibrator.corrections):
        calibrated[:, horizon, 0] = np.maximum(1.0, calibrated[:, horizon, 0] - correction)
        calibrated[:, horizon, 2] = np.minimum(5.0, calibrated[:, horizon, 2] + correction)
    return cast(np.ndarray, calibrated)


def pinball_loss(actual: np.ndarray, predicted: np.ndarray, quantile: float) -> float:
    """Mean pinball loss, with non-finite values rejected rather than hidden."""
    error = np.asarray(actual, dtype=np.float64) - np.asarray(predicted, dtype=np.float64)
    if not np.isfinite(error).all():
        raise ValueError("pinball loss received a non-finite value")
    return float(np.mean(np.maximum(quantile * error, (quantile - 1.0) * error)))


def evaluate_predictions(
    samples: tuple[WindowSample, ...] | list[WindowSample],
    predictions: np.ndarray,
    *,
    nominal_coverage: float,
) -> dict[str, float]:
    """Summarise masked MAE, seasonal MASE, interval coverage/width and pinball."""
    _validate_prediction_shape(samples, predictions)
    if not samples:
        raise ValueError("cannot evaluate an empty sample set")
    point = predictions[:, :, 1]
    actual = np.stack([sample.targets for sample in samples]).astype(np.float64)
    mask = np.stack([sample.target_mask for sample in samples]).astype(bool)
    scale = np.asarray([sample.mase_scale for sample in samples], dtype=np.float64)[:, None]
    errors = np.abs(point - actual)
    absolute = errors[mask]
    if not len(absolute):
        raise ValueError("evaluation split has no observed outcomes")
    mae = float(np.mean(absolute))
    mase = float(np.mean((errors / scale)[mask]))
    lower = predictions[:, :, 0]
    upper = predictions[:, :, 2]
    covered = (actual >= lower) & (actual <= upper) & mask
    width = (upper - lower)[mask]
    levels = QUANTILE_LEVELS
    pinballs: list[float] = []
    for quantile_index, quantile in enumerate(levels):
        quantile_errors = actual - predictions[:, :, quantile_index]
        pinballs.extend(
            np.maximum(quantile * quantile_errors, (quantile - 1.0) * quantile_errors)[
                mask
            ].tolist()
        )
    result: dict[str, float] = {
        "mae": mae,
        "mase_vs_seasonal_naive": mase,
        "interval_coverage": float(np.mean(covered[mask])),
        "interval_width": float(np.mean(width)),
        "pinball_loss": float(np.mean(pinballs)),
        "nominal_coverage": nominal_coverage,
        "observed_targets": float(mask.sum()),
    }
    for horizon in range(HORIZON_DAYS):
        horizon_mask = mask[:, horizon]
        if not horizon_mask.any():
            continue
        result[f"horizon_{horizon + 1}_mae"] = float(np.mean(errors[horizon_mask, horizon]))
        result[f"horizon_{horizon + 1}_coverage"] = float(np.mean(covered[horizon_mask, horizon]))
        result[f"horizon_{horizon + 1}_width"] = float(
            np.mean((upper - lower)[horizon_mask, horizon])
        )
        horizon_pinball: list[float] = []
        for quantile_index, quantile in enumerate(levels):
            quantile_errors = actual[:, horizon] - predictions[:, horizon, quantile_index]
            horizon_pinball.extend(
                np.maximum(
                    quantile * quantile_errors[horizon_mask],
                    (quantile - 1.0) * quantile_errors[horizon_mask],
                ).tolist()
            )
        result[f"horizon_{horizon + 1}_pinball_loss"] = float(np.mean(horizon_pinball))
    return result


def _validate_prediction_shape(
    samples: tuple[WindowSample, ...] | list[WindowSample], predictions: np.ndarray
) -> None:
    expected = (len(samples), HORIZON_DAYS, 3)
    if predictions.shape != expected:
        raise ValueError(f"expected prediction shape {expected}, got {predictions.shape}")
    if not np.isfinite(predictions).all():
        raise ValueError("predictions contain a non-finite value")
    if np.any(predictions[:, :, 0] > predictions[:, :, 1]) or np.any(
        predictions[:, :, 1] > predictions[:, :, 2]
    ):
        raise ValueError("predicted quantiles must be ordered 10 <= 50 <= 90")


__all__ = [
    "IntervalCalibrator",
    "apply_interval_calibrator",
    "evaluate_predictions",
    "fit_interval_calibrator",
    "pinball_loss",
]
