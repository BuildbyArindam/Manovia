"""Day 34 distillation: loss, student, featurizer mirror, config guards."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from manovia_ml.compression.distill import (
    DistillationError,
    LinearStudent,
    distill,
    load_examples_for_task,
    softmax,
)
from manovia_ml.compression.featurizer import HashingFeaturizer
from manovia_ml.config import load_distill_config


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _toy_rows() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    train = [
        {"text": "i want to end my life tonight", "label": "IMMINENT", "lang": "en"},
        {"text": "i have a plan to hurt myself", "label": "HIGH", "lang": "en"},
        {"text": "everything feels hopeless and heavy", "label": "MEDIUM", "lang": "en"},
        {"text": "i slept badly and feel tired", "label": "LOW", "lang": "en"},
        {"text": "the train was late and i read a book", "label": "NONE", "lang": "en"},
    ] * 6
    held_out = [
        {"text": "i want to end my life", "label": "IMMINENT", "lang": "en"},
        {"text": "the bus was late and i read", "label": "NONE", "lang": "en"},
    ]
    return train, held_out


def _config(tmp_path: Path, train: Path, held_out: Path, **overrides: object) -> object:
    from manovia_ml.config import (
        DistillConfig,
        load_config_dict,  # noqa: F401  (keeps import local)
    )

    payload: dict[str, object] = {
        "name": "test_distill",
        "task": "safety",
        "seed": 7,
        "teacher_kind": "sklearn_tfidf",
        "data": {
            "train": {"path": str(train)},
            "eval": {"path": str(held_out)},
            "labels": ["none", "low", "medium", "high", "imminent"],
        },
        "student": {"kind": "linear_hash", "feature_dim": 256},
        "training": {"temperature": 2.0, "alpha": 0.7, "epochs": 30, "learning_rate": 0.5},
        "tracking": {"experiment": "test", "tracking_uri": None, "log_environment": False},
    }
    payload.update(overrides)
    return DistillConfig.model_validate(payload)


def test_featurizer_golden_vector() -> None:
    """Pinned indices: the backend mirror hard-codes the same numbers."""

    featurizer = HashingFeaturizer(dim=64, word_ngram_max=2, char_ngram_min=3, char_ngram_max=4)
    vector = featurizer.transform_one("I feel hopeless and tired")
    top = int(np.argmax(vector))
    assert top == 11
    assert float(vector[11]) == pytest.approx(0.32359, abs=1e-4)
    assert int(np.count_nonzero(vector)) == 35


def test_featurizer_is_deterministic_and_process_independent() -> None:
    featurizer = HashingFeaturizer(dim=128)
    first = featurizer.transform_one("नमस्ते, I feel tired today")
    second = HashingFeaturizer(dim=128).transform_one("नमस्ते, I feel tired today")
    assert np.array_equal(first, second)
    assert float(np.linalg.norm(first)) == pytest.approx(1.0)


def test_softmax_temperature_flattens() -> None:
    logits = np.array([[4.0, 0.0]])
    sharp = softmax(logits, temperature=1.0)
    flat = softmax(logits, temperature=4.0)
    assert flat[0, 0] < sharp[0, 0]
    assert float(flat.sum()) == pytest.approx(1.0)


def test_distill_smoke_learns_and_reports(tmp_path: Path) -> None:
    train_rows, held_rows = _toy_rows()
    config = _config(
        tmp_path,
        _write_jsonl(tmp_path / "train.jsonl", train_rows),
        _write_jsonl(tmp_path / "eval.jsonl", held_rows),
    )
    result = distill(config, out_dir=tmp_path / "out")  # type: ignore[arg-type]
    assert result.metrics["student"]["crisis_recall"] == pytest.approx(1.0)
    assert result.metrics["final_loss"] is not None
    assert (tmp_path / "out" / "student.npz").is_file()
    loaded = LinearStudent.load(tmp_path / "out" / "student.npz")
    assert loaded.predict(["i want to end my life"]) == ["imminent"]


def test_distill_rejects_transformer_in_sandbox(tmp_path: Path) -> None:
    train_rows, held_rows = _toy_rows()
    config = _config(
        tmp_path,
        _write_jsonl(tmp_path / "train.jsonl", train_rows),
        _write_jsonl(tmp_path / "eval.jsonl", held_rows),
        student={"kind": "transformer", "base_model": "microsoft/multilingual-MiniLM-L12-H384"},
    )
    with pytest.raises(DistillationError, match="colab_distill_export"):
        distill(config)  # type: ignore[arg-type]


def test_distill_rejects_missing_teacher_artifact(tmp_path: Path) -> None:
    train_rows, held_rows = _toy_rows()
    config = _config(
        tmp_path,
        _write_jsonl(tmp_path / "train.jsonl", train_rows),
        _write_jsonl(tmp_path / "eval.jsonl", held_rows),
        teacher_kind="trained_artifact",
        teacher_artifact_dir="artifacts/safety/nope",
    )
    with pytest.raises(DistillationError, match="trained_artifact"):
        distill(config)  # type: ignore[arg-type]


def test_load_examples_refuses_bad_rows(tmp_path: Path) -> None:
    bad = _write_jsonl(tmp_path / "bad.jsonl", [{"text": "", "label": "NONE"}])
    with pytest.raises(DistillationError, match="no text"):
        load_examples_for_task(bad, task="safety")


def test_committed_smoke_configs_validate() -> None:
    for name in ("distill_safety_smoke.yaml", "distill_emotion_smoke.yaml"):
        config = load_distill_config(name)
        assert config.student.kind == "linear_hash"
    real = load_distill_config("distill_safety_student.yaml")
    assert real.student.kind == "transformer"
    assert "MiniLM" in real.student.base_model
