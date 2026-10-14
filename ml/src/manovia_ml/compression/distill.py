"""Knowledge distillation: a Day-32/33 teacher into a small student.

Loss (Hinton et al.): with temperature ``T``, teacher soft targets
``q = softmax(z_teacher / T)`` and student ``p_T = softmax(z_student / T)``,

    loss = alpha * T^2 * CE(q, p_T) + (1 - alpha) * CE(y, p_1)

The ``T^2`` factor keeps the soft term's gradient scale comparable as ``T``
changes. Everything is config-resident (``DistillConfig``); the seed reaches
the one stochastic step (weight initialisation) through ``derive_seed``.

Two student kinds (see ``DistillStudentConfig``):

* ``linear_hash`` — multinomial logistic regression over hashed n-grams,
  trained here in numpy with the exact loss above. This is the offline
  smoke student: it proves distil → ONNX → INT8 → gates → registry on a
  CPU with no torch and no download. It is *not* the production student.
* ``transformer`` — a MiniLM/DistilBERT-class multilingual encoder. That
  run needs a GPU and the hub, so it lives in
  ``ml/notebooks/colab_distill_export.ipynb``; calling the trainer for it
  here refuses with those instructions instead of half-running.

The sandbox teacher (``sklearn_tfidf``) is a TF-IDF + logistic-regression
stand-in trained on the same rows, because the Day-32/33 weights do not
exist in this sandbox. Every report names the teacher kind, so a smoke
number can never be quoted as a distilled-teacher number.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from manovia_ml.compression.featurizer import HashingFeaturizer
from manovia_ml.config import DistillConfig, load_distill_config
from manovia_ml.hashing import sha256_file
from manovia_ml.paths import resolve
from manovia_ml.seeds import derive_seed
from manovia_ml.training.safety.metrics import accuracy as _accuracy
from manovia_ml.training.safety.metrics import binary_crisis_metrics, macro_f1

__all__ = [
    "DistillResult",
    "DistillationError",
    "LinearStudent",
    "TeacherProvider",
    "distill",
    "load_examples_for_task",
    "main",
    "softmax",
]


class DistillationError(RuntimeError):
    """The run cannot proceed as configured (wrong student kind, no data...)."""


class TeacherProvider(Protocol):
    """Anything that can hand out soft targets in the student's label order."""

    def predict_proba(self, texts: Sequence[str]) -> np.ndarray: ...


def softmax(logits: np.ndarray, *, temperature: float = 1.0) -> np.ndarray:
    scaled = logits / temperature
    scaled = scaled - scaled.max(axis=-1, keepdims=True)
    exp = np.exp(scaled)
    result: np.ndarray = exp / exp.sum(axis=-1, keepdims=True)
    return result


def _soften(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    """Temperature-soften a probability matrix: ``p^(1/T)``, renormalised."""

    clipped = np.clip(probabilities, 1e-12, None)
    powered = clipped ** (1.0 / temperature)
    result: np.ndarray = powered / powered.sum(axis=-1, keepdims=True)
    return result


@dataclass
class LinearStudent:
    """Multinomial logistic regression over hashed features."""

    weights: np.ndarray  # (dim, n_classes), float32
    bias: np.ndarray  # (n_classes,), float32
    labels: tuple[str, ...]
    featurizer: HashingFeaturizer

    def predict_proba(self, texts: Sequence[str]) -> np.ndarray:
        features = self.featurizer.transform(texts)
        logits = features @ self.weights + self.bias
        return softmax(logits.astype(np.float64))

    def predict(self, texts: Sequence[str]) -> list[str]:
        probabilities = self.predict_proba(texts)
        return [self.labels[int(index)] for index in np.argmax(probabilities, axis=1)]

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            weights=self.weights,
            bias=self.bias,
            labels=np.array(self.labels),
            spec=np.array(json.dumps(self.featurizer.spec())),
        )
        return path

    @classmethod
    def load(cls, path: Path) -> LinearStudent:
        with np.load(path, allow_pickle=False) as bundle:
            spec = json.loads(str(bundle["spec"]))
            featurizer = HashingFeaturizer(
                dim=int(spec["dim"]),
                word_ngram_max=int(spec["word_ngram_max"]),
                char_ngram_min=int(spec["char_ngram_min"]),
                char_ngram_max=int(spec["char_ngram_max"]),
                max_length_chars=int(spec["max_length_chars"]),
            )
            return cls(
                weights=bundle["weights"].astype(np.float32),
                bias=bundle["bias"].astype(np.float32),
                labels=tuple(str(item) for item in bundle["labels"]),
                featurizer=featurizer,
            )


