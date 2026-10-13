"""Scoring an emotion model: macro-F1, per language, calibration, CIs, mistakes.

This module is deliberately **inference-only**. It imports the artefact loader
and the metric helpers, and nothing from the training loop, so the question the
Day-32 verification asks — "re-run the evaluation from the saved artefact in a
fresh process, no training code" — is answered by construction:
``python -m manovia_ml.training.emotion.evaluate --artifact DIR`` never touches
an optimiser, a scheduler or the training data. :func:`evaluate_artifact`
asserts it: it fails if ``manovia_ml.training.emotion.train`` is in
``sys.modules``.

The output payload is one JSON object with everything the report and the model
card quote, including the sample sizes those numbers rest on. Two things it says
out loud rather than leaving to the reader:

* macro-F1 here averages only over labels with support *or* predictions (see
  ``metrics.f1_per_label``), and the excluded labels are listed;
* a bootstrap interval over 174 rows is ±~0.07 wide, so a 0.02 difference
  between two models on that set is not a difference.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manovia_ml.training.emotion import metrics as metric_fns
from manovia_ml.training.emotion.data import (
    DEFAULT_VALIDATION_PATH,
    EmotionExample,
    default_validation_path,
    load_examples,
    normalise_text,
)
from manovia_ml.training.emotion.inference import (
    EmotionArtifact,
    load_artifact,
    predict_sets,
)
from manovia_ml.training.emotion.metrics import Row
from manovia_ml.training.emotion.taxonomy import EMOTIONS, NEUTRAL

__all__ = [
    "ErrorCase",
    "error_analysis",
    "evaluate_artifact",
    "score_predictions",
    "split_rows",
]

#: Causes the error analysis can assign, in the order it tries them. They are
#: *heuristics* and the report says so: they group mistakes for a human to read,
#: they are not a second model's opinion.
CAUSES = (
    "neutral_absorbed_a_positive",
    "positive_absorbed_a_neutral",
    "no_surface_cue",
    "label_neighbour_confusion",
    "untrained_language",
    "ambiguous_row",
)


@dataclass(frozen=True)
class ErrorCase:
    """One mistake, with a grouping cause and the numbers behind it."""

    example_id: str
    lang: str
    text: str
    truth: tuple[str, ...]
    predicted: tuple[str, ...]
    primary: str
    worst_label: str | None
    worst_probability: float
    cause: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.example_id,
            "lang": self.lang,
            "text": self.text,
            "truth": list(self.truth),
            "predicted": list(self.predicted),
            "primary": self.primary,
            "worst_label": self.worst_label,
            "worst_probability": round(self.worst_probability, 4),
            "cause": self.cause,
        }


def split_rows(
    examples: Sequence[EmotionExample],
    probabilities: Sequence[Sequence[float]],
    labels: Sequence[str] = EMOTIONS,
    thresholds: Mapping[str, float] | None = None,
) -> list[Row]:
    """``[(truth set, predicted set)]`` per row, in input order."""

    predicted = predict_sets(probabilities, labels, dict(thresholds or {}))
    return [
        (frozenset(example.labels), prediction)
        for example, prediction in zip(examples, predicted, strict=True)
    ]


def _primary_labels(
    probabilities: Sequence[Sequence[float]],
    labels: Sequence[str],
) -> list[str]:
    return [_argmax(row, labels) for row in probabilities]


def _argmax(row: Sequence[float], labels: Sequence[str]) -> str:
    best_index = max(range(len(labels)), key=lambda index: (float(row[index]), -index))
    if float(row[best_index]) <= 0.0:
        return NEUTRAL
    return labels[best_index]


def _confidence_pairs(
    probabilities: Sequence[Sequence[float]],
    examples: Sequence[EmotionExample],
    labels: Sequence[str],
    thresholds: Mapping[str, float],
) -> tuple[list[float], list[bool]]:
    """``(probability, correct)`` per (row, label) — the ECE input."""

    pairs: list[float] = []
    correct: list[bool] = []
    for example, row in zip(examples, probabilities, strict=True):
        truth = frozenset(example.labels)
        for index, label in enumerate(labels):
            probability = float(row[index])
            pairs.append(probability)
            correct.append((probability >= float(thresholds.get(label, 0.5))) == (label in truth))
    return pairs, correct


def score_predictions(
    examples: Sequence[EmotionExample],
    probabilities: Sequence[Sequence[float]],
    *,
    labels: Sequence[str] = EMOTIONS,
    thresholds: Mapping[str, float] | None = None,
    split: str = "test",
    bootstrap_resamples: int = 2000,
    confidence_level: float = 0.95,
    calibration_bins: int = 10,
    seed: int = 0,
) -> dict[str, Any]:
    """Everything a report quotes about one split, as one JSON-serialisable dict."""

    labels = tuple(labels)
    resolved_thresholds = (
        {label: float(thresholds.get(label, 0.5)) for label in labels}
        if thresholds
        else dict.fromkeys(labels, 0.5)
    )
    rows = split_rows(examples, probabilities, labels, resolved_thresholds)
    per_label = metric_fns.f1_per_label(rows, labels)
    macro, averaged_over = metric_fns.macro_f1(rows, labels)
    macro_ci = metric_fns.bootstrap_interval(
        rows,
        lambda sample: metric_fns.macro_f1(sample, labels)[0],
        resamples=bootstrap_resamples,
        level=confidence_level,
        seed=seed,
    )
    subset_ci = metric_fns.bootstrap_interval(
        rows,
        metric_fns.subset_accuracy,
        resamples=bootstrap_resamples,
        level=confidence_level,
        seed=seed + 1,
    )
    micro_ci = metric_fns.bootstrap_interval(
        rows,
        metric_fns.micro_f1,
        resamples=bootstrap_resamples,
        level=confidence_level,
        seed=seed + 2,
    )

    per_language: dict[str, Any] = {}
    for language in sorted({example.lang for example in examples}):
        indices = [index for index, example in enumerate(examples) if example.lang == language]
        subset = [rows[index] for index in indices]
        language_macro, language_labels = metric_fns.macro_f1(subset, labels)
        language_ci = metric_fns.bootstrap_interval(
            subset,
            lambda sample: metric_fns.macro_f1(sample, labels)[0],
            resamples=bootstrap_resamples,
            level=confidence_level,
            seed=seed + 3,
        )
        per_language[language] = {
            "rows": len(subset),
            "macro_f1": round(language_macro, 6),
            "macro_f1_ci": [round(value, 6) for value in language_ci],
            "micro_f1": round(metric_fns.micro_f1(subset), 6),
            "subset_accuracy": round(metric_fns.subset_accuracy(subset), 6),
            "labels_averaged_over": list(language_labels),
        }

    probabilities_pairs, correct_pairs = _confidence_pairs(
        probabilities, examples, labels, resolved_thresholds
    )
    ece = metric_fns.expected_calibration_error(
        probabilities_pairs, correct_pairs, bins=calibration_bins
    )

    # The confusion matrix needs one label per axis. Ground truth is the row's
    # own label when there is exactly one, and otherwise the true label the model
    # scored highest — a multi-label row has no single truth, and the report
    # says the table is the *primary* view rather than the whole answer.
    truth_primaries: list[str] = []
    for example, row in zip(examples, probabilities, strict=True):
        if len(example.labels) == 1:
            truth_primaries.append(example.labels[0])
        else:
            truth_primaries.append(
                max(example.labels, key=lambda label: (float(row[labels.index(label)]), label))
            )
    primaries = _primary_labels(probabilities, labels)

    return {
        "split": split,
        "rows": len(examples),
        "labels": list(labels),
        "thresholds": {label: round(value, 4) for label, value in resolved_thresholds.items()},
        "macro_f1": round(macro, 6),
        "macro_f1_ci": [round(value, 6) for value in macro_ci],
        "micro_f1": round(metric_fns.micro_f1(rows), 6),
        "micro_f1_ci": [round(value, 6) for value in micro_ci],
        "subset_accuracy": round(metric_fns.subset_accuracy(rows), 6),
        "subset_accuracy_ci": [round(value, 6) for value in subset_ci],
        "ece": round(ece, 6),
        "labels_averaged_over": list(averaged_over),
        "labels_without_support": [label for label, value in per_label.items() if value is None],
        "per_label_f1": {
            label: (None if value is None else round(value, 6))
            for label, value in per_label.items()
        },
        "per_label_detail": {
            label: {
                "tp": values[0],
                "fp": values[1],
                "fn": values[2],
                "precision": round(values[3], 6),
                "recall": round(values[4], 6),
                "f1": round(values[5], 6),
            }
            for label, values in metric_fns.precision_recall_per_label(rows, labels).items()
        },
        "per_language": per_language,
        "primary_confusion": metric_fns.primary_confusion(truth_primaries, primaries, labels),
        "reliability": metric_fns.reliability_bins(
            probabilities_pairs, correct_pairs, bins=calibration_bins
        ),
        "support": metric_fns.support_per_label(rows, labels),
        "notes": (
            "macro_f1 averages only labels with support or predictions; primary_confusion uses "
            "the argmax label (what the product shows) because a multi-label model has no "
            "single-label confusion matrix."
        ),
    }


def _cause_for(
    example: EmotionExample,
    predicted: frozenset[str],
    *,
    trained_languages: Sequence[str],
    neighbour_pairs: set[tuple[str, str]],
) -> str:
    """A heuristic tag for one mistake, for grouping in the report."""

    truth = example.label_set
    if example.lang not in trained_languages:
        return "untrained_language"
    missed = truth - predicted
    spurious = predicted - truth
    if NEUTRAL in spurious and missed:
        return "neutral_absorbed_a_positive"
    if NEUTRAL in missed and spurious:
        return "positive_absorbed_a_neutral"
    if any(
        (truth_label, predicted_label) in neighbour_pairs
        for truth_label in missed
        for predicted_label in spurious
    ):
        return "label_neighbour_confusion"
    if len(truth) > 1 or len(predicted) > 1:
        return "ambiguous_row"
    if normalise_text(example.text).count(" ") < 4:
        return "no_surface_cue"
    return "no_surface_cue"


def error_analysis(
    examples: Sequence[EmotionExample],
    probabilities: Sequence[Sequence[float]],
    *,
    labels: Sequence[str] = EMOTIONS,
    thresholds: Mapping[str, float] | None = None,
    trained_languages: Sequence[str] = ("en",),
    limit: int = 20,
) -> list[ErrorCase]:
    """The worst mistakes, grouped by cause.

    "Worst" is ranked by how wrong the sigmoid was about the label that matters
    most on that row: the largest ``|p - y|`` over all nine labels. That puts a
    confident wrong answer above a hesitant one, which is the order a person
    reading the list would want.
    """

    labels = tuple(labels)
    resolved = (
        {label: float(thresholds.get(label, 0.5)) for label in labels}
        if thresholds
        else dict.fromkeys(labels, 0.5)
    )
    predicted_sets = predict_sets(probabilities, labels, resolved)
    neighbour_pairs = {
        ("anxiety", "fear"),
        ("fear", "anxiety"),
        ("sadness", "loneliness"),
        ("loneliness", "sadness"),
        ("sadness", "neutral"),
        ("shame", "anxiety"),
        ("anger", "neutral"),
        ("calm", "neutral"),
        ("neutral", "calm"),
    }
    cases: list[tuple[float, ErrorCase]] = []
    for example, row, predicted in zip(examples, probabilities, predicted_sets, strict=True):
        truth = frozenset(example.labels)
        if truth == predicted:
            continue
        errors = [
            abs(float(value) - (1.0 if label in truth else 0.0))
            for label, value in zip(labels, row, strict=True)
        ]
        worst_index = max(range(len(labels)), key=lambda index: errors[index])
        cause = _cause_for(
            example,
            predicted,
            trained_languages=trained_languages,
            neighbour_pairs=neighbour_pairs,
        )
        cases.append(
            (
                errors[worst_index],
                ErrorCase(
                    example_id=example.id,
                    lang=example.lang,
                    text=example.text,
                    truth=tuple(label for label in labels if label in truth),
                    predicted=tuple(label for label in labels if label in predicted),
                    primary=_argmax(row, labels),
                    worst_label=labels[worst_index],
                    worst_probability=float(row[worst_index]),
                    cause=cause,
                ),
            )
        )
    cases.sort(key=lambda item: (-item[0], item[1].example_id))
    return [case for _, case in cases[:limit]]


def evaluate_artifact(
    artifact: EmotionArtifact,
    *,
    data: str | Path | None = None,
    examples: Sequence[EmotionExample] | None = None,
    split: str = "test",
    bootstrap_resamples: int = 2000,
    confidence_level: float = 0.95,
    calibration_bins: int = 10,
    seed: int = 0,
    trained_languages: Sequence[str] = ("en",),
    error_limit: int = 20,
    batch_size: int = 16,
) -> dict[str, Any]:
    """Score a packaged artefact on a split. No training code is imported."""

    if "manovia_ml.training.emotion.train" in sys.modules:  # pragma: no cover - guard
        raise RuntimeError(
            "training code is imported in this process; evaluation must run from the artefact alone"
        )
    if examples is None:
        examples = load_examples(data if data is not None else default_validation_path())
    probabilities = artifact.predict_probabilities(
        [example.text for example in examples], batch_size=batch_size
    )
    payload = score_predictions(
        examples,
        probabilities,
        labels=artifact.labels,
        thresholds=artifact.thresholds,
        split=split,
        bootstrap_resamples=bootstrap_resamples,
        confidence_level=confidence_level,
        calibration_bins=calibration_bins,
        seed=seed,
    )
    payload["artifact"] = {
        "directory": str(artifact.directory),
        "base_model": artifact.base_model,
        "warnings": list(artifact.warnings),
    }
    payload["errors"] = [
        case.as_dict()
        for case in error_analysis(
            examples,
            probabilities,
            labels=artifact.labels,
            thresholds=artifact.thresholds,
            trained_languages=trained_languages,
            limit=error_limit,
        )
    ]
    return payload


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.training.emotion.evaluate",
        description="Score a packaged emotion artefact. Loads no training code.",
    )
    parser.add_argument("--artifact", required=True, help="directory written by the training run")
    parser.add_argument(
        "--data", default=None, help=f"JSONL split (default: {DEFAULT_VALIDATION_PATH})"
    )
    parser.add_argument("--split", default="test")
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--bins", type=int, default=10)
    parser.add_argument("--errors", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    artifact = load_artifact(args.artifact)
    payload = evaluate_artifact(
        artifact,
        data=args.data,
        split=args.split,
        bootstrap_resamples=args.bootstrap,
        calibration_bins=args.bins,
        error_limit=args.errors,
        batch_size=args.batch_size,
    )
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(text)
    else:
        print(f"wrote {args.out}")
        print(
            f"macro_f1={payload['macro_f1']:.4f} "
            f"ci={payload['macro_f1_ci']} ece={payload['ece']:.4f} rows={payload['rows']}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(_main())
