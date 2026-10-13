"""Metrics, defined once, so a number in two places is the same number.

The evaluation CLI and the tests must compute accuracy and macro-F1 the same
way, and a change to either must be visible in one diff. That is why this module
exists instead of four inline calls to ``sklearn.metrics``.

Zero-division is handled explicitly: ``zero_division=0`` (a class with no
predictions scores 0, not a warning and not a crash). With six classes and 26
eval rows that path is reachable, and "it printed a warning and moved on" is not
an acceptable behaviour for a metric that gates a decision.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Literal

from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    log_loss,
    precision_recall_fscore_support,
)

__all__ = [
    "METRIC_NAMES",
    "MetricName",
    "classification_metrics",
    "confusion",
    "majority_baseline_accuracy",
    "per_class_f1",
]

MetricName = Literal["accuracy", "macro_f1", "weighted_f1", "log_loss"]

METRIC_NAMES: tuple[MetricName, ...] = ("accuracy", "macro_f1", "weighted_f1")

#: log_loss needs predicted probabilities, so it is opt-in per config.
PROBABILITY_METRICS: tuple[MetricName, ...] = ("log_loss",)


def classification_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    *,
    labels: Sequence[str],
    metrics: Sequence[MetricName] = METRIC_NAMES,
    probabilities: Sequence[Sequence[float]] | None = None,
) -> dict[str, float]:
    """Accuracy, F1 variants and (opt-in) log loss, keyed by config metric names.

    `probabilities` (when given) must have one column per entry of `labels`, in
    that order. log_loss is computed against the lexicographic order, because
    that is what scikit-learn expects and warns about otherwise — so the columns
    are reordered here rather than trusted.
    """

    if len(y_true) != len(y_pred):
        raise ValueError(f"y_true has {len(y_true)} rows, y_pred has {len(y_pred)}")
    if not y_true:
        raise ValueError("no rows to score")

    needs_probabilities = [name for name in metrics if name in PROBABILITY_METRICS]
    if needs_probabilities and probabilities is None:
        raise ValueError(f"{needs_probabilities} need predicted probabilities")

    values: dict[str, float] = {}
    if "accuracy" in metrics:
        values["accuracy"] = float(accuracy_score(y_true, y_pred))
    for name, average in (("macro_f1", "macro"), ("weighted_f1", "weighted")):
        if name not in metrics:
            continue
        _, _, f1, _ = precision_recall_fscore_support(
            y_true, y_pred, labels=list(labels), average=average, zero_division=0
        )
        values[name] = float(f1)
    if "log_loss" in metrics and probabilities is not None:
        # log_loss is the continuous half of the picture: with a small eval split
        # accuracy moves in 1/n jumps and can be identical across two seeds that
        # produced different models. The log loss always moves.
        order = sorted(labels)
        columns = [list(labels).index(label) for label in order]
        reordered = [[row[column] for column in columns] for row in probabilities]
        values["log_loss"] = float(log_loss(y_true, reordered, labels=order))
    return values


def majority_baseline_accuracy(y_true: Sequence[str]) -> float:
    """Accuracy of "always predict the most common label in this eval set".

    Logged as ``majority_accuracy`` next to every run's accuracy, because on
    small splits accuracy alone is unreadable. The toy smoke slice is 4 joy + 4
    anger: a constant classifier scores 0.500 there, so a run that says 0.375 has
    not learned the task and nobody should read it as a step forward. On a real
    imbalanced corpus the same number is the floor a model has to clear before
    any other discussion ("92 % accuracy" on a 92 %-negative set is one number
    pretending to be two).

    It is a property of the *data*, not of the model, so it is not part of a
    config's metric list and cannot be switched off.
    """

    if not y_true:
        raise ValueError("no rows to score")
    return float(max(Counter(y_true).values()) / len(y_true))


def per_class_f1(
    y_true: Sequence[str], y_pred: Sequence[str], *, labels: Sequence[str]
) -> dict[str, float]:
    _, _, f1, support = precision_recall_fscore_support(
        y_true, y_pred, labels=list(labels), average=None, zero_division=0
    )
    return {label: float(value) for label, value in zip(labels, f1, strict=True)} | {
        f"support.{label}": float(count) for label, count in zip(labels, support, strict=True)
    }


def confusion(
    y_true: Sequence[str], y_pred: Sequence[str], *, labels: Sequence[str]
) -> list[list[int]]:
    """Row = truth, column = prediction, in the given label order."""

    matrix = confusion_matrix(y_true, y_pred, labels=list(labels))
    return [[int(value) for value in row] for row in matrix]
