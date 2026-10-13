"""Multi-label metrics, calibration, bootstrap intervals and threshold tuning."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

import pytest

from manovia_ml.config import ThresholdConfig
from manovia_ml.training.emotion import metrics as emotion_metrics
from manovia_ml.training.emotion.metrics import Row
from manovia_ml.training.emotion.thresholds import apply_thresholds, grid_values, tune_thresholds

LABELS = ("joy", "sadness", "anger")


def _rows(pairs: Iterable[tuple[Iterable[str], Iterable[str]]]) -> list[Row]:
    return [(frozenset(truth), frozenset(predicted)) for truth, predicted in pairs]


def test_f1_is_none_without_support_or_predictions_and_zero_when_wrong() -> None:
    rows = _rows([({"joy"}, {"joy"}), ({"joy"}, {"sadness"}), ({"sadness"}, set())])
    per_label = emotion_metrics.f1_per_label(rows, LABELS)
    # joy: tp 1, fp 0, fn 1 -> P 1.0, R 0.5, F1 0.667
    assert per_label["joy"] == pytest.approx(2 / 3)
    # sadness: tp 0, fp 1, fn 1 -> F1 0.0 (support exists, the model got it wrong)
    assert per_label["sadness"] == 0.0
    # anger: no support and no predictions -> None, which is not the same as 0.0
    assert per_label["anger"] is None


def test_macro_f1_averages_only_the_labels_it_can() -> None:
    rows = _rows([({"joy"}, {"joy"}), ({"joy"}, {"joy"})])
    value, averaged = emotion_metrics.macro_f1(rows, LABELS)
    assert averaged == ("joy",)
    assert value == pytest.approx(1.0)


def test_micro_f1_and_subset_accuracy() -> None:
    rows = _rows([({"joy", "sadness"}, {"joy"}), ({"anger"}, {"anger"})])
    assert emotion_metrics.subset_accuracy(rows) == pytest.approx(0.5)
    # tp 2 (joy, anger), fp 0, fn 1 (sadness)
    assert emotion_metrics.micro_f1(rows) == pytest.approx(2 * 2 / (2 * 2 + 0 + 1))


def test_primary_confusion_is_keyed_true_predicted() -> None:
    counts = emotion_metrics.primary_confusion(
        ["joy", "joy", "anger"], ["joy", "sadness", "anger"], LABELS
    )
    assert counts == {"joy|joy": 1, "joy|sadness": 1, "anger|anger": 1}


def test_ece_is_zero_when_probabilities_are_perfectly_calibrated() -> None:
    # Two buckets: every 1.0 is right, every 0.0 is wrong.
    probabilities = [1.0] * 10 + [0.0] * 10
    correct = [True] * 10 + [False] * 10
    assert emotion_metrics.expected_calibration_error(
        probabilities, correct, bins=10
    ) == pytest.approx(0.0)


def test_ece_is_positive_when_the_model_is_overconfident() -> None:
    probabilities = [0.9] * 10
    correct = [True] * 5 + [False] * 5
    assert emotion_metrics.expected_calibration_error(
        probabilities, correct, bins=2
    ) == pytest.approx(0.4)


def test_reliability_bins_account_for_every_pair() -> None:
    probabilities = [0.1, 0.2, 0.9, 1.0]
    correct = [False, True, True, True]
    bins = emotion_metrics.reliability_bins(probabilities, correct, bins=5)
    assert sum(int(entry["count"]) for entry in bins) == 4


def test_bootstrap_interval_is_seeded_and_brackets_the_point_estimate() -> None:
    rows = _rows([({"joy"}, {"joy"})] * 5 + [({"joy"}, {"sadness"})] * 5)
    first = emotion_metrics.bootstrap_interval(
        rows, lambda sample: emotion_metrics.macro_f1(sample, LABELS)[0], resamples=200, seed=7
    )
    second = emotion_metrics.bootstrap_interval(
        rows, lambda sample: emotion_metrics.macro_f1(sample, LABELS)[0], resamples=200, seed=7
    )
    assert first == second
    point, _ = emotion_metrics.macro_f1(rows, LABELS)
    assert first[0] <= point <= first[1]


def test_bootstrap_interval_of_a_constant_is_zero_width() -> None:
    rows = _rows([({"joy"}, {"joy"})] * 4)
    low, high = emotion_metrics.bootstrap_interval(
        rows, emotion_metrics.subset_accuracy, resamples=50, seed=1
    )
    assert (low, high) == (1.0, 1.0)


def test_threshold_grid_is_inclusive_and_ordered() -> None:
    grid = grid_values(ThresholdConfig(search_min=0.1, search_max=0.9, search_steps=5))
    assert grid == (0.1, 0.3, 0.5, 0.7, 0.9)


def test_tuning_lowers_the_threshold_when_positives_are_under_predicted() -> None:
    # The positive class sits at 0.3-0.4 and 0.5 would miss all of it, while the
    # negatives stay under 0.2: the fitted threshold has to come down.
    probabilities = {"joy": [0.35, 0.4, 0.05, 0.1, 0.32, 0.08]}
    truths = {"joy": [True, True, False, False, True, False]}
    tuning = tune_thresholds(
        probabilities, truths, config=ThresholdConfig(min_support=2), labels=("joy",)
    )
    assert tuning.thresholds["joy"] < 0.5
    assert tuning.per_label["joy"].tuned is True
    assert tuning.per_label["joy"].metric_at_threshold >= tuning.per_label["joy"].metric_at_default


def test_low_support_keeps_the_default_and_says_why() -> None:
    tuning = tune_thresholds(
        {"joy": [0.9, 0.1]},
        {"joy": [True, False]},
        config=ThresholdConfig(min_support=3),
        labels=("joy",),
    )
    assert tuning.thresholds["joy"] == 0.5
    assert tuning.per_label["joy"].tuned is False
    assert "min_support" in tuning.per_label["joy"].reason


def test_tuning_is_deterministic_and_serialisable() -> None:
    probabilities = {"joy": [0.2, 0.8, 0.6, 0.1], "sadness": [0.7, 0.2, 0.1, 0.9]}
    truths = {"joy": [False, True, True, False], "sadness": [True, False, False, True]}
    config = ThresholdConfig(min_support=2)
    first = tune_thresholds(probabilities, truths, config=config, labels=("joy", "sadness"))
    second = tune_thresholds(probabilities, truths, config=config, labels=("joy", "sadness"))
    assert first.thresholds == second.thresholds
    payload = first.as_dict()
    assert payload["split"] == "validation"
    thresholds = cast(dict[str, float], payload["thresholds"])
    assert set(thresholds) == {"joy", "sadness"}
    assert "Tuned on the validation split only" in str(payload["note"])


def test_a_label_with_no_rows_keeps_the_default() -> None:
    tuning = tune_thresholds({"joy": []}, {"joy": []}, config=ThresholdConfig(), labels=("joy",))
    assert tuning.thresholds["joy"] == 0.5
    assert tuning.per_label["joy"].reason == "no validation rows for this label"


def test_apply_thresholds_uses_per_label_values() -> None:
    labels = ("joy", "sadness")
    thresholds = {"joy": 0.2, "sadness": 0.9}
    assert apply_thresholds([0.3, 0.5], labels, thresholds) == frozenset({"joy"})
    assert apply_thresholds([0.3, 0.95], labels, thresholds) == frozenset({"joy", "sadness"})
