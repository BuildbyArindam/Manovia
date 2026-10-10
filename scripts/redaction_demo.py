#!/usr/bin/env python3
"""Print the redactor's before/after for a fixed set of sample strings.

This is the artefact behind Day 10 verification item 2: it runs the real
:class:`~app.services.nlp.redaction.Redactor` against ten strings carrying the
identifier classes the product cares about, so the behaviour can be read rather
than asserted. It makes no network call and needs no configuration.

    python3 scripts/redaction_demo.py            # table
    python3 scripts/redaction_demo.py --json     # machine-readable
    python3 scripts/redaction_demo.py --text "call 9876543210"

The samples are invented: the phone numbers, Aadhaar-like ids and the card
number below are not anybody's.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.nlp.redaction import PLACEHOLDERS, Redactor  # noqa: E402

#: ``(label, text)``. Deliberately includes the near-misses that must survive
#: (a PIN code, an order count) as well as the identifiers that must not.
SAMPLES: tuple[tuple[str, str], ...] = (
    ("email", "you can reach me at riya.sharma@example.com any evening"),
    ("phone +91", "call me on +91 98765 43210 after six"),
    ("phone STD", "my landline is 09876543210"),
    ("phone mobile", "just dial 9876543210"),
    ("Aadhaar-like", "my aadhaar is 1234 5678 9012 on file"),
    ("PAN-like", "pan ABCDE1234F"),
    ("SSN-like", "ssn 123-45-6789"),
    ("card (Luhn-valid)", "card 4111111111111111 exp 12/28"),
    ("URL with a token", "the reset link was https://app.example.com/reset?token=abc123def"),
    ("URL, no secret", "privacy policy is at https://manovia.app/privacy"),
)

#: A message carrying several classes at once, the realistic worst case.
COMBINED = (
    "Hi, I'm Riya. Reach me at riya.sharma@example.com or +91 98765 43210 — "
    "my aadhaar is 1234 5678 9012, I used card 4111111111111111, and the "
    "login link was https://app.example.com/reset?token=abc123def"
)


def render(samples: tuple[tuple[str, str], ...], redactor: Redactor) -> str:
    lines: list[str] = []
    width = max(len(label) for label, _ in samples)
    for label, text in samples:
        result = redactor.redact(text)
        lines.append(f"{label:<{width}}  before : {text}")
        lines.append(f"{'':<{width}}  after  : {result.text}")
        lines.append(f"{'':<{width}}  removed: {result.summary()}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument("--text", help="redact one string instead of the samples")
    args = parser.parse_args(argv)

    redactor = Redactor()

    if args.text:
        result = redactor.redact(args.text)
        if args.json:
            print(json.dumps({"before": args.text, "after": result.text, "counts": result.counts}))
        else:
            print(f"before : {args.text}")
            print(f"after  : {result.text}")
            print(f"removed: {result.summary()}")
        return 0

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "label": label,
                        "before": text,
                        "after": redactor.redact(text).text,
                        "counts": redactor.redact(text).counts,
                    }
                    for label, text in SAMPLES
                ],
                indent=2,
            )
        )
        return 0

    print("Manovia PII redaction — before / after")
    print("=" * 78)
    print(f"placeholders: {', '.join(PLACEHOLDERS)}")
    print("names are NOT redacted by default (LLM_REDACT_NAMES=false)")
    print("=" * 78)
    print()
    print(render(SAMPLES, redactor))

    print("Combined: every class in one message")
    print("-" * 78)
    print(render((("combined", COMBINED),), redactor))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
