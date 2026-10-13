"""Every run must log the seven things the Day-31 brief asks for.

The test reads them back out of MLflow rather than trusting the code that wrote
them: if `tracked_run` stopped logging the commit or the data hash, these tests
fail without anyone having to remember the requirement.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from manovia_ml.config import TrainConfig
from manovia_ml.data.toy import build_splits
from manovia_ml.hashing import dataset_ref
from manovia_ml.mlflow_api import MlflowClient, mlflow
from manovia_ml.seeds import seed_everything
from manovia_ml.tracking import git_state, library_versions, tracked_run


def test_reporting_can_open_the_file_store_in_a_fresh_process(tmp_path: Path) -> None:
    """Regression: the report step of the Day-31 smoke run crashed on MLflow 3.x.

    The filesystem-store opt-out used to live in `tracking.py`. The smoke run then
    trained fine (that process imports `tracking`) and died *afterwards*, in a
    separate `python -m manovia_ml.reporting` process that never imports it, with
    "The filesystem tracking backend is in maintenance mode". A module that only
    reads a run record must still be able to open the default store, so the check
    runs in a fresh interpreter with `MLFLOW_ALLOW_FILE_STORE` deliberately
    removed from the environment.
    """

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MLFLOW_ALLOW_FILE_STORE", "MLFLOW_TRACKING_URI"}
    }
    env["MLFLOW_TRACKING_URI"] = f"file:{tmp_path / 'mlruns'}"
    script = (
        "import manovia_ml.reporting;"  # the only manovia import in the process
        "from manovia_ml.mlflow_api import MlflowClient;"
        "MlflowClient().search_experiments();"
        "print('ok')"
    )

    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, check=False
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


REQUIRED_PARAMS = {
    "configname",
    "config.sha256",
    "config.json",
    "phase",
    "git.commit",
    "git.branch",
    "git.dirty",
    "python.version",
    "platform",
    "seed",
    "lib.scikit-learn",
    "lib.mlflow",
}

REQUIRED_TAGS = {
    "manovia.phase",
    "manovia.config",
    "manovia.config.sha256",
    "manovia.git.commit",
    "manovia.started",
}


def test_tracked_run_logs_the_required_record(
    toy_config: TrainConfig, toy_splits: dict[str, dict[str, object]], ml_tracking: str
) -> None:
    refs = [
        dataset_ref(toy_splits["train"]["path"], name="train"),  # type: ignore[arg-type]
        dataset_ref(toy_splits["eval"]["path"], name="eval"),  # type: ignore[arg-type]
    ]
    state = seed_everything(toy_config.seed)

    with tracked_run(
        toy_config, phase="test", data_refs=refs, seed_state=state, params={"custom.param": "x"}
    ) as run:
        run.log_metrics({"accuracy": 0.5})
        run.log_dict({"ok": True}, "extra.json")
        run_id = run.run_id

    fetched = MlflowClient().get_run(run_id)
    params = set(fetched.data.params)
    tags = set(fetched.data.tags)

    assert params >= REQUIRED_PARAMS
    assert tags >= REQUIRED_TAGS
    assert fetched.data.params["custom.param"] == "x"
    assert params >= {
        "data.train.sha256",
        "data.train.rows",
        "data.eval.sha256",
        "data.eval.label.joy",
        "data.train.lang.hi",
    }
    assert fetched.data.metrics["accuracy"] == pytest.approx(0.5)
    assert fetched.data.tags["manovia.phase"] == "test"

    artefacts = {item.path for item in MlflowClient().list_artifacts(run_id)}
    assert {"config.json", "environment.json", "data.json", "extra.json"} <= artefacts


def test_environment_artifact_records_libraries_and_git(
    toy_config: TrainConfig, ml_tracking: str, tmp_path: Path
) -> None:
    with tracked_run(toy_config, phase="test") as run:
        run_id = run.run_id

    path = mlflow.artifacts.download_artifacts(
        run_id=run_id, artifact_path="environment.json", dst_path=str(tmp_path)
    )
    payload = json.loads(Path(str(path)).read_text(encoding="utf-8"))

    assert payload["libraries"]["scikit-learn"] != ""
    assert "commit" in payload["git"]
    assert payload["python"].startswith("3.")


def test_tracked_run_can_nest_an_eval_run_under_a_training_run(
    toy_config: TrainConfig, ml_tracking: str
) -> None:
    with tracked_run(toy_config, phase="train") as parent:
        parent_id = parent.run_id
        with tracked_run(toy_config, phase="eval", parent_run_id=parent_id) as child:
            child_id = child.run_id

    fetched = MlflowClient().get_run(child_id)

    assert fetched.data.tags["mlflow.parentRunId"] == parent_id
    assert fetched.data.tags["manovia.eval_of"] == parent_id


def test_git_state_is_honest_when_there_is_no_checkout(tmp_path: Path) -> None:
    """A run that cannot name a commit must say so, not leave the field blank."""

    state = git_state(tmp_path)

    assert state["commit"].startswith("unavailable")
    assert state["dirty"] == "true"


def test_git_state_reports_this_repository() -> None:
    state = git_state()

    assert len(state["commit"]) == 40
    assert state["dirty"] in {"true", "false"}


def test_library_versions_cover_the_libraries_that_move_numbers(
    toy_config: TrainConfig, ml_tracking: str
) -> None:
    versions = library_versions()

    assert versions["scikit-learn"] not in {"", "absent"}
    assert versions["numpy"] not in {"", "absent"}
    assert versions["dvc"] not in {"", "absent"}


def test_params_are_flattened_and_truncated(toy_config: TrainConfig, ml_tracking: str) -> None:
    long_value = "x" * 900

    with tracked_run(toy_config, phase="test", params={"long": long_value}) as run:
        run_id = run.run_id

    stored = MlflowClient().get_run(run_id).data.params["long"]

    assert len(stored) <= 500
    assert stored.endswith("...")


def test_raw_toy_split_stage_writes_hashes_a_run_can_quote(tmp_path: Path) -> None:
    manifest = build_splits(out_dir=tmp_path, seed=20261013, train_fraction=0.7, prefix="t")

    assert len(manifest.train.sha256) == 64
    assert manifest.train.labels == {
        "joy": 10,
        "sadness": 10,
        "anger": 10,
        "fear": 10,
        "calm": 10,
        "neutral": 10,
    }
