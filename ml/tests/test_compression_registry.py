"""Day 34 registry: register → promote (gated) → rollback restores."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from manovia_ml.compression.distill import distill
from manovia_ml.compression.onnx_export import export_student, write_manifest
from manovia_ml.compression.registry import (
    GateRejectedError,
    RegistryManager,
)


def _artifact(tmp_path: Path, name: str, seed: int) -> Path:
    from manovia_ml.config import DistillConfig

    rows = [
        {"text": "i want to end my life tonight", "label": "IMMINENT", "lang": "en"},
        {"text": "i have a plan to hurt myself", "label": "HIGH", "lang": "en"},
        {"text": "the train was late and i read a book", "label": "NONE", "lang": "en"},
    ] * 4
    data = tmp_path / f"{name}.jsonl"
    data.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    config = DistillConfig.model_validate(
        {
            "name": name,
            "task": "safety",
            "seed": seed,
            "teacher_kind": "sklearn_tfidf",
            "data": {
                "train": {"path": str(data)},
                "eval": {"path": str(data)},
                "labels": ["none", "low", "medium", "high", "imminent"],
            },
            "student": {"kind": "linear_hash", "feature_dim": 256},
            "training": {"epochs": 20, "learning_rate": 0.5},
            "tracking": {"experiment": "test", "tracking_uri": None, "log_environment": False},
        }
    )
    result = distill(config)
    out = tmp_path / name
    out.mkdir()
    export_student(result.student, out / "model.onnx")
    write_manifest(
        out,
        task="safety",
        labels=result.student.labels,
        featurizer=result.student.featurizer,
        model_filename="model.onnx",
        teacher_kind="sklearn_tfidf",
        student_kind="linear_hash",
    )
    return out


def test_promote_requires_gates_and_rollback_restores(tmp_path: Path) -> None:
    manager = RegistryManager(
        tracking_uri=f"file:{tmp_path / 'mlruns'}", registry_dir=tmp_path / "registry"
    )
    manager.register("safety-student", _artifact(tmp_path, "v1", 1), version="v1")
    # No gate evidence: promotion is refused, not assumed.
    with pytest.raises(GateRejectedError):
        manager.promote("safety-student", "v1")
    with pytest.raises(GateRejectedError):
        manager.promote("safety-student", "v1", gates_passed=False)

    aliases = manager.promote("safety-student", "v1", gates_passed=True)
    assert aliases["production"] == "v1"
    assert manager.resolve("safety-student").name == "v1"

    manager.register("safety-student", _artifact(tmp_path, "v2", 2), version="v2")
    aliases = manager.promote("safety-student", "v2", gates_passed=True)
    assert aliases["production"] == "v2"
    assert aliases["previous_production"] == "v1"
    assert "v1" in aliases["archived"]

    rolled = manager.rollback("safety-student")
    assert rolled["production"] == "v1", "rollback must restore the previous version"
    assert manager.resolve("safety-student").name == "v1"


def test_register_refuses_corrupt_artifact(tmp_path: Path) -> None:
    from manovia_ml.compression.registry import ChecksumError

    manager = RegistryManager(
        tracking_uri=f"file:{tmp_path / 'mlruns'}", registry_dir=tmp_path / "registry"
    )
    artifact = _artifact(tmp_path, "bad", 3)
    model = artifact / "model.onnx"
    model.write_bytes(model.read_bytes() + b"x")
    with pytest.raises(ChecksumError):
        manager.register("safety-student", artifact, version="v9")
