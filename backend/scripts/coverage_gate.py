#!/usr/bin/env python3
"""Fail the build when coverage drops below the Day 26 targets.

``pytest --cov`` prints a table and exits 0 no matter how bad the numbers are,
and coverage.py's own ``fail_under`` can only express one threshold for the
whole run. The Day 26 brief asks for three different promises at once:

* the backend overall stays at or above **85%**,
* every module under ``app/services/safety/`` stays at or above **95%** —
  the crisis rules, the ensemble, the output guard and the pattern loader are
  the code that decides whether somebody in trouble gets help, so "mostly
  covered" is not a sentence that belongs next to them,
* the crypto and scoring paths stay at **100%**, because a branch in either of
  them that no test has ever executed is a branch nobody can claim is safe:
  an untaken branch in ``decrypt_field`` is ciphertext we cannot say we reject,
  and an untaken branch in the scorer is an answer set we cannot say we refuse.

Both statements *and* branches are gated. A line count alone is satisfied by a
test that walks the happy path through every line and never enters an ``if``
body; branches are what the ``else`` clauses actually depend on.

Exit code 0 means every rule below passed. Exit code 1 prints each violated
rule as ``file: metric achieved < required`` so the failing module is obvious
from CI output without opening the HTML report.

Usage::

    coverage json  # written by `make test` to backend/coverage.json
    python scripts/coverage_gate.py coverage.json
    python scripts/coverage_gate.py coverage.json --json   # machine readable
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The overall floor for the whole backend.
OVERALL_MIN = 85.0

#: Modules that must stay at or above this, statements and branches.
SAFETY_MIN = 95.0

#: Modules that must stay at exactly 100%, statements and branches.
PERFECT = (
    "app/core/crypto.py",
    "app/services/journal/crypto.py",
    "app/services/assessments/scoring.py",
)


@dataclass(frozen=True)
class Rule:
    """One gate: a file glob plus the statement and branch floors for it."""

    name: str
    pattern: str
    statements: float
    branches: float


def default_rules() -> list[Rule]:
    """The Day 26 gate set, in the order failures are reported."""
    return [
        Rule("backend overall", "*", OVERALL_MIN, OVERALL_MIN),
        Rule("services/safety", "app/services/safety/*.py", SAFETY_MIN, SAFETY_MIN),
        *(Rule(f"exact: {path}", path, 100.0, 100.0) for path in PERFECT),
    ]


def _percent(covered: int, total: int) -> float:
    """Coverage percentage, defined as 100.0 when there is nothing to cover.

    A module with no statements (``app/services/safety/__init__.py`` re-exports
    only) is fully covered by definition; treating it as a division error would
    make the gate fail for a reason that has nothing to do with the code.
    """
    return 100.0 if total == 0 else 100.0 * covered / total


def _file_metrics(data: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Per-file statement and branch percentages from a coverage JSON payload."""
    metrics: dict[str, dict[str, float]] = {}
    for path, info in data.get("files", {}).items():
        summary = info["summary"]
        metrics[path] = {
            "statements": _percent(summary["covered_lines"], summary["num_statements"]),
            "branches": _percent(
                summary.get("covered_branches", 0), summary.get("num_branches", 0)
            ),
            "num_statements": float(summary["num_statements"]),
            "num_branches": float(summary.get("num_branches", 0)),
        }
    return metrics


def _aggregate(metrics: dict[str, dict[str, float]], scope: dict[str, Any]) -> dict[str, float]:
    """Combine per-file counters into one statement/branch pair for a scope."""
    covered_lines = float(scope["covered_lines"])
    covered_branches = float(scope.get("covered_branches", 0))
    return {
        "statements": _percent(int(covered_lines), int(scope["num_statements"])),
        "branches": _percent(int(covered_branches), int(scope.get("num_branches", 0))),
    }


def evaluate(data: dict[str, Any], rules: list[Rule] | None = None) -> list[str]:
    """Return one human-readable line per violated rule (empty when all pass)."""
    active = rules if rules is not None else default_rules()
    files = _file_metrics(data)
    totals = data["totals"]
    failures: list[str] = []

    for rule in active:
        if rule.pattern == "*":
            measured = _aggregate(files, totals)
            labels = ["overall"]
        else:
            matched = {
                path: metrics
                for path, metrics in files.items()
                if fnmatch.fnmatch(path, rule.pattern)
            }
            missing = [] if matched else [f"no files matched {rule.pattern!r}"]
            labels = [f"{rule.pattern} ({len(matched)} files)"] if matched else missing
            measured = {
                "statements": min((m["statements"] for m in matched.values()), default=100.0),
                "branches": min((m["branches"] for m in matched.values()), default=100.0),
            }
        for metric in ("statements", "branches"):
            achieved = measured[metric]
            required = getattr(rule, metric)
            if achieved + 1e-9 < required:
                worst = sorted(
                    (
                        (m[metric], path)
                        for path, m in (files.items() if rule.pattern == "*" else matched.items())
                    ),
                )[:3]
                detail = ", ".join(f"{path} {value:.1f}%" for value, path in worst)
                failures.append(
                    f"{rule.name} [{labels[0]}] {metric}: {achieved:.2f}% < {required:.0f}%"
                    f"  (lowest: {detail})"
                )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce the Day 26 coverage gates.")
    parser.add_argument(
        "coverage_json",
        nargs="?",
        default="coverage.json",
        help="path to the coverage JSON report written by pytest-cov (default: coverage.json)",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    path = Path(args.coverage_json)
    if not path.is_file():
        print(
            f"coverage gate: {path} not found — run `make test` (which writes it) first.",
            file=sys.stderr,
        )
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    rules = default_rules()
    failures = evaluate(data, rules)
    files = _file_metrics(data)

    if args.json:
        print(
            json.dumps(
                {
                    "overall": _aggregate(files, data["totals"]),
                    "rules": [
                        {
                            "name": rule.name,
                            "pattern": rule.pattern,
                            "min_statements": rule.statements,
                            "min_branches": rule.branches,
                        }
                        for rule in rules
                    ],
                    "failures": failures,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        totals = data["totals"]
        statements = _percent(totals["covered_lines"], totals["num_statements"])
        branches = _percent(totals.get("covered_branches", 0), totals.get("num_branches", 0))
        print("coverage gate (Day 26)")
        print(
            f"  backend overall      statements {statements:6.2f}%"
            f"  branches {branches:6.2f}%   floor {OVERALL_MIN:.0f}%"
        )
        for rule in rules[1:]:
            matched = {p: m for p, m in files.items() if fnmatch.fnmatch(p, rule.pattern)}
            for file_path in sorted(matched):
                metrics = matched[file_path]
                print(
                    f"  {file_path:52s} statements {metrics['statements']:6.2f}%"
                    f"  branches {metrics['branches']:6.2f}%"
                    f"   floor {rule.statements:.0f}%"
                )
        for failure in failures:
            print(f"  FAIL {failure}", file=sys.stderr)

    if failures:
        print(f"\ncoverage gate: {len(failures)} rule(s) violated", file=sys.stderr)
        return 1
    print("\ncoverage gate: all rules satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
