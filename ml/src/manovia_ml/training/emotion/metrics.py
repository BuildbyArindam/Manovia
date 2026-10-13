"""Multi-label metrics, calibration and bootstrap intervals — no frameworks.

Written rather than imported because three of the four things the Day-32 report
needs are not in scikit-learn's multi-label toolbox:

* **per-language macro-F1** (scikit-learn has no notion of a grouping column);
* **ECE for a multi-label head** (``calibration_curve`` is binary-classification
  shaped and would silently average away the per-label structure);
* **bootstrap intervals over rows**, so that a resample of a language subset
  keeps that language's rows together.

Every function takes plain sequences and returns plain dicts, so the numbers in a
report can be recomputed by hand from the CSV/JSONL the run wrote.

Definitions used throughout (and worth stating because "macro-F1" is three
different numbers in three papers):

* a *prediction* is a set of labels; a *truth* is a set of labels;
* per-label F1 is the usual ``2TP / (2TP + FP + FN)`` with a **zero** for a label
  that has no positives and no predictions — but such a label is reported as
  ``None`` by :func:`f1_per_label` and excluded from the macro average with the
  count of excluded labels printed next to it, because a 0 for "no rows exist"
  drags a macro average down for a data problem;
* macro-F1 is the unweighted mean over labels that have support *and* were
  predicted (or have support and no predictions, which is a real zero);
* the bootstrap resamples **rows** with replacement, ``B`` times, at a fixed
  seed, and reports the percentile interval.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable, Sequence

__all__ = [
    "bootstrap_interval",
    "expected_calibration_error",
    "f1_per_label",
    "macro_f1",
    "micro_f1",
    "primary_confusion",
    "reliability_bins",
    "subset_accuracy",
    "support_per_label",
]

#: Type of one row: truth set, prediction set. Kept as a tuple pair for speed.
Row = tuple[frozenset[str], frozenset[str]]


def _prf(
    true_positive: int, false_positive: int, false_negative: int
) -> tuple[float, float, float]:
    precision = (
        true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def support_per_label(
    rows: Sequence[Row],
    labels: Sequence[str],
) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(labels, 0)
    for truth, _ in rows:
        for label in truth:
            if label in counts:
                counts[label] += 1
    return counts


def f1_per_label(
    rows: Sequence[Row],
    labels: Sequence[str],
) -> dict[str, float | None]:
    """Per-label F1, or ``None`` when a label has neither support nor predictions.

    ``None`` is not the same as 0.0: 0.0 says "the model got this label wrong",
    ``None`` says "this split contains no rows for it", and a report that
    conflates the two is a report about the fixture, not the model.
    """

    result: dict[str, float | None] = {}
    for label in labels:
        tp = fp = fn = 0
        for truth, predicted in rows:
            in_true = label in truth
            in_pred = label in predicted
            if in_true and in_pred:
                tp += 1
            elif in_pred:
                fp += 1
            elif in_true:
                fn += 1
        if tp + fp + fn == 0:
            result[label] = None
        else:
            result[label] = _prf(tp, fp, fn)[2]
    return result


def macro_f1(
    rows: Sequence[Row],
    labels: Sequence[str],
) -> tuple[float, tuple[str, ...]]:
    """Macro-F1 plus the labels it averaged over."""

    per_label = f1_per_label(rows, labels)
    usable = tuple(label for label, value in per_label.items() if value is not None)
    if not usable:
        return 0.0, ()
    total = sum(value for value in per_label.values() if value is not None)
    return total / len(usable), usable


def micro_f1(rows: Sequence[Row]) -> float:
    tp = fp = fn = 0
    for truth, predicted in rows:
        tp += len(truth & predicted)
        fp += len(predicted - truth)
        fn += len(truth - predicted)
    return _prf(tp, fp, fn)[2]


def subset_accuracy(rows: Sequence[Row]) -> float:
    """Exact-match rate: the share of rows whose whole label set is right."""

    if not rows:
        return 0.0
    return sum(1 for truth, predicted in rows if truth == predicted) / len(rows)


def precision_recall_per_label(
    rows: Sequence[Row],
    labels: Sequence[str],
) -> dict[str, tuple[int, int, int, float, float, float]]:
    """``label → (tp, fp, fn, precision, recall, f1)`` — the numbers behind a table."""

    table: dict[str, tuple[int, int, int, float, float, float]] = {}
    for label in labels:
        tp = fp = fn = 0
        for truth, predicted in rows:
            in_true, in_pred = label in truth, label in predicted
            tp += int(in_true and in_pred)
            fp += int(in_pred and not in_true)
            fn += int(in_true and not in_pred)
        precision, recall, f1 = _prf(tp, fp, fn)
        table[label] = (tp, fp, fn, precision, recall, f1)
    return table


def primary_confusion(
    truths: Sequence[str],
    primaries: Sequence[str],
    labels: Sequence[str],
) -> dict[str, int]:
    """9x9 confusion counts keyed ``"true|predicted"`` over each row's primary label.

    A multi-label model does not have a confusion matrix in the textbook sense,
    so this is built from the *primary* label — the argmax, exactly what the
    product UI shows — and the report says so. The thresholded label sets are
    scored with F1 instead.
    """

    counts: Counter[str] = Counter()
    for truth, primary in zip(truths, primaries, strict=True):
        if truth not in labels or primary not in labels:
            continue
        counts[f"{truth}|{primary}"] += 1
    return dict(sorted(counts.items()))


def expected_calibration_error(
    probabilities: Sequence[float],
    correct: Sequence[bool],
    *,
    bins: int = 10,
) -> float:
    """Expected calibration error over ``(probability, correctness)`` pairs.

    The pairs are per **(row, label)**, not per row: a multi-label head has nine
    independent sigmoids and averaging them into one row-level confidence would
    hide exactly the miscalibration the report is looking for. Equal-width bins
    over ``[0, 1]``.
    """

    if not probabilities:
        return 0.0
    if len(probabilities) != len(correct):
        raise ValueError("probabilities and correct must be the same length")
    total = len(probabilities)
    error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        members = [
            (probability, hit)
            for probability, hit in zip(probabilities, correct, strict=True)
            if (low <= probability < high) or (index == bins - 1 and probability == 1.0)
        ]
        if not members:
            continue
        confidence = sum(probability for probability, _ in members) / len(members)
        accuracy = sum(1 for _, hit in members if hit) / len(members)
        error += (len(members) / total) * abs(accuracy - confidence)
    return error


def reliability_bins(
    probabilities: Sequence[float],
    correct: Sequence[bool],
    *,
    bins: int = 10,
) -> list[dict[str, float]]:
    """The per-bin table behind :func:`expected_calibration_error`."""

    out: list[dict[str, float]] = []
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        members = [
            (probability, hit)
            for probability, hit in zip(probabilities, correct, strict=True)
            if (low <= probability < high) or (index == bins - 1 and probability == 1.0)
        ]
        out.append(
            {
                "bin_low": low,
                "bin_high": high,
                "count": float(len(members)),
                "confidence": (
                    sum(probability for probability, _ in members) / len(members)
                    if members
                    else 0.0
                ),
                "accuracy": (sum(1 for _, hit in members if hit) / len(members))
                if members
                else 0.0,
            }
        )
    return out


def bootstrap_interval(
    rows: Sequence[Row],
    statistic: Callable[[Sequence[Row]], float],
    *,
    resamples: int = 2000,
    level: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap interval of ``statistic`` over resampled rows.

    Rows, not labels: resampling label-level counts would pretend the nine
    sigmoids are independent draws, and they are not (a row's labels co-occur).
    The RNG is seeded so the interval is a number, not a coin toss.
    """

    if not rows:
        return (0.0, 0.0)
    rng = random.Random(seed)
    size = len(rows)
    samples: list[float] = []
    for _ in range(resamples):
        draw = [rows[rng.randrange(size)] for _ in range(size)]
        samples.append(statistic(draw))
    samples.sort()
    alpha = (1.0 - level) / 2.0
    low = samples[min(len(samples) - 1, max(0, math.floor(alpha * len(samples))))]
    high = samples[min(len(samples) - 1, max(0, math.ceil((1.0 - alpha) * len(samples)) - 1))]
    return (low, high)
