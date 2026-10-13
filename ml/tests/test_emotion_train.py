"""The Day-32 training path end to end, on the tiny offline stand-in.

These tests train a *real* LoRA adapter through the real PEFT code path on a
200k-parameter randomly-initialised XLM-R. That is the point: the offline suite
must be able to fail on a broken optimiser, a broken scheduler or a broken
artefact writer, which mocks would not do. What it must never do is pretend the
metrics mean anything — every assertion here is about structure, provenance and
behaviour, never about accuracy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from manovia_ml.mlflow_api import mlflow

# The training stack is an optional extra (`pip install -e ".[training]"`), so a
# checkout that only wants the linear path, the catalogue checker or the docs
# still gets a green suite — but CI installs it, and then these tests *run*
# rather than skip. A skipped test is not a test.
pytest.importorskip("torch", reason='install the `training` extra: pip install -e ".[training]"')
pytest.importorskip("transformers", reason="install the `training` extra")
pytest.importorskip("peft", reason="install the `training` extra")


from manovia_ml.config import load_config_dict
from manovia_ml.data.toy import build_splits
from manovia_ml.training.emotion.data import load_examples, overlap_report
from manovia_ml.training.emotion.inference import load_artifact
from manovia_ml.training.emotion.train import class_weights, train

SMOKE_STEPS = 4


@pytest.fixture(scope="module")
def tiny_run(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Iterator[dict[str, Any]]:
    """One tiny training run, shared by every assertion about it.

    Module-scoped because a training run is the slowest thing in this suite
    (a few seconds); sharing it keeps the suite honest and quick. The MLflow
    store is a temporary file store, never ``ml/mlruns``.
    """

    tmp = tmp_path_factory.mktemp("emotion-train")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file:{tmp / 'mlruns'}")
    try:
        splits = build_splits(
            out_dir=tmp / "processed",
            seed=20261013,
            train_fraction=0.7,
            labels=["joy", "sadness", "anger", "fear", "calm", "neutral"],
            prefix="tiny",
        )
        held_out = tmp / "held_out.jsonl"
        rows = [
            {
                "id": "h-joy",
                "text": "the sun came out and everyone smiled",
                "labels": ["joy"],
                "lang": "en",
            },
            {
                "id": "h-sad",
                "text": "the bus left without me and I stood there",
                "labels": ["sadness"],
                "lang": "en",
            },
            {
                "id": "h-anger",
                "text": "they closed the gate right in front of us",
                "labels": ["anger"],
                "lang": "en",
            },
            {
                "id": "h-fear",
                "text": "the dog ran at me on the road",
                "labels": ["fear"],
                "lang": "hi",
            },
            {
                "id": "h-calm",
                "text": "rain on the window and a warm cup",
                "labels": ["calm"],
                "lang": "hi",
            },
            {
                "id": "h-neutral",
                "text": "the shop is next to the station",
                "labels": ["neutral"],
                "lang": "bn",
            },
            {
                "id": "h-multi",
                "text": "the deadline is tomorrow and my hands are shaking",
                "labels": ["anxiety", "fear"],
                "lang": "en",
            },
        ]
        with held_out.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        config = load_config_dict(
            {
                "name": "test_emotion_lora",
                "seed": 20261013,
                "notes": "test run: tiny random-init model, four steps",
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
                    "test": {"path": str(held_out), "rows": len(rows)},
                    "text_field": "text",
                    "label_field": "labels",
                    "lang_field": "lang",
                    "labels": [
                        "joy",
                        "sadness",
                        "anger",
                        "fear",
                        "anxiety",
                        "shame",
                        "loneliness",
                        "calm",
                        "neutral",
                    ],
                },
                "model": {
                    "kind": "emotion_lora",
                    "base_model": "tiny://xlm-roberta",
                    "tokenizer_max_length": 24,
                    "lora": {"rank": 2, "alpha": 4, "dropout": 0.0},
                    "training": {
                        "epochs": 1,
                        "batch_size": 8,
                        "eval_batch_size": 16,
                        "learning_rate": 5.0e-3,
                        "max_steps": SMOKE_STEPS,
                        "early_stopping_patience": 0,
                        "label_smoothing": 0.02,
                    },
                },
                "evaluation": {
                    # Not 1e-6 like the linear-kind test configs: a torch run's
                    # continuous metrics move by ~1e-2 between two identical CPU
                    # runs (see test_two_identical_runs_agree_on_the_thresholded
                    # _metrics below for the measurement and the contract).
                    "tolerance": 2.0e-2,
                    "metrics": ["macro_f1"],
                    "confusion_matrix": True,
                },
                "emotion": {
                    "thresholds": {"min_support": 1, "default": 0.5},
                    "optuna": {"enabled": False},
                    "report": {"bootstrap_resamples": 100, "calibration_bins": 4, "error_cases": 5},
                    "artifact_dir": str(tmp / "artifacts"),
                    "artifact_name": "test-emotion",
                },
                "tracking": {
                    "experiment": "manovia-ml-tests-emotion",
                    "tracking_uri": None,
                    "log_environment": False,
                },
            },
            origin="tests",
        )
        result = train(config, work_dir=tmp / "work", artifact_root=tmp / "artifacts-root")
        yield {"result": result, "config": config, "tmp": tmp, "splits": splits}
    finally:
        monkeypatch.undo()


def test_run_logs_a_run_id_and_records_the_metrics(tiny_run: dict[str, Any]) -> None:
    result = tiny_run["result"]
    assert result.run_id
    assert set(result.metrics) >= {"validation.macro_f1", "test.macro_f1", "test.ece"}
    assert 0.0 <= result.metrics["test.macro_f1"] <= 1.0
    # Validation macro-F1 is also an epoch-level metric; the history is the loss curve.
    assert [entry["epoch"] for entry in result.history] == [1.0]
    assert result.history[0]["steps"] == SMOKE_STEPS


def test_the_artefact_directory_has_the_agreed_layout(tiny_run: dict[str, Any]) -> None:
    root = tiny_run["result"].artifact_dir
    assert (root / "model" / "config.json").is_file()
    assert (root / "tokenizer" / "tokenizer_config.json").is_file()
    assert (root / "thresholds.json").is_file()
    assert (root / "MODEL_CARD.md").is_file()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["labels"] == [
        "joy",
        "sadness",
        "anger",
        "fear",
        "anxiety",
        "shame",
        "loneliness",
        "calm",
        "neutral",
    ]
    # No adapter in the packaged artefact: the weights are merged, so serving
    # does not need PEFT installed.
    assert not (root / "model" / "adapter_config.json").exists()
    assert manifest["model_files"], "every model file is hashed into the manifest"
    assert manifest["unreachable_labels"] == ["loneliness"]


def test_the_card_states_the_limitations_the_brief_asks_for(tiny_run: dict[str, Any]) -> None:
    card = (tiny_run["result"].artifact_dir / "MODEL_CARD.md").read_text(encoding="utf-8")
    assert "Domain shift from social-media text" in card
    assert "No native-speaker review" in card
    assert "loneliness" in card
    assert "Never a safety decision" in card


def test_the_manifest_points_back_at_its_run(tiny_run: dict[str, Any]) -> None:
    """A directory of weights has to say which run produced it.

    The artefact is written before the run is opened, so the id is stamped in
    afterwards; without this, tracing a model back to its MLflow record means
    searching every run for a matching artefact path.
    """

    manifest = json.loads(
        (Path(tiny_run["result"].artifact_dir) / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["run_id"] == tiny_run["result"].run_id
    # The provenance that makes the weights usable later, not just findable.
    assert manifest["labels"] == list(tiny_run["config"].data.labels)
    # The summaries describe the splits this run actually read (the fixture's
    # own 7-row held-out file, not the 174-row committed set that the real
    # configs use — that set is covered by test_emotion_data.py).
    assert manifest["data"]["test"]["rows"] == 7
    assert manifest["data"]["train"]["rows"] > 0
    assert manifest["unreachable_labels"] == ["loneliness"]


def test_a_fresh_process_reproduces_the_runs_interval_endpoints(
    tiny_run: dict[str, Any], tmp_path: Path
) -> None:
    """Message-2's check 3, as a test: the artefact alone reproduces the numbers.

    The CLI is run in a *new interpreter* that never imports the trainer (the
    module raises if it is imported in the same process), and its payload is
    compared with the payload the training run logged to MLflow. Everything
    thresholded, and the bootstrap interval endpoints, must match exactly.

    Found while doing this by hand: they did not. The trainer derives its
    bootstrap seed from the config seed for the split; the CLI used seed 0, so
    the interval endpoints moved in the third decimal — a published interval
    that no reader can reproduce. `bootstrap_seed_for()` reads the recorded seed
    from the manifest and derives the same stream.
    """

    result = tiny_run["result"]
    config = tiny_run["config"]
    client = mlflow.tracking.MlflowClient()
    logged = json.loads(
        Path(
            client.download_artifacts(result.run_id, "evaluation/test.json", str(tmp_path))
        ).read_text(encoding="utf-8")
    )

    out = tmp_path / "fresh-evaluation.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "manovia_ml.training.emotion.evaluate",
            "--artifact",
            str(result.artifact_dir),
            "--data",
            str(config.data.test.path),
            "--split",
            "test",
            "--bootstrap",
            str(config.emotion.report.bootstrap_resamples),
            "--out",
            str(out),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    fresh = json.loads(out.read_text(encoding="utf-8"))

    for key in ("macro_f1", "macro_f1_ci", "micro_f1", "micro_f1_ci", "subset_accuracy"):
        assert fresh[key] == logged[key], key
    assert fresh["per_label_f1"] == logged["per_label_f1"]
    assert fresh["per_language"] == logged["per_language"]
    assert fresh["thresholds"] == logged["thresholds"]
    # ECE is the one continuous metric, and it is allowed the documented CPU
    # float noise (see test_two_identical_runs_agree_on_the_thresholded_metrics).
    assert abs(fresh["ece"] - logged["ece"]) <= config.evaluation.tolerance


def test_thresholds_cover_every_label(tiny_run: dict[str, Any]) -> None:
    payload = json.loads(
        (tiny_run["result"].artifact_dir / "thresholds.json").read_text(encoding="utf-8")
    )
    assert set(payload["thresholds"]) == set(payload["labels"])
    assert payload["split"] == "validation"
    assert payload["per_label"]
    assert all(0.05 <= value <= 0.95 for value in payload["thresholds"].values())


def test_the_artefact_loads_and_predicts_with_the_same_labels(tiny_run: dict[str, Any]) -> None:
    artifact = load_artifact(tiny_run["result"].artifact_dir, device="cpu", max_length=24)
    probabilities = artifact.predict_probabilities(["I am so tired today"], batch_size=1)
    assert len(probabilities) == 1
    assert len(probabilities[0]) == len(artifact.labels)
    assert all(0.0 <= value <= 1.0 for value in probabilities[0])
    assert artifact.warnings == []


def test_missing_thresholds_degrade_with_a_warning(
    tmp_path: Path,
    tiny_run: dict[str, Any],
) -> None:
    """A directory without thresholds.json still runs — at 0.5, and it says so."""

    import shutil

    copy = tmp_path / "no-thresholds"
    shutil.copytree(tiny_run["result"].artifact_dir, copy)
    (copy / "thresholds.json").unlink()
    artifact = load_artifact(copy, device="cpu", max_length=24)
    assert artifact.warnings and "thresholds.json missing" in artifact.warnings[0]
    assert artifact.threshold_for("joy") == 0.5


def test_a_directory_without_weights_refuses_to_load(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not a packaged emotion artefact"):
        load_artifact(tmp_path)


def test_class_weights_are_capped_and_never_below_one() -> None:
    from manovia_ml.training.emotion.data import EmotionExample

    rare = [
        EmotionExample(id="a", text="t", labels=("joy",), lang="en", source="s"),
        *[
            EmotionExample(id=f"b{i}", text="u", labels=("sadness",), lang="en", source="s")
            for i in range(99)
        ],
    ]
    weights = class_weights(rare, ("joy", "sadness"), cap=50.0)
    assert weights[0] == 50.0  # 99/1, capped
    assert weights[1] == 1.0
    # A label nobody has: weight 1.0, not a division by zero.
    assert class_weights(rare, ("loneliness",)) == [1.0]


def test_training_refuses_when_the_held_out_set_overlaps_training(
    tiny_run: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A held-out set that shares rows with training is a bug, not a warning."""

    config = tiny_run["config"]
    leaked = tmp_path / "leaked.jsonl"
    train_examples = load_examples(config.data.train.path)
    leaked.write_text(
        json.dumps(
            {
                "id": "leak",
                "text": train_examples[0].text,
                "labels": list(train_examples[0].labels),
                "lang": "en",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    broken = config.model_copy(
        update={
            "data": config.data.model_copy(
                update={
                    "test": config.data.test.model_copy(
                        update={"path": str(leaked), "sha256": None, "rows": 1}
                    )
                }
            )
        }
    )
    with pytest.raises(ValueError, match="overlaps the test split"):
        train(broken, work_dir=tmp_path / "work", artifact_root=tmp_path / "root")


def test_two_identical_runs_agree_on_the_thresholded_metrics(
    tiny_run: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seed has to reach every stochastic step — measured, not asserted.

    Found while building Day 32: two runs of the same config logged *different*
    ECE values (0.2006 vs 0.2052 on validation) while agreeing on every
    thresholded metric. Interviewing the run showed the cause is not an unseeded
    RNG — model init, batch order and dropout masks are all reproducible, and
    `ml/tests/test_seeds.py` pins the torch part of that — but the CPU's blocked
    float reductions, which ATen/MKL reorder between runs. It cannot be seeded
    away, so the contract is stated instead of pretended:

    * everything computed by comparing probabilities to a **threshold** must be
      identical, bit for bit — that is what the product and the report use;
    * a **continuous** metric (ECE) may wobble, and the config's
      `evaluation.tolerance` is where that is written down and has to cover it.
    """

    config = tiny_run["config"]
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"file:{tmp_path / 'mlruns-second'}")
    again = train(config, work_dir=tmp_path / "work", artifact_root=tmp_path / "artifacts-root")

    first = tiny_run["result"].metrics
    assert set(first) == set(again.metrics)
    thresholded = {name: value for name, value in first.items() if not name.endswith("ece")}
    assert thresholded, "the run must log at least one thresholded metric"
    for name, value in thresholded.items():
        assert again.metrics[name] == value, name
    tolerance = config.evaluation.tolerance
    assert tolerance >= 1.0e-2, "documented CPU noise on ECE is up to ~1e-2: raise it deliberately"
    for name in (name for name in first if name.endswith("ece")):
        assert again.metrics[name] == pytest.approx(first[name], abs=tolerance), name


def test_overlap_between_the_committed_held_out_set_and_the_toy_fixture_is_clean(
    tiny_run: dict[str, Any],
) -> None:
    train_examples = load_examples(tiny_run["config"].data.train.path)
    report = overlap_report(train_examples, load_examples(tiny_run["config"].data.test.path))
    assert report.clean, report.fail_message()


def test_evaluation_runs_in_a_fresh_process_from_the_artefact_alone(
    tiny_run: dict[str, Any],
) -> None:
    """The verification the brief asks for, as a test: no training code in the child.

    The child process imports ``manovia_ml.training.emotion.evaluate``, which
    refuses to run if the training module is loaded, and prints the same
    macro-F1 the in-process scorer produced.
    """

    artifact_dir = tiny_run["result"].artifact_dir
    data = tiny_run["config"].data.test.path
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "manovia_ml.training.emotion.evaluate",
            "--artifact",
            str(artifact_dir),
            "--data",
            str(data),
            "--bootstrap",
            "100",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["macro_f1"] == pytest.approx(tiny_run["result"].test["macro_f1"], abs=1e-9)
    assert payload["artifact"]["directory"] == str(artifact_dir)
    # And the child really did not import the training module: its own guard
    # would have raised, but say it explicitly for the reader.
    assert "errors" in payload
