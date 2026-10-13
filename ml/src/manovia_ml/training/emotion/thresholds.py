"""Per-label decision thresholds, tuned on the validation split.

A single 0.5 cut is the wrong operating point for almost every label of a
multi-label head trained with class weighting: rare labels (`loneliness`,
`shame`) are pushed *up* by a balanced loss, so their sigmoid sits high on rows
that are not about them, while `neutral` — the residual class — ends up
under-predicted. The fix is one threshold per label, chosen on the validation
split, and the split matters: a threshold fitted on the test rows measures the
fitting, not the model.

The choice criterion is F1 per label by default. ``balanced_accuracy`` is
available for the case where a label's negatives dominate so hard that F1
rewards a conservative threshold. Either way:

* a label with fewer than ``thresholds.min_support`` positives in the validation
  split keeps the default — one positive cannot support a fitted threshold, and
  pretending otherwise is how a threshold lands at 0.05 for a label nobody
  checked;
* the grid is fixed and printed in the report, so the tuned value can be
  reproduced by hand;
* ties go to the threshold closest to the default, so the tuning is a nudge
  rather than a coin toss.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from manovia_ml.config import ThresholdConfig
from manovia_ml.training.emotion.taxonomy import EMOTIONS

__all__ = [
    "LabelThreshold",
    "ThresholdTuning",
    "apply_thresholds",
    "grid_values",
    "tune_thresholds",
]


@dataclass(frozen=True)
class LabelThreshold:
    """What the search did for one label."""

    label: str
    threshold: float
    support: int
    tuned: bool
    metric_at_threshold: float
    metric_at_default: float
    reason: str

    def as_dict(self) -> dict[str, float | int | str | bool]:
        return {
            "threshold": round(self.threshold, 4),
            "support": self.support,
            "tuned": self.tuned,
            "metric_at_threshold": round(self.metric_at_threshold, 6),
            "metric_at_default": round(self.metric_at_default, 6),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ThresholdTuning:
    """The full result: the operating point plus the evidence for it."""

    labels: tuple[str, ...]
    thresholds: dict[str, float]
    per_label: dict[str, LabelThreshold]
    split: str
    metric: str
    grid: tuple[float, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "split": self.split,
            "metric": self.metric,
            "grid": [round(value, 4) for value in self.grid],
            "labels": list(self.labels),
            "thresholds": {label: round(value, 4) for label, value in self.thresholds.items()},
            "per_label": {label: item.as_dict() for label, item in self.per_label.items()},
            "note": (
                "Tuned on the validation split only. The test split is scored with these "
                "values and is never used to choose them."
            ),
        }


def grid_values(config: ThresholdConfig) -> tuple[float, ...]:
    """The search grid: ``search_steps`` points from min to max, inclusive."""

    if config.search_steps == 1:
        return (round(config.search_min, 6),)
    span = config.search_max - config.search_min
    return tuple(
        round(config.search_min + span * index / (config.search_steps - 1), 6)
        for index in range(config.search_steps)
    )


def _metrics_at(
    probabilities: Sequence[float],
    truths: Sequence[bool],
    threshold: float,
) -> tuple[float, float]:
    """``(f1, balanced_accuracy)`` for one label at one threshold."""

    tp = fp = fn = tn = 0
    for probability, truth in zip(probabilities, truths, strict=True):
        predicted = probability >= threshold
        if predicted and truth:
            tp += 1
        elif predicted:
            fp += 1
        elif truth:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    sensitivity = recall
    specificity = tn / (tn + fp) if tn + fp else 0.0
    return f1, (sensitivity + specificity) / 2.0


def tune_thresholds(
    probabilities: Mapping[str, Sequence[float]],
    truths: Mapping[str, Sequence[bool]],
    *,
    config: ThresholdConfig,
    labels: Sequence[str] = EMOTIONS,
    split: str = "validation",
) -> ThresholdTuning:
    """Choose one threshold per label by scanning the configured grid."""

    grid = grid_values(config)
    thresholds: dict[str, float] = {}
    per_label: dict[str, LabelThreshold] = {}

    for label in labels:
        column = [float(value) for value in probabilities.get(label, ())]
        truth_column = [bool(value) for value in truths.get(label, ())]
        if len(column) != len(truth_column):
            raise ValueError(f"{label}: {len(column)} probabilities vs {len(truth_column)} labels")
        support = sum(1 for value in truth_column if value)
        if not column:
            thresholds[label] = config.default
            per_label[label] = LabelThreshold(
                label=label,
                threshold=config.default,
                support=0,
                tuned=False,
                metric_at_threshold=0.0,
                metric_at_default=0.0,
                reason="no validation rows for this label",
            )
            continue
        if support < config.min_support:
            f1_default, ba_default = _metrics_at(column, truth_column, config.default)
            metric_default = f1_default if config.metric == "f1" else ba_default
            thresholds[label] = config.default
            per_label[label] = LabelThreshold(
                label=label,
                threshold=config.default,
                support=support,
                tuned=False,
                metric_at_threshold=metric_default,
                metric_at_default=metric_default,
                reason=(
                    f"only {support} positive row(s) in {split}; min_support is "
                    f"{config.min_support}, so the default stands"
                ),
            )
            continue

        best_threshold = config.default
        best_score = -1.0
        for threshold in grid:
            f1, balanced = _metrics_at(column, truth_column, threshold)
            score = f1 if config.metric == "f1" else balanced
            # `>` plus a tie-break toward the default keeps the search a nudge.
            if score > best_score + 1e-12 or (
                abs(score - best_score) <= 1e-12
                and abs(threshold - config.default) < abs(best_threshold - config.default)
            ):
                best_threshold, best_score = threshold, score
        f1_default, ba_default = _metrics_at(column, truth_column, config.default)
        metric_default = f1_default if config.metric == "f1" else ba_default
        thresholds[label] = best_threshold
        per_label[label] = LabelThreshold(
            label=label,
            threshold=best_threshold,
            support=support,
            tuned=True,
            metric_at_threshold=best_score,
            metric_at_default=metric_default,
            reason=(
                f"{config.metric} on {split}: {best_score:.4f} "
                f"vs {metric_default:.4f} at the default"
            ),
        )

    return ThresholdTuning(
        labels=tuple(labels),
        thresholds=thresholds,
        per_label=per_label,
        split=split,
        metric=config.metric,
        grid=grid,
    )


def apply_thresholds(
    probabilities: Sequence[float],
    labels: Sequence[str],
    thresholds: Mapping[str, float],
) -> frozenset[str]:
    """One row's label set at the given thresholds."""

    return frozenset(
        label
        for index, label in enumerate(labels)
        if float(probabilities[index]) >= float(thresholds.get(label, 0.5))
    )
