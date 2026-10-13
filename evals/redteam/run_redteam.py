"""Run the Day 29 red-team suite and write a dated report.

    python evals/redteam/run_redteam.py                 # all suites, rules-only gate
    python evals/redteam/run_redteam.py --with-ml       # + informational ML level per case
    python evals/redteam/run_redteam.py --label baseline

Writes ``evals/reports/redteam/<date>[-label]/summary.md`` and ``results.json``
and exits 1 when any case fails. Everything is offline: Fake LLM, temporary
SQLite, freshly generated keys.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import redteam  # noqa: E402

REPORTS = HERE.parent / "reports" / "redteam"


def render(results: list[redteam.CaseResult], *, label: str, with_ml: bool) -> str:
    table = redteam.summarise(results)
    lines = [
        f"# Red-team run — {dt.date.today().isoformat()} ({label})",
        "",
        "Offline: Fake LLM provider, temporary SQLite, generated keys. Safety-bypass cases "
        "are gated on the deterministic rules engine"
        + ("; the ensemble (rules+ML) level is shown for information." if with_ml else "."),
        "",
        "| suite | passed | failed | result |",
        "| --- | ---: | ---: | --- |",
    ]
    for suite in redteam.SUITES:
        row = table.get(suite, {"passed": 0, "failed": 0})
        lines.append(
            f"| {suite} | {row['passed']} | {row['failed']} | "
            f"{'PASS' if row['failed'] == 0 else 'FAIL'} |"
        )
    total_failed = sum(r["failed"] for r in table.values())
    lines += ["", f"**Total: {len(results) - total_failed}/{len(results)} passed.**", ""]
    for suite in redteam.SUITES:
        lines += [
            f"## {suite}",
            "",
            "| id | category | result | detail |",
            "| --- | --- | --- | --- |",
        ]
        for r in (r for r in results if r.suite == suite):
            detail = r.detail.replace("|", "\\|")
            if with_ml and "ensemble_level" in r.data:
                detail += f" (ensemble: {r.data['ensemble_level']})"
            lines.append(
                f"| {r.id} | {r.category} | {'PASS' if r.passed else '**FAIL**'} | {detail} |"
            )
        lines.append("")
    trace = [r for r in results if r.category == "multi_turn"]
    if trace:
        lines += ["## Multi-turn traces", ""]
        for r in trace:
            lines += [
                f"### {r.id}",
                "",
                "| turn | alone (rules) | chat risk_level | response_type | message |",
                "| ---: | --- | --- | --- | --- |",
            ]
            for row in r.data["trace"]:
                lines.append(
                    f"| {row['turn']} | {row['alone']} | {row['risk_level']} | "
                    f"{row['response_type']} | {row['text']} |"
                )
            lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-ml", action="store_true")
    parser.add_argument("--label", default="final")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    logging.disable(logging.WARNING)

    results = asyncio.run(redteam.run_all(with_ml=args.with_ml))
    out = args.out or REPORTS / f"{dt.date.today().isoformat()}-{args.label}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(
        render(results, label=args.label, with_ml=args.with_ml) + "\n", encoding="utf-8"
    )
    (out / "results.json").write_text(
        json.dumps([r.to_json() for r in results], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    table = redteam.summarise(results)
    for suite in redteam.SUITES:
        row = table.get(suite, {"passed": 0, "failed": 0})
        print(
            f"{suite:18} passed={row['passed']:3} failed={row['failed']:3} "
            f"{'PASS' if row['failed'] == 0 else 'FAIL'}"
        )
    failed = [r for r in results if not r.passed]
    for r in failed:
        print(f"  FAIL {r.id}: {r.detail}")
    print(f"report: {out.relative_to(HERE.parents[1])}/summary.md")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
