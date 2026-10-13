"""Metrics for the five-level safety problem, with intervals.

Everything here is a pure function of (truths, predictions). The two numbers the
brief asks for by name are the binary **HIGH+IMMINENT recall** (the outcome the
safety design protects) and the **abstention rate** (the share of messages where
the conformal set declined to name one level); the rest exist so those two cannot
be quoted without context.

Intervals are the same seeded percentile bootstrap the eval harness uses
(``evals/run_safety_eval.py``), so a number here and a number there were computed
the same way.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Any, Final

from manovia_ml.training.safety.taxonomy import COST_MATRIX, LEVELS, severity

__all__ = [
    "CRISIS_LEVELS",
    "accuracy",
    "binary_crisis_metrics",
    "bootstrap_ci",
    "confusion_matrix",
    "expected_cost",
    "macro_f1",
    "per_level_prf",
    "top_level",
]

#: The levels the crisis policy treats as "a helpline, not a conversation".
CRISIS_LEVELS: Final[frozenset[str]] = frozenset({"high", "imminent"})


def _normalise(label: str) -> str:
    return label.strip().lower()


def accuracy(truths: Sequence[str], predictions: Sequence[str]) -> float:
    if not truths:
        return 0.0
    hits = sum(
        1 for a, b in zip(truths, predictions, strict=True) if _normalise(a) == _normalise(b)
    )
    return hits / len(truths)


def confusion_matrix(
    truths: Sequence[str], predictions: Sequence[str], labels: Sequence[str] = LEVELS
) -> list[list[int]]:
    """Rows = truth, columns = prediction, both in ``labels`` order."""
    index = {label: position for position, label in enumerate(labels)}
    matrix = [[0 for _ in labels] for _ in labels]
    for truth, prediction in zip(truths, predictions, strict=True):
        matrix[index[_normalise(truth)]][index[_normalise(prediction)]] += 1
    return matrix


def per_level_prf(
    truths: Sequence[str], predictions: Sequence[str], labels: Sequence[str] = LEVELS
) -> dict[str, dict[str, float | None]]:
    """Precision, recall, F1 and support for every level."""
    out: dict[str, dict[str, float | None]] = {}
    for label in labels:
        tp = sum(
            1
            for t, p in zip(truths, predictions, strict=True)
            if _normalise(t) == label and _normalise(p) == label
        )
        fp = sum(
            1
            for t, p in zip(truths, predictions, strict=True)
            if _normalise(t) != label and _normalise(p) == label
        )
        fn = sum(
            1
            for t, p in zip(truths, predictions, strict=True)
            if _normalise(t) == label and _normalise(p) != label
        )
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and (precision + recall) > 0
            else None
        )
        out[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(tp + fn),
        }
    return out


def macro_f1(
    truths: Sequence[str], predictions: Sequence[str], labels: Sequence[str] = LEVELS
) -> float:
    scores = [
        value["f1"]
        for value in per_level_prf(truths, predictions, labels).values()
        if value["f1"] is not None
    ]
    return sum(scores) / len(scores) if scores else 0.0


def binary_crisis_metrics(
    truths: Sequence[str], predictions: Sequence[str]
) -> dict[str, float | None]:
    """The HIGH+IMMINENT view: precision, recall, F1 and the crisis support."""
    tp = fp = fn = 0
    for truth, prediction in zip(truths, predictions, strict=True):
        actual = _normalise(truth) in CRISIS_LEVELS
        predicted = _normalise(prediction) in CRISIS_LEVELS
        tp += int(actual and predicted)
        fp += int(predicted and not actual)
        fn += int(actual and not predicted)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positives": float(tp),
        "false_positives": float(fp),
        "false_negatives": float(fn),
        "crisis_support": float(tp + fn),
    }


def expected_cost(truths: Sequence[str], predictions: Sequence[str]) -> float:
    """Mean price of the mistakes actually made, under the documented matrix."""
    if not truths:
        return 0.0
    total = sum(
        COST_MATRIX[_normalise(truth)][_normalise(prediction)]
        for truth, prediction in zip(truths, predictions, strict=True)
    )
    return total / len(truths)


def top_level(probabilities: dict[str, float], *, cost_rule: bool = False) -> str:
    """The level to act on: the expected-cost argmin, or the plain argmax."""
    if not cost_rule:
        return max(LEVELS, key=lambda label: float(probabilities.get(label, 0.0)))
    from manovia_ml.training.safety.taxonomy import expected_cost_level

    return expected_cost_level(probabilities)


def bootstrap_ci(
    items: Sequence[Any],
    metric_fn: Callable[[Sequence[Any]], float | None],
    *,
    resamples: int = 2000,
    confidence_level: float = 0.95,
    seed: int = 20261013,
) -> tuple[float | None, float | None]:
    """Deterministic percentile bootstrap confidence interval over ``items``."""
    if not items or resamples <= 0:
        return None, None
    rng = random.Random(seed)
    n = len(items)
    estimates: list[float] = []
    for _ in range(resamples):
        sample = [items[rng.randrange(n)] for _ in range(n)]
        value = metric_fn(sample)
        if value is not None:
            estimates.append(value)
    if not estimates:
        return None, None
    estimates.sort()
    alpha = (1.0 - confidence_level) / 2.0
    low_index = max(0, min(len(estimates) - 1, int(alpha * len(estimates))))
    high_index = max(0, min(len(estimates) - 1, int((1.0 - alpha) * len(estimates)) - 1))
    return estimates[low_index], estimates[high_index]


def severity_distance(truth: str, prediction: str) -> int:
    """How many levels apart the two are — the shape the cost matrix encodes."""
    return abs(severity(truth) - severity(prediction))
