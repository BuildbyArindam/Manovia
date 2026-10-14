"""ONNX runtime: real session, batching, warm-up, timeout fallback, checksum.

The artefact is a real (tiny) ONNX graph built in the test with the
``onnx`` package — no committed weights (AGENTS.md ML rule 3). Without
the serving extra (CI's ``[dev]``-only backend job) the whole file skips,
like the torch-dependent suites; with it, these run for real.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
pytest.importorskip("numpy")

import numpy as np

from app.services.ml_runtime.classifier import OnnxSafetyClassifier
from app.services.ml_runtime.registry import LoadedModel
from app.services.ml_runtime.runtime import OnnxModelRuntime
from app.services.safety.base import RiskLevel

LABELS = ["none", "low", "medium", "high", "imminent"]
_SPEC = {
    "version": 1,
    "dim": 64,
    "word_ngram_max": 2,
    "char_ngram_min": 3,
    "char_ngram_max": 5,
    "max_length_chars": 2000,
}


def _write_model(directory: Path, *, seed: int) -> LoadedModel:
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    weights = (rng.standard_normal((64, 5)) * 0.5).astype(np.float32)
    # Make "end/life"-ish rows predictable enough: the classifier test only
    # needs a valid distribution, not accuracy.
    bias = np.zeros(5, dtype=np.float32)
    graph = helper.make_graph(
        nodes=[
            helper.make_node("Gemm", ["features", "W", "b"], ["logits"]),
            helper.make_node("Softmax", ["logits"], ["probabilities"], axis=1),
        ],
        name="test-student",
        inputs=[helper.make_tensor_value_info("features", TensorProto.FLOAT, ["batch", 64])],
        outputs=[
            helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, ["batch", 5]),
            helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", 5]),
        ],
        initializer=[numpy_helper.from_array(weights, "W"), numpy_helper.from_array(bias, "b")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(directory / "model.onnx"))
    manifest = {
        "format_version": 1,
        "task": "safety",
        "labels": LABELS,
        "featurizer": _SPEC,
        "model_file": "model.onnx",
        "sha256": hashlib.sha256((directory / "model.onnx").read_bytes()).hexdigest(),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return LoadedModel(
        model_name="safety-student",
        version=directory.name,
        directory=directory,
        model_path=directory / "model.onnx",
        manifest=manifest,
    )


def test_runtime_predicts_and_warms_up(tmp_path: Path) -> None:
    runtime = OnnxModelRuntime(_write_model(tmp_path / "v1", seed=1), timeout_seconds=5.0)
    assert runtime.warmup() is True
    prediction = runtime.predict("i feel hopeless today")
    assert len(prediction.probabilities) == 5
    assert sum(prediction.probabilities) == pytest.approx(1.0, abs=1e-5)
    assert prediction.latency_ms >= 0.0
    assert runtime.describe()["warmed"] is True
    runtime.close()


def test_runtime_batches_many_texts(tmp_path: Path) -> None:
    runtime = OnnxModelRuntime(_write_model(tmp_path / "v1", seed=2), timeout_seconds=5.0)
    rows = runtime.predict_many(["one message", "another message", "a third"])
    assert len(rows) == 3
    assert all(sum(row.probabilities) == pytest.approx(1.0, abs=1e-5) for row in rows)
    runtime.close()


def test_runtime_falls_back_to_previous_version(tmp_path: Path) -> None:
    primary = _write_model(tmp_path / "v2", seed=3)
    fallback = _write_model(tmp_path / "v1", seed=4)
    # Corrupt the primary's bytes after manifest creation: session load fails.
    primary.model_path.write_bytes(b"not an onnx model")
    runtime = OnnxModelRuntime(primary, fallback=fallback, timeout_seconds=5.0)
    prediction = runtime.predict("anything at all")
    assert prediction.used_fallback is True
    assert prediction.model_version == "v1"
    runtime.close()


def test_classifier_adapter_speaks_ml_prediction(tmp_path: Path) -> None:
    runtime = OnnxModelRuntime(_write_model(tmp_path / "v1", seed=5), timeout_seconds=5.0)
    classifier = OnnxSafetyClassifier(runtime, labels=tuple(LABELS))
    prediction = classifier.predict("i feel a bit low today")
    assert prediction is not None
    assert set(prediction.probabilities) == set(LABELS)
    assert prediction.level in tuple(RiskLevel)
    assert classifier.predict("   ") is None
    runtime.close()


def test_onnx_choice_falls_back_to_baseline_without_registry(settings) -> None:  # type: ignore[no-untyped-def]
    """SAFETY_ML=onnx with no registry: baseline + recorded reason, never a crash."""

    from app.services.safety.ml_classifier import build_safety_classifier

    settings.safety_ml_enabled = True
    settings.safety_ml = "onnx"
    settings.ml_runtime_registry_dir = ""
    classifier = build_safety_classifier(settings)
    assert classifier.enabled
    assert "onnx-unavailable" in classifier.version
