#!/usr/bin/env python3
"""Export Python's ``str.casefold()`` as ground truth for the TypeScript engine.

Day 41 (Private Mode) runs the safety rules engine in the browser. One step of
``app.services.safety.normalise._fold`` is ``casefold()``, and JavaScript has no
equivalent: ``String.prototype.toLowerCase()`` differs from full case folding on
a few hundred code points (the sharp s folds to two letters, a final sigma folds
to its medial form, the micro sign folds to the Greek mu). Rather than hand-write
that table - a table nobody can review and that silently rots - this script
writes the code points a browser needs an override for to a JSON fixture:

    frontend/src/features/private/unicode/python-casefold.json

Two consumers, and the loop is closed on both sides:

* ``frontend/scripts/build-private-bundle.mjs`` derives the browser's
  casefold-override table from the fixture at build time, by comparing each
  entry with ``toLowerCase()`` and keeping only the divergences. Nothing is
  hard-coded in the bundle compiler.
* ``backend/tests/safety/test_private_bundle.py`` regenerates the fixture and
  fails if the committed copy drifted, and the frontend's
  ``casefold.test.ts`` asserts the TypeScript implementation reproduces every
  entry in it.

Which code points are included, and why that set is complete:

* every code point whose casefold is **not** the identity (1530 of them) — the
  characters the browser must fold differently from ``toLowerCase()``;
* every code point whose casefold **is** the identity but whose ``lower()`` is
  not (the 86 Cherokee syllabics U+13A0 to U+13F5, which Unicode deliberately
  excludes from case folding); and
* every code point listed in ``frontend/src/features/private/unicode/
  js-lowercase-codepoints.json``, which ``frontend/scripts/export-js-lowercase.mjs``
  generates from the JavaScript runtime itself. Python folds with Unicode 14 and
  a current browser with Unicode 17, so characters encoded in between lowercase
  in the browser and are unassigned — identity — here. For those the fixture
  records the identity, which tells the compiler to emit an override that stops
  the browser lowercasing them.

For every other code point neither runtime changes the character, so
``toLowerCase()`` is already correct there. That is checked exhaustively from
the JavaScript side (``verifyUnicodeClaim`` in the bundle compiler): a code point
missing from this fixture that still lowercases in a browser fails the build.

Usage::

    .venv/bin/python backend/scripts/export_casefold_cases.py [--check]

``--check`` regenerates in memory and exits 1 when the committed file differs,
which is what CI runs; without it the file is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
DATA_DIR: Final = REPO_ROOT / "frontend" / "src" / "features" / "private" / "unicode"
OUTPUT: Final = DATA_DIR / "python-casefold.json"
#: Generated from the JavaScript runtime by ``frontend/scripts/export-js-lowercase.mjs``.
JS_LOWERCASE: Final = DATA_DIR / "js-lowercase-codepoints.json"

#: Surrogates are not characters; ``chr()`` makes them but no string may contain them.
_SURROGATES: Final = range(0xD800, 0xE000)


def js_lowercase_codepoints() -> tuple[frozenset[int], dict[str, Any]]:
    """The code points a browser lowercases, and the fixture they came from."""
    if not JS_LOWERCASE.exists():
        raise SystemExit(
            f"missing {JS_LOWERCASE.relative_to(REPO_ROOT)} - run "
            "`npm --prefix frontend run private:fixtures` first"
        )
    payload = json.loads(JS_LOWERCASE.read_text(encoding="utf-8"))
    codepoints = payload.get("codepoints")
    if not isinstance(codepoints, list) or any(not isinstance(item, int) for item in codepoints):
        relative = JS_LOWERCASE.relative_to(REPO_ROOT)
        raise SystemExit(f"{relative}: codepoints must be a list of ints")
    provenance = {
        "count": payload.get("count"),
        "sha256": payload.get("sha256"),
        "unicode_version": (payload.get("runtime") or {}).get("unicode"),
    }
    return frozenset(codepoints), provenance


def casefold_cases() -> dict[str, str]:
    """Every code point where a browser's ``toLowerCase()`` needs an override.

    That is the union described in the module docstring: the code points whose
    full case folding is not the identity, plus the ones Unicode excludes from
    case folding but not from lowercasing (Cherokee), for which the recorded
    value is the character itself.

    Keyed by ``hex(codepoint)`` so the fixture is readable and stable across
    JSON writers; the consumer parses the key back into a number.
    """
    browser_lowercases, _ = js_lowercase_codepoints()
    cases: dict[str, str] = {}
    for codepoint in range(0x110000):
        if codepoint in _SURROGATES:
            continue
        char = chr(codepoint)
        folded = char.casefold()
        if folded != char or char.lower() != char or codepoint in browser_lowercases:
            cases[hex(codepoint)] = folded
    return cases


def count_folded_only() -> int:
    """How many entries exist because casefold is not the identity (a report figure)."""
    return sum(
        1
        for codepoint in range(0x110000)
        if codepoint not in _SURROGATES and chr(codepoint).casefold() != chr(codepoint)
    )


def build_payload() -> dict[str, Any]:
    """The fixture document, with provenance and a content hash."""
    cases = casefold_cases()
    canonical = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "format": "manovia-casefold-cases/1",
        "generated_by": "backend/scripts/export_casefold_cases.py",
        "source": "Python str.casefold() over every Unicode code point (surrogates excluded)",
        "unicodedata_version": unicodedata.unidata_version,
        "count": len(cases),
        "folded_count": count_folded_only(),
        "js_lowercase_source": js_lowercase_codepoints()[1],
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "cases": cases,
    }


def render(payload: dict[str, Any]) -> str:
    """Serialise deterministically: sorted keys, two-space indent, LF endings."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when the committed fixture differs",
    )
    args = parser.parse_args(argv)

    payload = build_payload()
    rendered = render(payload)

    if args.check:
        if not OUTPUT.exists():
            print(f"MISSING {OUTPUT}", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"DRIFT {OUTPUT} - re-run without --check", file=sys.stderr)
            return 1
        digest = payload["sha256"][:16]
        print(f"OK {OUTPUT.relative_to(REPO_ROOT)}: {payload['count']} cases, sha256 {digest}")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(
        f"wrote {OUTPUT.relative_to(REPO_ROOT)}: {payload['count']} override cases "
        f"({payload['folded_count']} of them because casefold is not the identity); "
        f"sha256 {payload['sha256'][:16]}, Unicode {payload['unicodedata_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
