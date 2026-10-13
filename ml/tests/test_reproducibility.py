"""The Day-31 reproducibility contract, as tests.

1. Same config + same seed → metrics equal, well inside tolerance.
2. Different seed → metrics move (on a stochastic model), so the seed is not
   decoration.
3. The recorded baseline (`ml/configs/expected/<config>.json`) is checked on
   every run, and a drifted one stops the run.
4. A deterministic model kind stays deterministic across seeds — the seed check
   above must not be satisfied by *everything* being random.

The numbers are tiny (20 train / 8 eval rows). What is being asserted is the
machinery: the same inputs produce the same outputs, bit for bit, and the
baseline gate notices when they do not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from manovia_ml.config import compare_metrics, expected_metrics_path, load_config_dict
from manovia_ml.training.train import train
from tests.conftest import config_dict

TOLERANCE = 1.0e-6


def metrics_of(splits: dict[str, dict[str, Any]]) -> dict[str, float]:
    return train(load_config_dict(config_dict(splits)), check_expected=False).metrics


def test_same_config_and_seed_reproduce_the_metrics(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    first = metrics_of(toy_splits)
    second = metrics_of(toy_splits)

    assert set(first) == set(second)
    for name, value in first.items():
        assert second[name] == pytest.approx(value, abs=TOLERANCE), name


def test_a_different_seed_moves_the_metrics(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    baseline = metrics_of(toy_splits)
    other = train(load_config_dict(config_dict(toy_splits, seed=7)), check_expected=False).metrics

    problems = compare_metrics(other, baseline, tolerance=TOLERANCE)

    assert problems, (
        "a different seed produced identical metrics — the seed is not reaching the model"
    )
    assert other["log_loss"] != pytest.approx(baseline["log_loss"], abs=TOLERANCE)


def test_sgd_is_seed_sensitive_while_logreg_is_deterministic(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    """Both behaviours are properties, not accidents (documented in config.py)."""

    sgd_a = train(load_config_dict(config_dict(toy_splits, seed=1)), check_expected=False).metrics
    sgd_b = train(load_config_dict(config_dict(toy_splits, seed=2)), check_expected=False).metrics
    lr_a = train(
        load_config_dict(config_dict(toy_splits, kind="tfidf_logreg", seed=1)), check_expected=False
    ).metrics
    lr_b = train(
        load_config_dict(config_dict(toy_splits, kind="tfidf_logreg", seed=2)), check_expected=False
    ).metrics

    assert compare_metrics(sgd_b, sgd_a, tolerance=TOLERANCE) != []
    assert compare_metrics(lr_b, lr_a, tolerance=TOLERANCE) == []


def test_the_pipeline_can_still_fit_its_own_rows(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    """The canary that held-out accuracy on 8 rows cannot be.

    If labels were misaligned, the features stopped being built, or the fit
    silently no-oped, held-out accuracy could stay "plausible" while this
    collapses. It is logged on every run as `train_accuracy`.
    """

    result = train(load_config_dict(config_dict(toy_splits)), check_expected=False)

    assert result.metrics["train_accuracy"] == pytest.approx(1.0)


def test_a_drifted_baseline_stops_the_run(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str, tmp_path: Path
) -> None:
    config_path = tmp_path / "baseline_test.yaml"
    config_path.write_text(
        yaml.safe_dump(config_dict(toy_splits, name="baseline_test")), encoding="utf-8"
    )

    recorded = train(
        load_config_dict(config_dict(toy_splits, name="baseline_test")),
        config_path=config_path,
        check_expected=False,
        record_expected=True,
    )
    assert recorded.recorded_expected_path is not None
    assert expected_metrics_path(config_path).is_file()

    # The same run again: the baseline matches, nothing to report.
    clean = train(
        load_config_dict(config_dict(toy_splits, name="baseline_test")), config_path=config_path
    )
    assert clean.expected_checked is True
    assert clean.expected_problems == []

    # A deliberately wrong baseline must be caught, not tolerated.
    stored = expected_metrics_path(config_path).read_text(encoding="utf-8")
    expected_metrics_path(config_path).write_text(
        stored.replace('"accuracy": 0.375', '"accuracy": 0.99'), encoding="utf-8"
    )
    drifted = train(
        load_config_dict(config_dict(toy_splits, name="baseline_test")), config_path=config_path
    )

    assert drifted.expected_problems
    assert any("accuracy" in problem for problem in drifted.expected_problems)


def test_a_seed_override_skips_the_baseline_check(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str, tmp_path: Path
) -> None:
    """Changing the seed is a legitimate act; it is the *same-seed* check that gates."""

    config_path = tmp_path / "seed_test.yaml"
    config_path.write_text(
        yaml.safe_dump(config_dict(toy_splits, name="seed_test")), encoding="utf-8"
    )
    train(
        load_config_dict(config_dict(toy_splits, name="seed_test")),
        config_path=config_path,
        check_expected=False,
        record_expected=True,
    )

    # The baseline was recorded at seed 20261013; this run uses 99 on purpose, so
    # the check is skipped rather than failed (a seed change is not drift).
    result = train(
        load_config_dict(config_dict(toy_splits, name="seed_test", seed=99)),
        config_path=config_path,
    )

    assert result.expected_checked is False
    assert result.expected_problems == []


def test_metrics_are_logged_to_mlflow_with_the_seed(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    from mlflow.tracking import MlflowClient

    result = train(load_config_dict(config_dict(toy_splits)), check_expected=False)
    fetched = MlflowClient().get_run(result.run_id)

    assert fetched.data.params["seed"] == "20261013"
    assert float(fetched.data.params["evaluation.tolerance"]) == TOLERANCE
    assert set(result.metrics) <= set(fetched.data.metrics)
    for name, value in result.metrics.items():
        assert fetched.data.metrics[name] == pytest.approx(value, abs=TOLERANCE)


def test_deterministic_run_ids_differ_but_the_record_is_the_same(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    """Two runs are two records; only the *content* is required to match."""

    first = train(load_config_dict(config_dict(toy_splits)), check_expected=False)
    second = train(load_config_dict(config_dict(toy_splits)), check_expected=False)

    assert first.run_id != second.run_id
    assert compare_metrics(second.metrics, first.metrics, tolerance=TOLERANCE) == []
    assert first.train_ref.sha256 == second.train_ref.sha256
