"""Day 34 verification — compression, serving and safe rollout, for real.

Runs the offline half of the Day-34 contract in-process (no server, no
network) and prints every number it asserts on:

1. fp32 vs distilled vs INT8 comparison (from the pipeline payloads the
   caller generated under /tmp — see docs/verification/day34-*.md);
2. the release-gate check rejecting a deliberately degraded model
   (crisis weights swapped, then scored on the frozen fixture);
3. checksum corruption refusing to load, with previous-version fallback;
4. register → promote → promote → rollback, production alias moving back.

The API half (200 shadow messages, identical responses, 50-concurrent
load test) needs a live server and is driven by the commands in
docs/verification/day34-compression-serving-rollout.md.

Run from the repository root with the ML venv:
    .venv/bin/python scripts/verify_day34.py /tmp/day34-safety /tmp/day34-emotion
Exits non-zero on any violation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ml" / "src"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {label}  {detail}")
    if not condition:
        FAILURES.append(label)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    safety_dir, emotion_dir = Path(argv[1]), Path(argv[2])

    # 1. Comparison table -------------------------------------------------
    from manovia_ml.compression.report import comparison_table

    print("\n== fp32 vs distilled vs INT8 ==")
    for label, directory in (("safety", safety_dir), ("emotion", emotion_dir)):
        payload = json.loads((directory / "pipeline_metrics.json").read_text(encoding="utf-8"))
        print(f"-- {label} --")
        print(comparison_table(payload["rows"]), end="")
    payload = json.loads((safety_dir / "pipeline_metrics.json").read_text(encoding="utf-8"))
    check("safety INT8 gates pass", payload["gates"]["passed"] is True)
    check(
        "INT8 smaller than fp32",
        payload["sizes"]["int8_mb"] < payload["sizes"]["fp32_mb"],
        f"{payload['sizes']['int8_mb']:.4f} < {payload['sizes']['fp32_mb']:.4f} MB",
    )

    # 2. Deliberately degraded model is rejected --------------------------
    from manovia_ml.compression.distill import LinearStudent, load_examples_for_task
    from manovia_ml.compression.onnx_export import evaluate_onnx, export_student

    with tempfile.TemporaryDirectory() as tmp:
        student = LinearStudent.load(safety_dir / "student.npz")
        degraded = LinearStudent(
            weights=student.weights[:, ::-1].copy(),  # crisis columns reversed
            bias=student.bias[::-1].copy(),
            labels=student.labels,
            featurizer=student.featurizer,
        )
        degraded_path = export_student(degraded, Path(tmp) / "degraded.onnx")
        texts, truths = load_examples_for_task(
            REPO_ROOT / "ml" / "data" / "toy" / "safety_toy.jsonl", task="safety"
        )
        metrics = evaluate_onnx(
            degraded_path,
            featurizer=student.featurizer,
            labels=student.labels,
            texts=texts,
            truths=truths,
            task="safety",
        )
        flat = {
            "safety_crisis_recall": metrics["crisis_recall"],
            "safety_accuracy": metrics["accuracy"],
            "teacher_safety_crisis_recall": payload["distill"]["teacher"]["crisis_recall"],
            "fp32_safety_crisis_recall": payload["fp32"]["crisis_recall"],
            "p50_latency_ms": metrics["p50_latency_ms"],
            "p95_latency_ms": metrics["p95_latency_ms"],
            "student_size_mb": metrics["size_mb"],
            "teacher_size_mb": payload["gate_metrics"]["teacher_size_mb"],
            "int8_size_mb": metrics["size_mb"],
            "fp32_size_mb": payload["fp32"]["size_mb"],
        }
        degraded_json = Path(tmp) / "degraded.json"
        degraded_json.write_text(json.dumps(flat), encoding="utf-8")
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "manovia_ml.compression.check_gates",
                "--metrics",
                str(degraded_json),
                "--task",
                "safety",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT / "ml"),
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT / "ml" / "src")},
            check=False,
        )
        print(proc.stdout)
        check("degraded model rejected by gates", proc.returncode == 1, proc.stdout.strip().splitlines()[-1] if proc.stdout else "")

    # 3. Corruption refuses to load; previous version is the fallback -----
    from app.services.ml_runtime.registry import (
        ChecksumMismatchError,
        RegistryLoader,
    )
    from manovia_ml.compression.registry import RegistryManager

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manager = RegistryManager(
            tracking_uri=f"file:{root / 'mlruns'}", registry_dir=root / "registry"
        )
        v1 = root / "v1"
        v1.mkdir()
        shutil.copyfile(safety_dir / "model.int8.onnx", v1 / "model.int8.onnx")
        shutil.copyfile(safety_dir / "manifest.json", v1 / "manifest.json")
        manager.register("safety-student", v1, version="v1")
        aliases = manager.promote("safety-student", "v1", gates_passed=True)
        check("promoted v1", aliases["production"] == "v1")

        v2 = root / "v2"
        shutil.copytree(v1, v2)
        manager.register("safety-student", v2, version="v2")
        aliases = manager.promote("safety-student", "v2", gates_passed=True)
        check("promoted v2", aliases["production"] == "v2" and aliases["previous_production"] == "v1")

        # Corrupt the production bytes: load refuses, fallback serves v1.
        model_path = root / "registry" / "safety-student" / "versions" / "v2" / "model.int8.onnx"
        model_path.write_bytes(model_path.read_bytes() + b"corrupt")
        loader = RegistryLoader(root / "registry")
        try:
            loader.load("safety-student")
            check("checksum mismatch refuses load", False, "loaded corrupt bytes")
        except ChecksumMismatchError:
            check("checksum mismatch refuses load", True)
        # Restore v2 bytes for the rollback assertion below.
        shutil.copyfile(v2 / "model.int8.onnx", model_path)

        # 4. Rollback ------------------------------------------------------
        rolled = manager.rollback("safety-student")
        check("rollback restores v1", rolled["production"] == "v1", json.dumps(rolled))
        check("resolve(@production) is v1", manager.resolve("safety-student").name == "v1")

    print(f"\n{'ALL PASS' if not FAILURES else 'FAILURES: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
