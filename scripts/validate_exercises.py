#!/usr/bin/env python3
"""Validate every exercise with the runtime Pydantic schema; optionally print all copy.

Usage: python scripts/validate_exercises.py [--directory PATH] [--review] [--schema]
No server, network, API key, or database is required.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.exercises.engine import (
    ExerciseContentError,
    ExerciseDefinition,
    load_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=None)
    parser.add_argument(
        "--review",
        action="store_true",
        help="print every title, instruction, note, and choice",
    )
    parser.add_argument(
        "--schema", action="store_true", help="print the Pydantic JSON schema"
    )
    args = parser.parse_args()
    if args.schema:
        print(
            json.dumps(
                ExerciseDefinition.model_json_schema(), indent=2, ensure_ascii=False
            )
        )
        return 0
    try:
        catalog = load_catalog(args.directory)
    except ExerciseContentError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    for exercise in catalog.list():
        print(
            f"PASS {exercise.id:24} {exercise.duration_seconds:3}s  {len(exercise.steps):2} steps  {exercise.title.en}"
        )
        if args.review:
            print(f"  Titles: {exercise.title.model_dump()}")
            print(f"  {exercise.description}")
            for note in (*exercise.contraindications, *exercise.notes):
                print(f"  Note: {note}")
            for step in exercise.steps:
                print(
                    f"  {step.id} ({step.duration_seconds}s, {step.cue}): {step.instruction}"
                )
                for choice in step.choices:
                    print(f"    Choice: {choice.label}")
            print(f"  Evidence: {exercise.evidence_note}")
    print(
        f"Validated {len(catalog.list())} exercise YAML files; all duration sums match."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
