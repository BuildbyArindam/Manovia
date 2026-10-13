"""The metric definitions, including the floor that makes accuracy readable.

The point of these tests is not that scikit-learn's accuracy is correct. It is
that *this project's* numbers mean one thing: the same definition everywhere, the
majority-class floor always available next to accuracy, log loss independent of
the order a config happens to list its labels in, and a report that cannot show
accuracy without the floor beside it.
"""

from __future__ import annotations

import pytest

from manovia_ml.evaluation.metrics import (
    classification_metrics,
    confusion,
    majority_baseline_accuracy,
    per_class_f1,
)
from manovia_ml.reporting import render_report

LABELS = ["joy", "sadness", "anger"]


def test_majority_baseline_is_the_score_of_a_constant_classifier() -> None:
    assert majority_baseline_accuracy(["joy", "anger", "joy", "anger"]) == 0.5
    assert majority_baseline_accuracy(["joy"] * 9 + ["anger"]) == 0.9


def test_majority_baseline_refuses_an_empty_eval_set() -> None:
    with pytest.raises(ValueError, match="no rows"):
        majority_baseline_accuracy([])


def test_metrics_are_keyed_by_the_names_the_config_asked_for() -> None:
    values = classification_metrics(
        ["joy", "anger"],
        ["joy", "anger"],
        labels=["joy", "anger"],
        metrics=["accuracy", "macro_f1"],
    )

    assert set(values) == {"accuracy", "macro_f1"}
    assert values["accuracy"] == 1.0
    assert values["macro_f1"] == 1.0


def test_log_loss_does_not_depend_on_the_config_label_order() -> None:
    """Regression: columns are reordered to `sorted(labels)` before sklearn sees them.

    Reading `predict_proba` positionally against the config's label order silently
    mislabels every probability, and scikit-learn's only objection is a warning
    about lexicographic order that is easy to scroll past.
    """

    truth = ["joy", "anger", "joy", "anger"]
    # A model that is confidently right about the first two rows and wrong about
    # the last two, expressed in two different label orders.
    in_config_order = [
        [0.9, 0.1],  # joy, anger
        [0.2, 0.8],
        [0.9, 0.1],
        [0.1, 0.9],
    ]

    first = classification_metrics(
        truth,
        ["joy", "anger", "joy", "anger"],
        labels=["joy", "anger"],
        metrics=["log_loss"],
        probabilities=in_config_order,
    )
    second = classification_metrics(
        truth,
        ["joy", "anger", "joy", "anger"],
        labels=["anger", "joy"],
        metrics=["log_loss"],
        probabilities=[[row[1], row[0]] for row in in_config_order],
    )

    assert first["log_loss"] == pytest.approx(second["log_loss"])
    assert first["log_loss"] < 0.4


def test_log_loss_without_probabilities_is_an_error_not_a_zero() -> None:
    with pytest.raises(ValueError, match="predicted probabilities"):
        classification_metrics(["joy"], ["joy"], labels=["joy"], metrics=["log_loss"])


def test_probabilities_of_the_wrong_width_are_rejected() -> None:
    with pytest.raises(IndexError):
        classification_metrics(
            ["joy"],
            ["joy"],
            labels=["joy", "anger"],
            metrics=["log_loss"],
            probabilities=[[0.5]],
        )


def test_confusion_matrix_rows_are_truth_and_columns_are_prediction() -> None:
    assert confusion(["joy", "anger"], ["anger", "anger"], labels=["joy", "anger"]) == [
        [0, 1],
        [0, 1],
    ]


def test_per_class_f1_carries_support_next_to_each_label() -> None:
    values = per_class_f1(["joy", "joy", "anger"], ["joy", "anger", "anger"], labels=LABELS)

    # One key per label (the F1) and one `support.<label>` per label (the row count),
    # so a reader can always see the denominator behind a zero.
    assert values["support.joy"] == 2.0
    assert values["support.sadness"] == 0.0
    assert values["sadness"] == 0.0  # zero_division=0, not a warning


def test_a_report_never_shows_accuracy_without_the_floor() -> None:
    report = render_report(
        run_id="abc123",
        params={"seed": "1", "manovia.phase": "smoke"},
        metrics={"accuracy": 0.375, "majority_accuracy": 0.5, "train_accuracy": 1.0},
        tags={"manovia.phase": "smoke"},
        title="toy",
    )

    assert "majority-class rate" in report
    assert "below the floor" in report
    assert "in-sample accuracy" in report


def test_a_report_still_renders_for_a_run_without_the_extra_metrics() -> None:
    report = render_report(
        run_id="abc123", params={"seed": "1"}, metrics={"accuracy": 0.5}, tags={}, title="toy"
    )

    assert "| accuracy | 0.500000 |" in report
    assert "majority-class rate" not in report
