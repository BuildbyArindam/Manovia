#!/usr/bin/env python3
"""Check fairness gates: fail CI when any template exceeds its max_gap.

Usage:
  python ml/scripts/check_fairness_gates.py
  python ml/scripts/check_fairness_gates.py --report ml/reports/fairness.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Ensure ml/src is on path
ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "ml" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from manovia_ml.fairness.audit import audit_all_fake  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Check fairness gates")
    parser.add_argument("--report", type=str, default="ml/reports/fairness.json")
    parser.add_argument("--config", type=str, default="ml/configs/release_gates.yaml")
    args = parser.parse_args()

    report_path = pathlib.Path(args.report)
    config_path = pathlib.Path(args.config)

    result = audit_all_fake(config_path=config_path)

    # Write report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Print summary
    print(f"Fairness audit: {result['total_models']} models, {len(result['models'])} reports")
    for model, rep in result["models"].items():
        status = "PASS" if rep["passed"] else "FAIL"
        largest = rep["largest_gap"]
        gap = largest["max_gap"] if largest else 0.0
        cat = largest["category"] if largest else "-"
        print(f"  {model:24s} {status}  largest_gap={gap:.4f} ({cat})  violations={len(rep['violations'])}")
        # per-category table
        for cat_name, cgap in sorted(rep["category_max_gaps"].items()):
            print(f"    - {cat_name:20s} max_gap={cgap:.4f}")

    if result["largest_gap_overall"]:
        lg = result["largest_gap_overall"]
        print(f"\nLargest overall: {lg['template_id']} ({lg['category']}) gap={lg['max_gap']:.4f} threshold={lg['threshold']:.4f}")
    print(f"\nOverall: {'PASS' if result['overall_passed'] else 'FAIL'} -> {report_path}")

    if not result["overall_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
