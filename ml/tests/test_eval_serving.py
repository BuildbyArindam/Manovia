"""Evaluation and serving: a run id is enough to score and to use a model.

The end-to-end test here is the same sequence the Day-31 verification runs by
hand: train → evaluate the run it printed → load the bundle → predict. The
consistency check inside `evaluate_run` is the interesting part: it re-computes
the metrics the training run logged, so a non-deterministic pipeline or a moved
split fails here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import pytest

from manovia_ml.config import load_config_dict
from manovia_ml.evaluation.evaluate import evaluate_run
from manovia_ml.serving.artifacts import BundleError, load_bundle, save_bundle
from manovia_ml.serving.predictor import EmotionPredictor
from manovia_ml.training.train import train
from tests.conftest import config_dict


def train_something(splits: dict[str, dict[str, Any]]) -> Any:
    return train(load_config_dict(config_dict(splits)), check_expected=False)


def test_training_writes_a_loadable_bundle_into_the_run(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str, tmp_path: Path
) -> None:
    from manovia_ml.serving.artifacts import fetch_run_artifact

    result = train_something(toy_splits)
    bundle_path = fetch_run_artifact(result.run_id, destination=tmp_path)
    bundle = load_bundle(bundle_path)

    assert bundle_path.name == "model.joblib"
    assert bundle.labels == ["joy", "anger"]
    assert bundle.run_id is None or len(bundle.run_id) > 0
    assert bundle.config_sha256 is not None and len(bundle.config_sha256) == 64


def test_predictor_from_path_predicts_and_reports_confidence(tmp_path: Path) -> None:
    from sklearn.dummy import DummyClassifier

    pipeline = DummyClassifier(strategy="prior").fit(["a", "b", "c"], ["joy", "joy", "anger"])
    path = save_bundle(
        tmp_path / "model.joblib",
        pipeline=pipeline,
        labels=["joy", "anger"],
        meta={"config": "unit", "run_id": "unit-run"},
    )
    predictor = EmotionPredictor.from_path(path)

    predictions = predictor.predict(["hello", "the queue was unfair"])

    assert [item.label for item in predictions] == ["joy", "joy"]
    assert all(item.confidence is not None for item in predictions)
    assert predictor.labels == ["joy", "anger"]
    assert predictor.run_id == "unit-run"
    # The estimator's class order is lexicographic (anger, joy); the bundle's is
    # not. The probabilities must follow the *bundle's* order, not the columns.
    probabilities = predictor._bundle.predict_proba(["hello"])[0]
    assert set(probabilities) == {"joy", "anger"}
    assert probabilities["joy"] > probabilities["anger"]


def test_predictor_handles_empty_input(tmp_path: Path) -> None:
    from sklearn.dummy import DummyClassifier

    pipeline = DummyClassifier(strategy="most_frequent").fit(["a"], ["joy"])
    predictor = EmotionPredictor.from_path(
        save_bundle(tmp_path / "m.joblib", pipeline=pipeline, labels=["joy"], meta={})
    )

    assert predictor.predict([]) == []


def test_bundle_refuses_a_different_library_version(tmp_path: Path) -> None:
    """The version skew that bit this project twice is a load-time error here."""

    from sklearn.dummy import DummyClassifier

    path = save_bundle(
        tmp_path / "m.joblib",
        pipeline=DummyClassifier().fit(["a"], ["joy"]),
        labels=["joy"],
        meta={},
    )
    payload = joblib.load(path)
    payload["meta"]["library_versions"]["scikit-learn"] = "0.1.0"
    joblib.dump(payload, path)

    with pytest.raises(BundleError, match="different stack"):
        load_bundle(path)

    assert load_bundle(path, allow_version_mismatch=True).labels == ["joy"]


def test_bundle_rejects_a_foreign_file(tmp_path: Path) -> None:
    path = tmp_path / "not-a-bundle.joblib"
    joblib.dump({"hello": "world"}, path)

    with pytest.raises(BundleError, match="not a manovia-ml-bundle"):
        load_bundle(path)


def test_save_bundle_refuses_an_empty_label_list(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="no labels"):
        save_bundle(tmp_path / "m.joblib", pipeline=object(), labels=[], meta={})


def test_missing_bundle_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(BundleError, match="no bundle at"):
        load_bundle(tmp_path / "nope.joblib")


def test_evaluate_run_matches_the_training_run(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    result = train_something(toy_splits)

    evaluation = evaluate_run(result.run_id)

    assert evaluation.consistent, evaluation.consistency_problems
    assert evaluation.scored_run_id == result.run_id
    assert evaluation.eval_run_id != result.run_id
    assert evaluation.labels == ["joy", "anger"]
    for name, value in evaluation.metrics.items():
        assert value == pytest.approx(result.metrics[name], abs=1.0e-9), name


def test_evaluation_is_logged_as_its_own_run(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    from mlflow.tracking import MlflowClient

    result = train_something(toy_splits)
    evaluation = evaluate_run(result.run_id)

    fetched = MlflowClient().get_run(evaluation.eval_run_id)

    assert fetched.data.tags["manovia.phase"] == "eval"
    assert fetched.data.tags["manovia.eval_of"] == result.run_id
    assert fetched.data.params["eval_of"] == result.run_id
    assert fetched.data.metrics["accuracy"] == pytest.approx(evaluation.metrics["accuracy"])
    artefacts = {item.path for item in MlflowClient().list_artifacts(evaluation.eval_run_id)}
    assert "eval.json" in artefacts


def test_evaluation_refuses_data_that_no_longer_matches_the_pin(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    """The split is pinned in the config; a changed file must stop the scoring."""

    result = train_something(toy_splits)
    eval_path = Path(str(toy_splits["eval"]["path"]))
    original = eval_path.read_text(encoding="utf-8")
    eval_path.write_text(original.replace("joy", "anger", 3), encoding="utf-8")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        evaluate_run(result.run_id)

    eval_path.write_text(original, encoding="utf-8")


def test_evaluate_run_rejects_a_failed_bundle_pin(
    toy_splits: dict[str, dict[str, Any]], ml_tracking: str
) -> None:
    config = load_config_dict(config_dict(toy_splits, name="pin_test"))
    # A config whose pin cannot be satisfied must fail before any scoring happens.
    pinned = config.model_copy(
        update={
            "data": config.data.model_copy(
                update={
                    "eval": config.data.eval.model_copy(update={"sha256": "b" * 64}),
                }
            )
        }
    )

    with pytest.raises(ValueError, match="sha256 mismatch"):
        train(pinned, check_expected=False)
