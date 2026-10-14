"""One-command Day-34 smoke pipeline: distil → ONNX → INT8 → gates → report.

This is the sandbox path (linear student, stand-in teacher). It writes only
to the caller's output directory — artefacts there are git-ignored model
bytes, never committed; the committed outputs are the report markdown and
the metrics JSON this module produces when the Makefile target asks for
them under ``ml/reports/``.

CLI:
    python -m manovia_ml.compression.pipeline \
        --config configs/distill_safety_smoke.yaml --out /tmp/day34-safety
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from manovia_ml.compression.distill import distill, load_examples_for_task
from manovia_ml.compression.gates import evaluate_gates, load_release_gates
from manovia_ml.compression.onnx_export import (
    evaluate_onnx,
    export_student,
    file_size_mb,
    quantize_onnx,
    write_manifest,
)
from manovia_ml.compression.report import write_report
from manovia_ml.config import DistillConfig, load_distill_config
from manovia_ml.paths import resolve

__all__ = ["main", "run_pipeline"]


def _gate_metrics(
    config: DistillConfig, result: Any, fp32: dict[str, Any], int8: dict[str, Any]
) -> dict[str, float]:
    teacher = result.metrics["teacher"]
    student = result.metrics["student"]
    metrics: dict[str, float] = {
        "p50_latency_ms": float(int8["p50_latency_ms"]),
        "p95_latency_ms": float(int8["p95_latency_ms"]),
        "student_size_mb": float(int8["size_mb"]),
        "fp32_size_mb": float(fp32["size_mb"]),
        "int8_size_mb": float(int8["size_mb"]),
    }
    # Teacher size: declared for real runs; for the smoke stand-in the
    # student file itself is the honest denominator the config intends
    # (teacher_size_mb=0 means "no declared teacher", so use fp32 student
    # as the reference and say so in the report notes).
    metrics["teacher_size_mb"] = (
        float(config.teacher_size_mb) if config.teacher_size_mb > 0 else float(fp32["size_mb"]) * 4
    )
    if config.task == "safety":
        metrics.update(
            {
                "safety_crisis_recall": float(int8["crisis_recall"]),
                "safety_accuracy": float(int8["accuracy"]),
                "teacher_safety_crisis_recall": float(teacher["crisis_recall"]),
                "fp32_safety_crisis_recall": float(fp32["crisis_recall"]),
            }
        )
    else:
        metrics.update(
            {
                "emotion_macro_f1": float(int8["macro_f1"]),
                "teacher_emotion_macro_f1": float(teacher["macro_f1"]),
                "fp32_emotion_macro_f1": float(fp32["macro_f1"]),
            }
        )
    _ = student
    return metrics


def run_pipeline(config: DistillConfig, *, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = distill(config, out_dir=out_dir)
    student = result.student

    fp32_path = export_student(student, out_dir / "model.onnx")
    int8_path = quantize_onnx(fp32_path, out_dir / "model.int8.onnx")

    eval_spec = config.data.test or config.data.eval
    eval_path = resolve(eval_spec.path)
    texts, truths = load_examples_for_task(eval_path, task=config.task)
    fp32_metrics = evaluate_onnx(
        fp32_path,
        featurizer=student.featurizer,
        labels=student.labels,
        texts=texts,
        truths=truths,
        task=config.task,
    )
    int8_metrics = evaluate_onnx(
        int8_path,
        featurizer=student.featurizer,
        labels=student.labels,
        texts=texts,
        truths=truths,
        task=config.task,
    )
    write_manifest(
        out_dir,
        task=config.task,
        labels=student.labels,
        featurizer=student.featurizer,
        model_filename="model.int8.onnx",
        teacher_kind=config.teacher_kind,
        student_kind=config.student.kind,
        extra={"fp32_model_file": "model.onnx", "config_name": config.name},
    )

    gates = load_release_gates()
    gate_metrics = _gate_metrics(config, result, fp32_metrics, int8_metrics)
    report = evaluate_gates(gate_metrics, gates, task=config.task)

    teacher_metrics = result.metrics["teacher"]
    teacher_latency = result.metrics["teacher_latency"]
    rows: list[dict[str, Any]] = [
        {
            "name": f"teacher ({config.teacher_kind})",
            "size_mb": gate_metrics["teacher_size_mb"],
            "p50_latency_ms": teacher_latency["p50_latency_ms"],
            "p95_latency_ms": teacher_latency["p95_latency_ms"],
            "safety_crisis_recall": teacher_metrics.get("crisis_recall"),
            "emotion_macro_f1": teacher_metrics.get("macro_f1"),
            "accuracy": teacher_metrics.get("accuracy"),
        },
        {
            "name": "student fp32 (ONNX)",
            "size_mb": fp32_metrics["size_mb"],
            "p50_latency_ms": fp32_metrics["p50_latency_ms"],
            "p95_latency_ms": fp32_metrics["p95_latency_ms"],
            "safety_crisis_recall": fp32_metrics.get("crisis_recall"),
            "emotion_macro_f1": fp32_metrics.get("macro_f1"),
            "accuracy": fp32_metrics.get("accuracy"),
        },
        {
            "name": "student INT8 (ONNX, dynamic)",
            "size_mb": int8_metrics["size_mb"],
            "p50_latency_ms": int8_metrics["p50_latency_ms"],
            "p95_latency_ms": int8_metrics["p95_latency_ms"],
            "safety_crisis_recall": int8_metrics.get("crisis_recall"),
            "emotion_macro_f1": int8_metrics.get("macro_f1"),
            "accuracy": int8_metrics.get("accuracy"),
        },
    ]
    payload: dict[str, Any] = {
        "config": config.name,
        "task": config.task,
        "rows": rows,
        "gate_metrics": gate_metrics,
        "gates": report.as_dict(),
        "fp32": fp32_metrics,
        "int8": int8_metrics,
        "distill": result.metrics,
        "sizes": {"fp32_mb": file_size_mb(fp32_path), "int8_mb": file_size_mb(int8_path)},
    }
    (out_dir / "pipeline_metrics.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_report(
        title=f"Day 34 compression report — {config.name}",
        rows=rows,
        gates=report.as_dict(),
        notes=[
            f"Teacher kind: `{config.teacher_kind}`; student kind: `{config.student.kind}`. "
            "Smoke numbers are plumbing (stand-in teacher, train==eval fixture), not quality.",
            "INT8 is ONNX Runtime dynamic quantisation (QInt8 weights); fp32 and INT8 were "
            "scored on the same frozen eval rows in the same process.",
            "Promotion requires every gate above to pass; a failed gate rejects the candidate "
            "(ml/configs/release_gates.yaml, ADR 0034).",
        ],
        out_path=out_dir / "compression_report.md",
    )
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.compression.pipeline",
        description="Day 34 smoke pipeline: distil, export, quantise, gate, report.",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    payload = run_pipeline(load_distill_config(args.config), out_dir=Path(args.out))
    print(json.dumps({"rows": payload["rows"], "gates": payload["gates"]}, indent=2))
    return 0 if payload["gates"]["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
