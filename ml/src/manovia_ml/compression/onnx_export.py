"""ONNX export + INT8 dynamic quantisation for a distilled student.

The linear student's whole computation is ``softmax(features @ W + b)``,
so the exported graph is exactly that: a ``Gemm`` followed by a
``Softmax``. No featurisation lives in the graph — the hashed-feature
spec travels in the manifest and both scorers (here and the backend
runtime) rebuild it, pinned by golden vectors (see ``featurizer.py``).

``onnx`` and ``onnxruntime`` are optional (the ``serving`` extra): every
function that needs them imports lazily and raises :class:`ExportError`
with the install command, so importing this module never fails in a
pydantic-only environment.

Dynamic quantisation here is ONNX Runtime's ``quantize_dynamic`` with
INT8 weights. On the tiny fixture graph the quantised file can be *larger*
than fp32 (quantised-operator overhead dominates a 20 KB graph); the
report prints both sizes and the size gate allows that explicitly
(``max_int8_fp32_ratio`` in ``release_gates.yaml``) instead of pretending
quantisation always shrinks a model.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from manovia_ml.compression.distill import LinearStudent, _task_metrics
from manovia_ml.compression.featurizer import HashingFeaturizer
from manovia_ml.hashing import sha256_file

__all__ = [
    "ExportError",
    "evaluate_onnx",
    "export_student",
    "file_size_mb",
    "quantize_onnx",
    "write_manifest",
]


class ExportError(RuntimeError):
    """Export/quantisation cannot run here (missing extra, bad artefact)."""


def file_size_mb(path: Path) -> float:
    return Path(path).stat().st_size / (1024 * 1024)


def export_student(student: LinearStudent, out_path: Path) -> Path:
    """Write the fp32 ONNX graph for ``student`` and return its path."""

    try:
        import onnx
        from onnx import TensorProto, helper, numpy_helper
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise ExportError(
            "ONNX export needs the serving extra: pip install -e './ml[serving]'"
        ) from exc

    weights = np.ascontiguousarray(student.weights.astype(np.float32))
    bias = np.ascontiguousarray(student.bias.astype(np.float32))
    dim, n_classes = weights.shape
    graph = helper.make_graph(
        nodes=[
            helper.make_node(
                "Gemm", ["features", "W", "b"], ["logits"], transB=0, alpha=1.0, beta=1.0
            ),
            helper.make_node("Softmax", ["logits"], ["probabilities"], axis=1),
        ],
        name="manovia-student",
        inputs=[
            helper.make_tensor_value_info("features", TensorProto.FLOAT, ["batch", dim]),
        ],
        outputs=[
            helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, ["batch", n_classes]),
            helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", n_classes]),
        ],
        initializer=[numpy_helper.from_array(weights, "W"), numpy_helper.from_array(bias, "b")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(out_path))
    return out_path


def quantize_onnx(fp32_path: Path, out_path: Path) -> Path:
    """INT8 dynamic quantisation of the fp32 graph (weights only)."""

    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise ExportError(
            "INT8 quantisation needs the serving extra: pip install -e './ml[serving]'"
        ) from exc
    out_path.parent.mkdir(parents=True, exist_ok=True)
    quantize_dynamic(
        model_input=str(fp32_path),
        model_output=str(out_path),
        weight_type=QuantType.QInt8,
    )
    return out_path


class _OnnxScorer:
    """One loaded ONNX model: featurize → session.run → probabilities."""

    def __init__(self, path: Path, featurizer: HashingFeaturizer) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - without the extra
            raise ExportError(
                "ONNX scoring needs the serving extra: pip install -e './ml[serving]'"
            ) from exc
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._featurizer = featurizer

    def predict_proba(self, texts: Sequence[str]) -> np.ndarray:
        features = self._featurizer.transform(texts)
        outputs = self._session.run(["probabilities"], {"features": features})
        return np.asarray(outputs[0], dtype=np.float64)

    def predict(self, texts: Sequence[str], labels: Sequence[str]) -> list[str]:
        probabilities = self.predict_proba(texts)
        return [labels[int(i)] for i in np.argmax(probabilities, axis=1)]


def evaluate_onnx(
    path: Path,
    *,
    featurizer: HashingFeaturizer,
    labels: Sequence[str],
    texts: Sequence[str],
    truths: Sequence[str],
    task: str,
) -> dict[str, Any]:
    """Frozen-eval metrics + per-message latency for one ONNX file."""

    scorer = _OnnxScorer(path, featurizer)
    predictions = scorer.predict(texts, labels)
    metrics: dict[str, Any] = _task_metrics(task, truths, predictions, labels)
    samples: list[float] = []
    for text in texts:
        started = time.perf_counter()
        scorer.predict_proba([text])
        samples.append((time.perf_counter() - started) * 1000.0)
    ordered = sorted(samples)
    metrics["p50_latency_ms"] = float(ordered[len(ordered) // 2]) if ordered else 0.0
    metrics["p95_latency_ms"] = (
        float(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]) if ordered else 0.0
    )
    metrics["size_mb"] = file_size_mb(path)
    return metrics


def write_manifest(
    directory: Path,
    *,
    task: str,
    labels: Sequence[str],
    featurizer: HashingFeaturizer,
    model_filename: str,
    teacher_kind: str,
    student_kind: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    """The manifest the registry and the backend loader both read.

    ``sha256`` is over the model file's bytes; a loader that does not
    verify it has not loaded the model it thinks it has (Day 34 gate).
    """

    model_path = directory / model_filename
    manifest: dict[str, Any] = {
        "format_version": 1,
        "task": task,
        "labels": list(labels),
        "featurizer": featurizer.spec(),
        "model_file": model_filename,
        "sha256": sha256_file(model_path),
        "size_mb": file_size_mb(model_path),
        "teacher_kind": teacher_kind,
        "student_kind": student_kind,
    }
    if extra:
        manifest.update(extra)
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
