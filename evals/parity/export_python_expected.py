#!/usr/bin/env python3
"""Export what the Python rules engine answers for the Day 41 parity corpus.

``evals/parity/corpus.jsonl`` holds 500 messages; this script runs every one of
them through the engine the API runs and writes the answers to
``evals/parity/python-expected.json``:

    level, matched categories, rationale codes, and the whole assessment context

Those are the expectations the browser must reproduce.
``frontend/src/features/private/rules/parity.test.ts`` runs the TypeScript port
over the same corpus and asserts equality field by field — not just the level,
because two engines that agree on a verdict while disagreeing on *why* are two
engines that will disagree on some message nobody tested.

Both the corpus and these expectations are committed, and both have a ``--check``
mode, so CI fails when the content or the engine changes and the artifact was not
regenerated. The file also records the SHA-256 of the browser bundle it was
generated beside, which ties the three artifacts together: content → bundle →
expectations.

USAGE
    .venv/bin/python evals/parity/export_python_expected.py            # write
    .venv/bin/python evals/parity/export_python_expected.py --check     # drift gate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
BACKEND: Final = REPO_ROOT / "backend"
PARITY_DIR: Final = Path(__file__).resolve().parent
CORPUS: Final = PARITY_DIR / "corpus.jsonl"
OUTPUT: Final = PARITY_DIR / "python-expected.json"
BUNDLE: Final = REPO_ROOT / "frontend" / "public" / "private" / "safety-bundle.json"

FORMAT: Final = "manovia-parity-expectations/1"
CORPUS_FORMAT: Final = "manovia-parity-corpus/1"
EXPECTED_COUNT: Final = 500

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.safety.rules import RuleEngine, build_engine  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_corpus() -> list[dict[str, Any]]:
    """The corpus, with its header line checked rather than skipped."""
    if not CORPUS.exists():
        raise SystemExit(f"missing {CORPUS} — run build_corpus.py first")
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    if header.get("format") != CORPUS_FORMAT:
        raise SystemExit(f"{CORPUS.name}: unsupported corpus format {header.get('format')!r}")
    rows = [json.loads(line) for line in lines[1:] if line.strip()]
    if len(rows) != EXPECTED_COUNT:
        raise SystemExit(f"{CORPUS.name}: {len(rows)} messages, expected {EXPECTED_COUNT}")
    if header.get("count") != len(rows):
        raise SystemExit(f"{CORPUS.name}: header count disagrees with the rows")
    return rows


def expand(entry: dict[str, Any]) -> str:
    """The message text, expanding a `{filler, repeat, append}` recipe."""
    if "text" in entry:
        return str(entry["text"])
    return str(entry["filler"]) * int(entry["repeat"]) + str(entry["append"])


def expected_for(engine: RuleEngine, entry: dict[str, Any]) -> dict[str, Any]:
    assessment = engine.assess(expand(entry), language=entry.get("lang"))
    return {
        "id": entry["id"],
        "level": assessment.level.label,
        "categories": [category.value for category in assessment.matched_categories],
        "codes": list(assessment.rationale_codes),
        "context": assessment.context.model_dump(mode="json"),
    }


def build_payload(engine: RuleEngine, rows: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.monotonic()
    cases = [expected_for(engine, entry) for entry in rows]
    elapsed = time.monotonic() - started
    levels = Counter(case["level"] for case in cases)
    return {
        "format": FORMAT,
        "generated_by": "evals/parity/export_python_expected.py",
        "description": "What the Python rules engine answers for evals/parity/corpus.jsonl.",
        "count": len(cases),
        "corpus_sha256": sha256(CORPUS),
        "bundle_sha256": sha256(BUNDLE) if BUNDLE.exists() else None,
        # No timings in the artifact: a value that changes on every run would make
        # the --check drift gate useless.
        "runtime": {
            "python": platform.python_version(),
            "unicodedata": unicodedata.unidata_version,
        },
        "_elapsed_seconds": round(elapsed, 2),
        "levels": dict(sorted(levels.items())),
        "cases": cases,
    }


def render(payload: dict[str, Any]) -> str:
    """The artifact: everything except the run-specific timing line."""
    document = {key: value for key, value in payload.items() if not key.startswith("_")}
    return json.dumps(document, ensure_ascii=True, indent=1, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the artifact is stale")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a summary as one JSON object instead of prose",
    )
    args = parser.parse_args()

    rows = read_corpus()
    engine = build_engine()
    payload = build_payload(engine, rows)
    rendered = render(payload)

    stale_reason: str | None = None
    if not OUTPUT.exists():
        stale_reason = f"MISSING {OUTPUT.relative_to(REPO_ROOT)}"
    elif OUTPUT.read_text(encoding="utf-8") != rendered:
        stale_reason = f"DRIFT {OUTPUT.relative_to(REPO_ROOT)}"

    if args.check:
        if stale_reason is not None:
            print(stale_reason + " — re-run without --check", file=sys.stderr)
            return 1
        print(f"OK {OUTPUT.name}: {payload['count']} cases, levels {payload['levels']}")
        return 0
    if stale_reason is not None and args.json:
        print(json.dumps({"ok": False, "reason": stale_reason}))
        return 1

    OUTPUT.write_text(rendered, encoding="utf-8")
    summary = {
        "ok": True,
        "path": str(OUTPUT.relative_to(REPO_ROOT)),
        "count": payload["count"],
        "levels": payload["levels"],
        "seconds": payload["_elapsed_seconds"],
        "bytes": len(rendered),
    }
    print(
        json.dumps(summary)
        if args.json
        else (
            f"wrote {summary['path']}: {summary['count']} cases in {summary['seconds']}s, "
            f"{summary['bytes']} bytes, levels {payload['levels']}"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
