"""The Optuna sweep: two real trials, two real training runs.

Slow by design (each trial trains the tiny model for a handful of steps) because
the thing worth testing is that a trial *is* a normal run: same function, same
logging, same artefact, nested under a parent run. A mocked sweep would prove
none of that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# The training stack is an optional extra (`pip install -e ".[training]"`), so a
# checkout that only wants the linear path, the catalogue checker or the docs
# still gets a green suite — but CI installs it, and then these tests *run*
# rather than skip. A skipped test is not a test.
pytest.importorskip("torch", reason='install the `training` extra: pip install -e ".[training]"')
pytest.importorskip("transformers", reason="install the `training` extra")
pytest.importorskip("peft", reason="install the `training` extra")


from manovia_ml.config import TrainConfig, load_config_dict
from manovia_ml.data.toy import build_splits
from manovia_ml.mlflow_api import mlflow
from manovia_ml.training.emotion.tune import sweep

pytest.importorskip("optuna", reason="install the `training` extra")

pytestmark = pytest.mark.slow


@pytest.fixture
def sweep_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TrainConfig:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file:{tmp_path / 'mlruns'}")
    splits = build_splits(
        out_dir=tmp_path / "processed",
        seed=20261013,
        train_fraction=0.7,
        labels=["joy", "anger"],
        prefix="tune",
    )
    held_out = tmp_path / "held_out.jsonl"
    held_out.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                # Deliberately unlike anything the generator produces, so the
                # overlap guard does not (rightly) refuse to train.
                {
                    "id": "h1",
                    "text": "the afternoon light through the window",
                    "labels": ["joy"],
                    "lang": "en",
                },
                {
                    "id": "h2",
                    "text": "they stepped in front of the whole queue",
                    "labels": ["anger"],
                    "lang": "hi",
                },
                {
                    "id": "h3",
                    "text": "the shop is beside the station",
                    "labels": ["neutral"],
                    "lang": "bn",
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return load_config_dict(
        {
            "name": "test_emotion_sweep",
            "seed": 20261013,
            "threads": 1,
            "data": {
                "train": {
                    "path": str(splits.train.path),
                    "sha256": splits.train.sha256,
                    "rows": splits.train.rows,
                },
                "eval": {
                    "path": str(splits.eval.path),
                    "sha256": splits.eval.sha256,
                    "rows": splits.eval.rows,
                },
                "validation": {
                    "path": str(splits.eval.path),
                    "sha256": splits.eval.sha256,
                    "rows": splits.eval.rows,
                },
                "test": {"path": str(held_out), "rows": 3},
                "text_field": "text",
                "label_field": "labels",
                "lang_field": "lang",
                "labels": ["joy", "anger", "neutral"],
            },
            "model": {
                "kind": "emotion_lora",
                "base_model": "tiny://xlm-roberta",
                "tokenizer_max_length": 16,
                "lora": {"rank": 2, "alpha": 4, "dropout": 0.0},
                "training": {
                    "epochs": 1,
                    "batch_size": 8,
                    "max_steps": 2,
                    "early_stopping_patience": 0,
                },
            },
            "evaluation": {"tolerance": 1.0e-6, "metrics": ["macro_f1"], "confusion_matrix": True},
            "emotion": {
                "thresholds": {"min_support": 1},
                "optuna": {
                    "enabled": True,
                    "n_trials": 2,
                    "rank_choices": [2, 4],
                    "learning_rate_low": 1.0e-3,
                    "learning_rate_high": 1.0e-2,
                    "seed": 5,
                },
                "report": {"bootstrap_resamples": 100, "calibration_bins": 4},
                "artifact_dir": str(tmp_path / "artifacts"),
                "artifact_name": "sweep-model",
            },
            "tracking": {
                "experiment": "manovia-ml-tests-sweep",
                "tracking_uri": None,
                "log_environment": False,
            },
        },
        origin="tests",
    )


def test_sweep_runs_every_trial_and_picks_a_winner(
    sweep_config: TrainConfig,
    tmp_path: Path,
) -> None:
    result = sweep(sweep_config, trials=2, work_dir=tmp_path / "work")
    assert result["sweep_run_id"]
    assert len(result["trials"]) == 2
    assert {trial["rank"] for trial in result["trials"]} <= {2, 4}
    assert all(trial["run_id"] for trial in result["trials"])
    assert result["best"]["rank"] in {2, 4}
    assert 0.0 <= result["best"]["value"] <= 1.0
    # Each trial is a normal run, so each wrote a loadable artefact.
    for trial in result["trials"]:
        assert Path(trial["artifact_dir"], "manifest.json").is_file()


def test_sweep_refuses_to_run_when_it_is_not_switched_on(sweep_config: TrainConfig) -> None:
    emotion = sweep_config.emotion
    assert emotion is not None  # the config the fixture built always has one
    off = sweep_config.model_copy(
        update={
            "emotion": emotion.model_copy(
                update={"optuna": emotion.optuna.model_copy(update={"enabled": False})}
            )
        }
    )
    with pytest.raises(ValueError, match=r"optuna\.enabled is false"):
        sweep(off, trials=1)


def test_the_parent_run_records_the_trials(sweep_config: TrainConfig, tmp_path: Path) -> None:
    result = sweep(sweep_config, trials=1, work_dir=tmp_path / "work")
    mlflow.set_tracking_uri(
        sweep_config.tracking.tracking_uri or "file:" + str(tmp_path / "mlruns")
    )
    client = mlflow.tracking.MlflowClient()
    run = client.get_run(result["sweep_run_id"])
    assert run.data.params["optuna.n_trials"] == "1"
    assert float(run.data.params["sweep.best.rank"]) in {2.0, 4.0}
    assert any(key == "trial.0.value" for key in run.data.metrics)