def load_examples_for_task(path: Path, *, task: str) -> tuple[list[str], list[str]]:
    """Read a JSONL eval/train file into (texts, single labels).

    Safety rows carry ``label`` (any case); emotion rows carry ``label`` or
    a ``labels`` list (the smoke path takes the first listed emotion —
    the real multi-label student is the transformer path, and the report
    says so). Rows without text or label are refused, not skipped silently.
    """

    if task not in {"safety", "emotion"}:
        raise DistillationError(f"unknown task {task!r}")
    texts: list[str] = []
    labels: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        text = row.get("text")
        label = row.get("label")
        if label is None and isinstance(row.get("labels"), list) and row["labels"]:
            label = row["labels"][0]
        if not isinstance(text, str) or not text.strip():
            raise DistillationError(f"{path}:{line_number}: row has no text")
        if not isinstance(label, str) or not label.strip():
            raise DistillationError(f"{path}:{line_number}: row has no label")
        texts.append(text)
        labels.append(label.strip().lower())
    if not texts:
        raise DistillationError(f"{path}: no usable rows")
    return texts, labels


class _SklearnTeacher:
    """TF-IDF + logistic regression stand-in teacher (sandbox only)."""

    def __init__(self, labels: Sequence[str]) -> None:
        self._labels = tuple(labels)
        self._pipeline: Any = None

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> _SklearnTeacher:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline

        self._pipeline = Pipeline(
            [
                (
                    "tfidf",
                    TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, lowercase=True),
                ),
                ("clf", LogisticRegression(max_iter=1000, C=4.0)),
            ]
        )
        self._pipeline.fit(list(texts), list(labels))
        return self

    def predict_proba(self, texts: Sequence[str]) -> np.ndarray:
        if self._pipeline is None:
            raise DistillationError("teacher used before fit")
        raw = self._pipeline.predict_proba(list(texts))
        classes = [str(item).lower() for item in self._pipeline.classes_]
        out: np.ndarray = np.zeros((len(texts), len(self._labels)), dtype=np.float64)
        for column, label in enumerate(classes):
            if label in self._labels:
                out[:, self._labels.index(label)] = raw[:, column]
        row_sums = out.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0.0] = 1.0
        normalised: np.ndarray = out / row_sums
        return normalised


