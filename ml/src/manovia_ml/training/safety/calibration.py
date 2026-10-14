"""Temperature scaling, ECE and the reliability diagram.

A five-level classifier whose probabilities are *ordered* is enough for the
ensemble's raise bands; a classifier whose probabilities are *calibrated* is
enough for the abstention policy to mean anything. ``P(high) = 0.7`` has to be
read as "seven times in ten", otherwise a coverage guarantee computed on top of
it is a guarantee about a number nobody can interpret.

So: **one parameter, fitted on the calibration split, and never on the test
split.** Temperature scaling (Guo et al. 2017) divides every logit by a single
``T``. It is the smallest possible correction, it cannot change the argmax (so it
cannot change the point prediction), and it is exactly what a reviewer can audit
by hand: "T = 1.83 means the raw logits were about 1.8x too confident".

The module also produces the two artefacts the brief asks for: **ECE** before and
after, and the **reliability diagram data** (per-bin accuracy vs confidence) that
goes into the report.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from manovia_ml.training.safety.taxonomy import LEVEL_INDEX, LEVELS

__all__ = [
    "DEFAULT_BINS",
    "CalibrationResult",
    "ReliabilityBin",
    "apply_temperature",
    "calibrate",
    "expected_calibration_error",
    "fit_temperature",
    "reliability_diagram",
    "softmax",
]

#: Number of equal-width confidence bins for the reliability diagram.
DEFAULT_BINS: Final[int] = 10


def softmax(logits: Sequence[float]) -> list[float]:
    """Numerically stable softmax over one row of logits."""
    if not logits:
        return []
    peak = max(logits)
    exponentials = [math.exp(value - peak) for value in logits]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def apply_temperature(logits: Sequence[float], temperature: float) -> list[float]:
    """Divide the logits by ``temperature`` and softmax. ``T > 1`` softens."""
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    return softmax([value / temperature for value in logits])


def _log_probs(logits: Sequence[float]) -> list[float]:
    probabilities = softmax(logits)
    return [math.log(max(value, 1e-12)) for value in probabilities]


def _negative_log_likelihood(logits: Sequence[Sequence[float]], labels: Sequence[int]) -> float:
    total = 0.0
    for row, label in zip(logits, labels, strict=True):
        total -= _log_probs(row)[label]
    return total / max(1, len(labels))


def fit_temperature(
    logits: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    lower: float = 0.05,
    upper: float = 10.0,
    iterations: int = 80,
) -> float:
    """The temperature minimising NLL on the calibration split.

    Golden-section search on ``log T`` over ``[lower, upper]``: one-dimensional,
    derivative-free, deterministic, and no scipy dependency. Searching in log
    space keeps ``T`` positive by construction, so a wandering search cannot
    produce a sign flip (which would invert the ranking and quietly destroy
    calibration while making NLL look like it improved).
    """
    if not logits or len(logits) != len(labels):
        raise ValueError("logits and labels must be non-empty and the same length")
    if any(label < 0 or label >= len(LEVELS) for label in labels):
        raise ValueError(f"labels must index into {LEVELS}")

    def nll(log_temperature: float) -> float:
        temperature = math.exp(log_temperature)
        scaled = [[value / temperature for value in row] for row in logits]
        return _negative_log_likelihood(scaled, labels)

    low, high = math.log(lower), math.log(upper)
    ratio = 0.6180339887498949
    x1 = high - ratio * (high - low)
    x2 = low + ratio * (high - low)
    f1, f2 = nll(x1), nll(x2)
    for _ in range(iterations):
        if f1 > f2:
            low, x1, f1 = x1, x2, f2
            x2 = low + ratio * (high - low)
            f2 = nll(x2)
        else:
            high, x2, f2 = x2, x1, f1
            x1 = high - ratio * (high - low)
            f1 = nll(x1)
        if abs(high - low) < 1e-7:
            break
    return round(math.exp((low + high) / 2), 6)


@dataclass(frozen=True)
class ReliabilityBin:
    """One row of the reliability diagram: what the model claimed vs what happened."""

    lower: float
    upper: float
    count: int
    accuracy: float
    confidence: float

    @property
    def gap(self) -> float:
        """Confidence minus accuracy. Positive means over-confident."""
        return self.confidence - self.accuracy

    def as_dict(self) -> dict[str, Any]:
        return {
            "bin": f"[{self.lower:.1f}, {self.upper:.1f})",
            "count": self.count,
            "accuracy": round(self.accuracy, 4),
            "confidence": round(self.confidence, 4),
            "gap": round(self.gap, 4),
        }


def _argmax(row: Mapping[str, float]) -> tuple[str, float]:
    best_label = LEVELS[0]
    best_value = -1.0
    for label in LEVELS:
        value = float(row.get(label, 0.0))
        if value > best_value:
            best_value, best_label = value, label
    return best_label, best_value


def reliability_diagram(
    probabilities: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    *,
    bins: int = DEFAULT_BINS,
) -> list[ReliabilityBin]:
    """Bin the top-class confidence and compare it with how often it was right."""
    if bins < 2:
        raise ValueError("bins must be at least 2")
    if len(probabilities) != len(labels):
        raise ValueError("probabilities and labels must be the same length")
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for row, label in zip(probabilities, labels, strict=True):
        best_label, confidence = _argmax(row)
        index = min(bins - 1, int(confidence * bins))
        buckets[index].append((confidence, best_label == label.strip().lower()))

    out: list[ReliabilityBin] = []
    for index, bucket in enumerate(buckets):
        lower, upper = index / bins, (index + 1) / bins
        if not bucket:
            out.append(
                ReliabilityBin(lower=lower, upper=upper, count=0, accuracy=0.0, confidence=0.0)
            )
            continue
        out.append(
            ReliabilityBin(
                lower=lower,
                upper=upper,
                count=len(bucket),
                accuracy=sum(1 for _, correct in bucket if correct) / len(bucket),
                confidence=sum(value for value, _ in bucket) / len(bucket),
            )
        )
    return out


def expected_calibration_error(
    probabilities: Sequence[Mapping[str, float]],
    labels: Sequence[str],
    *,
    bins: int = DEFAULT_BINS,
) -> float:
    """ECE: the sample-weighted mean |confidence - accuracy| across the bins."""
    diagram = reliability_diagram(probabilities, labels, bins=bins)
    total = sum(item.count for item in diagram)
    if not total:
        return 0.0
    return sum(item.count * abs(item.gap) for item in diagram) / total


@dataclass(frozen=True)
class CalibrationResult:
    """What the calibration step fitted and what it did to the numbers."""

    temperature: float
    ece_before: float
    ece_after: float
    nll_before: float
    nll_after: float
    bins: tuple[ReliabilityBin, ...]
    split: str = "calibration"

    def as_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "temperature": self.temperature,
            "ece_before": round(self.ece_before, 6),
            "ece_after": round(self.ece_after, 6),
            "nll_before": round(self.nll_before, 6),
            "nll_after": round(self.nll_after, 6),
            "reliability": [item.as_dict() for item in self.bins],
        }


def calibrate(
    logits: Sequence[Sequence[float]],
    labels: Sequence[str],
    *,
    bins: int = DEFAULT_BINS,
    split: str = "calibration",
) -> CalibrationResult:
    """Fit the temperature and report before/after ECE, NLL and the diagram.

    ``labels`` are the level *names*; ``logits`` rows follow ``LEVELS`` order.
    """
    indices = [LEVEL_INDEX[label.strip().lower()] for label in labels]
    temperature = fit_temperature(logits, indices)
    before = [dict(zip(LEVELS, softmax(row), strict=True)) for row in logits]
    after = [dict(zip(LEVELS, apply_temperature(row, temperature), strict=True)) for row in logits]
    return CalibrationResult(
        temperature=temperature,
        ece_before=expected_calibration_error(before, labels, bins=bins),
        ece_after=expected_calibration_error(after, labels, bins=bins),
        nll_before=_negative_log_likelihood(logits, indices),
        nll_after=_negative_log_likelihood(
            [[value / temperature for value in row] for row in logits], indices
        ),
        bins=tuple(reliability_diagram(after, labels, bins=bins)),
        split=split,
    )
