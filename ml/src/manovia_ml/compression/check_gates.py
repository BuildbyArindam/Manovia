"""Release-gate check CLI: metrics JSON in, PASS/FAIL out (exit 1 on FAIL).

    python -m manovia_ml.compression.check_gates --metrics candidate.json --task safety

The metrics file is either a pipeline payload (``pipeline_metrics.json``,
whose ``gate_metrics`` are used) or a flat ``{metric: value}`` mapping.
A deliberately degraded candidate — crisis recall below the floor, or an
INT8 drop beyond ``release_gates.yaml`` — exits 1 and names the gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from manovia_ml.compression.gates import evaluate_gates, load_release_gates

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.compression.check_gates",
        description="Check candidate metrics against ml/configs/release_gates.yaml.",
    )
    parser.add_argument("--metrics", required=True, help="pipeline payload or flat metrics JSON")
    parser.add_argument("--task", required=True, choices=["safety", "emotion"])
    parser.add_argument("--gates", default=None, help="release_gates.yaml (default: committed)")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.metrics).read_text(encoding="utf-8"))
    metrics = payload.get("gate_metrics", payload)
    report = evaluate_gates(
        {str(k): float(v) for k, v in metrics.items()},
        load_release_gates(args.gates),
        task=args.task,
    )
    for check in report.checks:
        print(f"{'PASS' if check.passed else 'FAIL'}  {check.name} — {check.detail}")
    print("GATES: PASS" if report.passed else "GATES: FAIL (candidate rejected)")
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