def _train_linear_student(
    *,
    features: np.ndarray,
    teacher_soft: np.ndarray,
    hard_onehot: np.ndarray,
    config: DistillConfig,
    n_classes: int,
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Full-batch Adam on the distillation loss. Deterministic given the seed."""

    rng = np.random.default_rng(derive_seed(config.seed, "distill-init"))
    dim = features.shape[1]
    weights: np.ndarray = (rng.standard_normal((dim, n_classes)) * 0.01).astype(np.float64)
    bias: np.ndarray = np.zeros(n_classes, dtype=np.float64)
    temperature = config.training.temperature
    alpha = config.training.alpha
    targets_soft = _soften(teacher_soft, temperature)
    x: np.ndarray = features.astype(np.float64)
    losses: list[float] = []
    m_w = np.zeros_like(weights)
    v_w = np.zeros_like(weights)
    m_b = np.zeros_like(bias)
    v_b = np.zeros_like(bias)
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    steps_per_epoch = max(1, math_ceil(len(x), config.training.batch_size))
    step = 0
    for _epoch in range(config.training.epochs):
        for batch_index in range(steps_per_epoch):
            step += 1
            start = (batch_index * config.training.batch_size) % len(x)
            order = np.arange(start, min(start + config.training.batch_size, len(x)))
            xb, qb, yb = x[order], targets_soft[order], hard_onehot[order]
            logits = xb @ weights + bias
            p_t = softmax(logits, temperature=temperature)
            p_1 = softmax(logits)
            # dL/dz for alpha*T^2*CE(q,p_T) + (1-alpha)*CE(y,p_1)
            grad_logits = (alpha * temperature * (p_t - qb) + (1.0 - alpha) * (p_1 - yb)) / len(xb)
            grad_w = xb.T @ grad_logits + config.training.l2 * weights
            grad_b = grad_logits.sum(axis=0)
            m_w = beta1 * m_w + (1 - beta1) * grad_w
            v_w = beta2 * v_w + (1 - beta2) * (grad_w**2)
            m_b = beta1 * m_b + (1 - beta1) * grad_b
            v_b = beta2 * v_b + (1 - beta2) * (grad_b**2)
            weights -= (
                config.training.learning_rate
                * (m_w / (1 - beta1**step))
                / (np.sqrt(v_w / (1 - beta2**step)) + eps)
            )
            bias -= (
                config.training.learning_rate
                * (m_b / (1 - beta1**step))
                / (np.sqrt(v_b / (1 - beta2**step)) + eps)
            )
        logits = x @ weights + bias
        loss = float(
            alpha
            * temperature**2
            * _cross_entropy(targets_soft, softmax(logits, temperature=temperature))
            + (1.0 - alpha) * _cross_entropy(hard_onehot, softmax(logits))
        )
        losses.append(loss)
    return weights.astype(np.float32), bias.astype(np.float32), losses


def math_ceil(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _cross_entropy(targets: np.ndarray, probabilities: np.ndarray) -> float:
    return float(-np.mean(np.sum(targets * np.log(np.clip(probabilities, 1e-12, None)), axis=1)))


def _task_metrics(
    task: str, truths: Sequence[str], predictions: Sequence[str], labels: Sequence[str]
) -> dict[str, float]:
    metrics = {
        "accuracy": _accuracy(truths, predictions),
        "macro_f1": macro_f1(truths, predictions, list(labels)),
    }
    if task == "safety":
        crisis = binary_crisis_metrics(truths, predictions)
        metrics["crisis_recall"] = float(crisis["recall"] or 0.0)
        metrics["crisis_precision"] = float(crisis["precision"] or 0.0)
    return metrics


def _benchmark(predict: Any, texts: Sequence[str], *, repeats: int = 3) -> dict[str, float]:
    """Per-message latency (ms) over repeated passes; p50/p95 of singles."""

    samples: list[float] = []
    for _ in range(repeats):
        for text in texts:
            started = time.perf_counter()
            predict([text])
            samples.append((time.perf_counter() - started) * 1000.0)
    if not samples:
        return {"p50_latency_ms": 0.0, "p95_latency_ms": 0.0}
    ordered = sorted(samples)
    return {
        "p50_latency_ms": float(ordered[len(ordered) // 2]),
        "p95_latency_ms": float(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]),
    }


@dataclass(frozen=True)
class DistillResult:
    student: LinearStudent
    metrics: dict[str, Any] = field(default_factory=dict)
    artifact_path: Path | None = None


def distill(config: DistillConfig, *, out_dir: Path | None = None) -> DistillResult:
    """Run one distillation and (optionally) write the student artefact."""

    if config.student.kind == "transformer":
        raise DistillationError(
            "student.kind 'transformer' needs torch+transformers and a GPU: run "
            "ml/notebooks/colab_distill_export.ipynb (see ml/README.md Day 34). "
            "The sandbox path is student.kind 'linear_hash'."
        )
    if config.teacher_kind == "trained_artifact":
        raise DistillationError(
            "teacher_kind 'trained_artifact' needs a packaged Day-32/33 artefact "
            f"at {config.teacher_artifact_dir!r}, which this sandbox does not have; "
            "use teacher_kind 'sklearn_tfidf' for the smoke run (reports name it)."
        )

    train_path = resolve(config.data.train.path)
    eval_spec = config.data.test or config.data.eval
    eval_path = resolve(eval_spec.path)
    for spec, path in ((config.data.train, train_path), (eval_spec, eval_path)):
        if spec.sha256 is not None and sha256_file(path) != spec.sha256:
            raise DistillationError(f"{path}: sha256 does not match the config pin")

    train_texts, train_labels = load_examples_for_task(train_path, task=config.task)
    eval_texts, eval_labels = load_examples_for_task(eval_path, task=config.task)
    labels = tuple(config.data.labels or sorted(set(train_labels) | set(eval_labels)))
    label_index = {label: index for index, label in enumerate(labels)}
    unknown = (set(train_labels) | set(eval_labels)) - set(labels)
    if unknown:
        raise DistillationError(f"labels missing from config: {sorted(unknown)}")

    teacher = _SklearnTeacher(labels).fit(train_texts, train_labels)
    teacher_train = teacher.predict_proba(train_texts)
    teacher_eval = teacher.predict_proba(eval_texts)

    featurizer = HashingFeaturizer(
        dim=config.student.feature_dim,
        word_ngram_max=config.student.word_ngram_max,
        char_ngram_min=config.student.char_ngram_min,
        char_ngram_max=config.student.char_ngram_max,
        max_length_chars=config.student.max_length_chars,
    )
    features = featurizer.transform(train_texts)
    hard: np.ndarray = np.zeros((len(train_texts), len(labels)), dtype=np.float64)
    for row, label in enumerate(train_labels):
        hard[row, label_index[label]] = 1.0

    weights, bias, losses = _train_linear_student(
        features=features,
        teacher_soft=teacher_train,
        hard_onehot=hard,
        config=config,
        n_classes=len(labels),
    )
    student = LinearStudent(weights=weights, bias=bias, labels=labels, featurizer=featurizer)

    student_predictions = student.predict(eval_texts)
    teacher_predictions = [labels[int(i)] for i in np.argmax(teacher_eval, axis=1)]
    metrics: dict[str, Any] = {
        "task": config.task,
        "teacher_kind": config.teacher_kind,
        "student_kind": config.student.kind,
        "labels": list(labels),
        "train_rows": len(train_texts),
        "eval_rows": len(eval_texts),
        "eval_path": str(eval_path),
        "final_loss": losses[-1] if losses else None,
        "student": _task_metrics(config.task, eval_labels, student_predictions, labels),
        "teacher": _task_metrics(config.task, eval_labels, teacher_predictions, labels),
        "latency": _benchmark(student.predict_proba, eval_texts[: min(40, len(eval_texts))]),
        "teacher_latency": _benchmark(
            teacher.predict_proba, eval_texts[: min(40, len(eval_texts))]
        ),
    }

    artifact_path: Path | None = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = student.save(out_dir / "student.npz")
        (out_dir / "distill_metrics.json").write_text(
            json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return DistillResult(student=student, metrics=metrics, artifact_path=artifact_path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.compression.distill",
        description="Distil a teacher into a small student (Day 34).",
    )
    parser.add_argument("--config", required=True, help="distill YAML under ml/configs/")
    parser.add_argument("--out", default=None, help="artefact directory (student.npz + metrics)")
    args = parser.parse_args(argv)
    config = load_distill_config(args.config)
    result = distill(config, out_dir=Path(args.out) if args.out else None)
    print(json.dumps(result.metrics, indent=2, sort_keys=True))
    if result.artifact_path is not None:
        print(f"student: {result.artifact_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
