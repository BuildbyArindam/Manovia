#!/usr/bin/env python3
"""Shadow-mode report: disagreement counts by category, never raw text.

Reads the JSONL disagreement log the shadow runner appends (categories,
levels and latencies only — the runner has no text field to write) and
prints the comparison an operator needs before promotion: how often the
candidate disagreed, in which direction, and at what latency.

    python scripts/shadow_report.py /path/to/shadow.jsonl

Exits 2 if any record carries a key outside the allowed metadata set —
a log line with a text-shaped field in it is a privacy bug, and the
report refuses to summarise it quietly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ml_runtime.shadow import ShadowRecord, summarise

ALLOWED_KEYS = {
    "category",
    "production_level",
    "candidate_level",
    "candidate_latency_ms",
    "production_version",
    "candidate_version",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shadow disagreement report (categories only).")
    parser.add_argument("log", help="shadow JSONL log written by ShadowRunner")
    args = parser.parse_args(argv)

    records: list[ShadowRecord] = []
    for line_number, line in enumerate(
        Path(args.log).read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        payload = json.loads(line)
        extra = set(payload) - ALLOWED_KEYS
        if extra:
            print(
                f"line {line_number}: unexpected keys {sorted(extra)} — refusing", file=sys.stderr
            )
            return 2
        records.append(ShadowRecord(**payload))

    summary = summarise(records)
    print(f"total comparisons: {summary['total']}")
    for category, count in summary["counts"].items():
        print(f"  {category}: {count}")
    print(f"disagreement rate: {summary['disagreement_rate']:.3f}")
    latency = summary["candidate_latency_ms"]
    print(f"candidate latency ms: p50={latency['p50']} p95={latency['p95']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
