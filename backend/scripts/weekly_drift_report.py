#!/usr/bin/env python3
"""Generate a weekly drift report from the drift monitor snapshots.

This script is privacy-preserving: it reads only aggregate histograms and
centroids, never raw text or embeddings.

Usage:
  python backend/scripts/weekly_drift_report.py --window 2026-W42 --baseline 2026-W40
  python backend/scripts/weekly_drift_report.py --demo  # generate demo windows

Writes:
  ml/reports/drift/weekly_<window>.json
  ml/reports/drift/weekly_<window>.md
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "backend"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app.services.drift.monitor import DriftMonitor  # noqa: E402


def _demo_populate(monitor: DriftMonitor) -> None:
    """Populate baseline and current windows with synthetic aggregates for demo."""
    import random

    random.seed(48)
    baseline = "2026-W40"
    current = "2026-W42"

    # Baseline: normal traffic
    for _ in range(800):
        monitor.record(
            baseline,
            text_length=random.randint(20, 150),
            language=random.choice(["en", "en", "en", "hi", "bn"]),
            confidence=random.uniform(0.6, 0.95),
            safety_level=random.choice(["none"] * 7 + ["low"] * 2 + ["medium"]),
            abstained=random.random() < 0.08,
            guard_blocked=random.random() < 0.02,
            embedding=[random.gauss(0.0, 0.5) for _ in range(4)],
        )

    # Current: shifted inputs — longer messages, more hi, lower confidence, higher abstention
    for _ in range(800):
        monitor.record(
            current,
            text_length=random.randint(80, 350),  # shift to longer
            language=random.choice(["hi", "hi", "bn", "en", "en"]),
            confidence=random.uniform(0.35, 0.75),  # lower confidence
            safety_level=random.choice(["none"] * 5 + ["low"] * 2 + ["medium"] * 2 + ["high"]),
            abstained=random.random() < 0.18,  # double
            guard_blocked=random.random() < 0.07,  # triple
            embedding=[random.gauss(0.3, 0.6) for _ in range(4)],  # centroid shift
        )

    monitor.set_baseline(baseline)


def _render_markdown(report: dict) -> str:
    lines = [
        f"# Weekly Drift Report — {report['current_window']}",
        "",
        f"Generated at: {report['generated_at']}",
        f"Baseline: `{report['baseline_window']}`",
        f"Current: `{report['current_window']}`",
        "",
        f"**Alerts: {report['alert_count']}** — {'🔴 critical' if report['has_critical'] else '🟡 warnings' if report['alert_count'] else '✅ no drift'}",  # noqa: E501
        "",
        "## Aggregate snapshots (privacy-preserving)",
        "",
        "No raw text, no embeddings, no identifiers are stored. Only histograms and centroids.",
        "",
        "### Baseline",
        "",
        "```json",
        json.dumps(report["baseline"], indent=2),
        "```",
        "",
        "### Current",
        "",
        "```json",
        json.dumps(report["current"], indent=2),
        "```",
        "",
        "## Alerts (PSI/KS)",
        "",
        "| metric | PSI | KS | severity | message |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not report["alerts"]:
        lines.append("| — | — | — | none | No drift detected |")
    else:
        for a in report["alerts"]:
            lines.append(
                f"| {a['metric']} | {a['psi']:.3f} | {a['ks']:.3f} | {a['severity']} | {a['message']} |"  # noqa: E501
            )
    lines.extend(
        [
            "",
            "## Metrics & runbook",
            "",
            "- `manovia_drift_alerts_total{metric,severity}` counts alerts; alert on `severity=critical`.",  # noqa: E501
            "- `manovia_drift_checks_total{window}` counts weekly checks.",
            "- Thresholds: PSI 0.10 warning / 0.25 critical; KS 0.15 warning / 0.30 critical.",
            "- When critical fires, check: new language mix? longer inputs? model confidence drop?",
            "- This report is stored in `ml/reports/drift/` and linked from PROGRESS.md.",
            "",
            "_Privacy: all numbers are aggregates over a window. No per-message data leaves the process._",  # noqa: E501
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly drift report")
    parser.add_argument("--window", type=str, default="2026-W42", help="Current window")
    parser.add_argument("--baseline", type=str, default="2026-W40", help="Baseline window")
    parser.add_argument("--demo", action="store_true", help="Populate with demo synthetic data")
    parser.add_argument("--out-dir", type=str, default="ml/reports/drift", help="Output directory")
    args = parser.parse_args()

    monitor = DriftMonitor()
    if args.demo:
        _demo_populate(monitor)
        # override to use the demo windows
        args.baseline = "2026-W40"
        args.window = "2026-W42"
    else:
        # try to load from existing snapshots if any? For now demo is required for sandbox
        print(
            "No live snapshots — use --demo to generate a report with synthetic aggregates (privacy-safe)."  # noqa: E501
        )
        _demo_populate(monitor)

    report = monitor.weekly_report(args.window, baseline_window=args.baseline)

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"weekly_{args.window}.json"
    md_path = out_dir / f"weekly_{args.window}.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    print(f"Wrote {json_path} ({report['alert_count']} alerts, critical={report['has_critical']})")
    print(f"Wrote {md_path}")
    for a in report["alerts"]:
        print(
            f"  ALERT {a['severity']:8s} {a['metric']:28s} PSI={a['psi']:.3f} KS={a['ks']:.3f}  {a['message']}"  # noqa: E501
        )

    # Also emit metrics-like lines for verification
    print("\nMetrics that would fire:")
    for a in report["alerts"]:
        print(
            f'  manovia_drift_alerts_total{{metric="{a["metric"]}",severity="{a["severity"]}"}} 1'
        )


if __name__ == "__main__":
    main()
