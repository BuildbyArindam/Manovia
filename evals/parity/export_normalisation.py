#!/usr/bin/env python3
"""Export Python's normalisation of probe messages for the browser port to match.

The 500-message parity corpus compares *verdicts*. This compares the layer
underneath them: the projections the verdicts are built from. A port can agree on
500 levels while computing a different `primary`, and then disagree on the 501st
message — so the intermediate values are pinned too.

For every probe this writes what `app.services.safety.normalise` produces:

    primary          apostrophes out, case folded, punctuation spaced
    variants         the five projections, deduplicated, in order
    squashed         `primary` with whitespace removed
    collapsed        `primary` with repeated letters collapsed
    clause_text      `primary` with clause bars at the same offsets
    clauses          clause text and offsets (must be valid in `primary`)
    first_person     whether the message has a first-person subject of its own
    quotes           [text with the quoted spans removed, the quoted spans]
    confusables      the homoglyph fold
    combining        the stacked-mark strip

Probes are the targeted messages from the parity corpus (skipping the ones long
enough to be windowed — their levels are compared by the corpus, their 200 KB
`primary` is not worth committing twice) plus a list written for this file:
NFKC curiosities, case-folding edge cases, and the code points where Python's and
JavaScript's whitespace classes disagree.

USAGE
    .venv/bin/python evals/parity/export_normalisation.py            # write
    .venv/bin/python evals/parity/export_normalisation.py --check     # drift gate
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import unicodedata
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
BACKEND: Final = REPO_ROOT / "backend"
PARITY_DIR: Final = Path(__file__).resolve().parent
CORPUS: Final = PARITY_DIR / "corpus.jsonl"
OUTPUT: Final = PARITY_DIR / "python-normalisation.json"

FORMAT: Final = "manovia-parity-normalisation/1"
#: Corpus messages longer than this are windowed; their projections are not exported.
MAX_PROBE_CHARS: Final = 2000

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.safety.normalise import (  # noqa: E402
    apply_leet,
    correct_spellings,
    fold_confusables,
    has_first_person,
    index_clauses,
    normalise,
    split_quotes,
    strip_invisible,
    strip_latin_combining,
    to_clause_text,
    to_primary,
)
from app.services.safety.patterns import load_patterns  # noqa: E402

#: Probes written for this file: Unicode behaviour, not risk vocabulary.
EXTRA_PROBES: Final[tuple[tuple[str, str], ...]] = (
    ("apostrophes", "don't can't won't I'll you're"),
    ("apostrophes_unicode", "don\u2019t can\u2018t \u02bctonight"),
    ("sharp_s", "stra\u00dfe STRASSE Stra\u00dfe"),
    ("final_sigma", "\u039f\u03a3 \u03c3\u03c2 \u03a3\u03a4\u0399\u03a3"),
    ("micro_sign", "\u00b5g \u03bcg"),
    ("cherokee", "\u13a0\u13b1\u13cf"),
    ("turkish_dotless", "\u0131 \u0130 i I"),
    ("fullwidth", "\uff24\uff29\uff25 \uff44\uff49\uff45"),
    ("ligature", "\ufb01le \ufb00ect"),
    ("angstrom", "\u212b \u00c5 \u00e5"),
    ("roman_numerals", "\u216b \u2170 \u2160"),
    ("superscripts", "x\u00b2 \u2070 \u00b9\u2070"),
    ("devanagari_nukta", "\u0915\u093c \u0916\u093c \u0928\u093c\u0947"),
    ("devanagari_matra", "\u0915\u093f\u0924\u0928\u093e \u091a\u093e\u0939\u0924\u093e"),
    ("bengali_yaphala", "\u0995\u09cd\u09af \u09b0\u09cd\u09ac \u09b9\u09cd\u09af"),
    ("bengali_digits", "\u09e6\u09e7\u09e8 \u09e9\u09ea"),
    ("arabic_indic_digits", "\u0660\u0661\u0662 \u06f0\u06f1"),
    ("cyrillic_real", "\u043f\u0440\u0438\u0432\u0435\u0442 \u043a\u0430\u043a"),
    ("cyrillic_lookalikes", "\u0456 \u0435 \u0430 \u043e \u0440 \u0441"),
    ("greek_lookalikes", "\u03b1 \u03b2 \u03b5 \u03b7 \u03ba \u03bd"),
    ("combining_stack", "d\u0336i\u0336e\u0336 \u0065\u0301\u0301\u0301"),
    ("zero_width", "su\u200bicide\u200c \u2060word\ufeff"),
    ("soft_hyphen", "su\u00adicide \u00ad\u00ad"),
    ("bidi_marks", "\u202eword\u202c \u200f\u200e"),
    ("ws_file_separator", "i\u001cwant\u001cto\u001cdie"),
    ("ws_unit_separators", "a\u001fb\u001ec\u001dd"),
    ("ws_bom", "i\ufeffwant\ufeffto\ufeffdie"),
    ("ws_next_line", "a\u0085b"),
    ("ws_nbsp", "i\u00a0want\u00a0to\u00a0die"),
    ("ws_mongolian", "a\u180eb"),
    ("ws_line_separator", "a\u2028b\u2029c"),
    ("ws_ideographic", "i\u3000want\u3000to\u3000die"),
    ("ws_thin_spaces", "a\u2009b\u202fc\u205fd\u2000e"),
    ("astral", "\U0001d504 i want to die \U0001f622"),
    ("astral_only", "\U0001f622\U0001f62d\U0001f5a4"),
    ("emoji_between_words", "i\u2764die"),
    ("clause_bars", "i am ok.\ni want to die; really! why? no"),
    ("conjunctions", "i want to die but i wont and i am scared"),
    ("leet", "k1ll m3 1 want t0 d!e"),
    ("leet_money", "i w@nt t0 d!e $o bad"),
    ("misspellings", "sucide thoghts, i am sucidal"),
    ("repeats", "kiiiillll meee, diiiiieeee"),
    ("quotes_double", 'she said "i want to die" and left'),
    ("quotes_curly", "she said \u201ci want to die\u201d and left"),
    ("quotes_single_spaced", "she said 'i want to die' and left"),
    ("quotes_single_word", "don't say 'die' to me"),
    ("self_quote", "my diary says 'i want to kill myself tonight'"),
    ("relation", "my friend wants to die, my mum doesnt care"),
    ("third_person_frames", "she said she wants to die; he told me he has a plan"),
    ("fiction_frame", "in my story the hero kills himself"),
    ("empty", ""),
    ("whitespace_only", " \t\n\r "),
    ("punctuation_only", "!!! ??? ..."),
    ("mixed_scripts", "main \u092e\u0930 \u099c\u09be\u09ac\u09cb i want to die"),
    ("long_clause", "i " + "very " * 60 + "much want to die"),
)


def corpus_probes() -> list[tuple[str, str]]:
    """Targeted corpus messages, short enough to be worth committing twice."""
    lines = CORPUS.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines[1:] if line.strip()]
    probes: list[tuple[str, str]] = []
    for row in rows:
        if not str(row.get("source", "")).startswith("day41:"):
            continue
        if "text" not in row:
            continue  # a `repeat` recipe: windowed, and covered by the corpus
        text = str(row["text"])
        if len(text) > MAX_PROBE_CHARS:
            continue
        probes.append((str(row["id"]), text))
    return probes


def probe_record(identifier: str, text: str) -> dict[str, Any]:
    context = load_patterns().context
    cleaned = strip_invisible(text)
    message = normalise(text, context)
    primary = message.primary
    marked = to_clause_text(cleaned)
    index = index_clauses(primary, marked=marked)
    remainder, quoted = split_quotes(text)
    assert message.index.clauses == index.clauses, f"{identifier}: clause index disagrees"
    return {
        "id": identifier,
        "text": text,
        "stripped": cleaned,
        "variants": list(message.variants),
        "primary": primary,
        "leet": to_primary(apply_leet(cleaned, context.leet_map)),
        "corrected": correct_spellings(primary, context),
        "clause_text": marked,
        "clauses": [
            {"text": clause.text, "start": clause.start, "end": clause.end}
            for clause in index.clauses
        ],
        "squashed": message.squashed,
        "collapsed": message.collapsed,
        "confusables": fold_confusables(text),
        "combining": strip_latin_combining(text),
        "first_person": has_first_person(primary, context),
        "quotes": [remainder, quoted],
        "lengths": {
            "primary": len(primary),
            "clause_text": len(marked),
            "codepoints": len(text),
            "utf16_units": len(text.encode("utf-16-le")) // 2,
        },
    }


def build_payload() -> dict[str, Any]:
    probes = corpus_probes() + list(EXTRA_PROBES)
    seen: set[str] = set()
    cases: list[dict[str, Any]] = []
    for identifier, text in probes:
        if text in seen:
            continue
        seen.add(text)
        cases.append(probe_record(identifier, text))
    return {
        "format": FORMAT,
        "generated_by": "evals/parity/export_normalisation.py",
        "description": "What Python's normalise layer produces, for the browser port to match.",
        "count": len(cases),
        "runtime": {
            "python": platform.python_version(),
            "unicodedata": unicodedata.unidata_version,
        },
        "cases": cases,
    }


def render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=1, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the artifact is stale")
    args = parser.parse_args()
    payload = build_payload()
    rendered = render(payload)

    if args.check:
        if not OUTPUT.exists():
            print(f"MISSING {OUTPUT}", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"DRIFT {OUTPUT} — re-run without --check", file=sys.stderr)
            return 1
        print(f"OK {OUTPUT.name}: {payload['count']} probes")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    relative = OUTPUT.relative_to(REPO_ROOT)
    print(f"wrote {relative}: {payload['count']} probes, {len(rendered)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
