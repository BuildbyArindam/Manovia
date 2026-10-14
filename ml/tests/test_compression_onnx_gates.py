"""Day 34: ONNX export, INT8 quantisation and the release gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from manovia_ml.compression.distill import distill
from manovia_ml.compression.gates import evaluate_gates, load_release_gates
from manovia_ml.compression.onnx_export import (
    evaluate_onnx,
    export_student,
    write_manifest,
)
from manovia_ml.compression.pipeline import run_pipeline
from manovia_ml.config import DistillConfig


def _config(tmp_path: Path) -> DistillConfig:
    rows = [
        {"text": "i want to end my life tonight", "label": "IMMINENT", "lang": "en"},
        {"text": "i have a plan to hurt myself", "label": "HIGH", "lang": "en"},
        {"text": "everything feels hopeless and heavy", "label": "MEDIUM", "lang": "en"},
        {"text": "i slept badly and feel tired", "label": "LOW", "lang": "en"},
        {"text": "the train was late and i read a book", "label": "NONE", "lang": "en"},
    ] * 6
    train = tmp_path / "train.jsonl"
    held = tmp_path / "eval.jsonl"
    train.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    held.write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in [
                {"text": "i want to end my life", "label": "IMMINENT", "lang": "en"},
                {"text": "i have a plan to hurt myself tonight", "label": "HIGH", "lang": "en"},
                {"text": "the bus was late and i read", "label": "NONE", "lang": "en"},
                {"text": "i slept badly", "label": "LOW", "lang": "en"},
            ]
        ),
        encoding="utf-8",
    )
    return DistillConfig.model_validate(
        {
            "name": "test_onnx",
            "task": "safety",
            "seed": 7,
            "teacher_kind": "sklearn_tfidf",
            "data": {
                "train": {"path": str(train)},
                "eval": {"path": str(held)},
                "labels": ["none", "low", "medium", "high", "imminent"],
            },
            "student": {"kind": "linear_hash", "feature_dim": 512},
            "training": {"temperature": 2.0, "alpha": 0.7, "epochs": 40, "learning_rate": 0.5},
            "tracking": {"experiment": "test", "tracking_uri": None, "log_environment": False},
        }
    )


def test_quantised_model_passes_gates_in_fixture(tmp_path: Path) -> None:
    """The brief's fixture test: INT8 on the frozen eval passes every gate."""

    payload = run_pipeline(_config(tmp_path), out_dir=tmp_path / "candidate")
    assert payload["gates"]["passed"], payload["gates"]
    assert payload["int8"]["crisis_recall"] >= payload["fp32"]["crisis_recall"] - 0.02
    assert (tmp_path / "candidate" / "compression_report.md").is_file()
    assert (tmp_path / "candidate" / "model.int8.onnx").is_file()


def test_onnx_matches_numpy_student(tmp_path: Path) -> None:
    config = _config(tmp_path)
    result = distill(config, out_dir=tmp_path / "student")
    onnx_path = export_student(result.student, tmp_path / "model.onnx")
    texts = ["i want to end my life", "the train was late"]
    expected = result.student.predict(texts)
    metrics = evaluate_onnx(
        onnx_path,
        featurizer=result.student.featurizer,
        labels=result.student.labels,
        texts=texts,
        truths=["imminent", "none"],
        task="safety",
    )
    assert metrics["accuracy"] == pytest.approx(1.0)
    assert expected == ["imminent", "none"]


def test_gates_reject_deliberately_degraded_model() -> None:
    """A candidate whose INT8 crisis recall collapsed must be rejected."""

    gates = load_release_gates()
    degraded = {
        "safety_crisis_recall": 0.40,
        "safety_accuracy": 0.50,
        "teacher_safety_crisis_recall": 0.95,
        "fp32_safety_crisis_recall": 0.95,
        "p50_latency_ms": 1.0,
        "p95_latency_ms": 2.0,
        "student_size_mb": 0.01,
        "teacher_size_mb": 1.0,
        "int8_size_mb": 0.005,
        "fp32_size_mb": 0.02,
    }
    report = evaluate_gates(degraded, gates, task="safety")
    assert not report.passed
    failed = {check.name for check in report.failures}
    assert "safety.min_crisis_recall" in failed
    assert "safety.max_crisis_recall_drop_int8_vs_fp32" in failed


def test_gates_fail_closed_on_missing_metrics() -> None:
    report = evaluate_gates({}, load_release_gates(), task="safety")
    assert not report.passed


def test_manifest_checksum_detects_corruption(tmp_path: Path) -> None:
    from manovia_ml.compression.registry import ChecksumError, verify_artifact

    config = _config(tmp_path)
    result = distill(config, out_dir=tmp_path / "student")
    export_student(result.student, tmp_path / "model.onnx")
    write_manifest(
        tmp_path,
        task="safety",
        labels=result.student.labels,
        featurizer=result.student.featurizer,
        model_filename="model.onnx",
        teacher_kind="sklearn_tfidf",
        student_kind="linear_hash",
    )
    assert verify_artifact(tmp_path)["task"] == "safety"
    model = tmp_path / "model.onnx"
    model.write_bytes(model.read_bytes() + b"corrupt")
    with pytest.raises(ChecksumError, match="refusing to load"):
        verify_artifact(tmp_path)
