#!/usr/bin/env python3
"""Build the Day 41 cross-language parity corpus (500 phrases).

Private Mode runs a TypeScript port of the rules engine on the device. The two
implementations must agree, and "agree" has to mean something checkable, so both
are run over one shared corpus and compared field by field:

    evals/parity/corpus.jsonl            one message per line (this file writes it)
    evals/parity/python-expected.json    what Python answers   (export_python_expected.py)
    frontend/src/features/private/rules/parity.test.ts
                                         what TypeScript answers, asserted equal

Composition (exactly 500 messages; no RNG, no ordering surprises). Pools are
taken in the order below, deduplicated by message text, and the dataset pool —
the largest — fills whatever is left, so the total is 500 whatever overlaps:

1. messages written for this corpus, aimed at the places where Python and
   JavaScript genuinely differ: ``\\s`` membership (U+001C, U+FEFF), code points
   versus UTF-16 units (astral characters), case folding versus
   ``toLowerCase()``, windowing past 4000 and past 64000 characters, empty input,
   and each normalisation projection;
2. ``evals/redteam/cases/safety_bypass.yaml`` (Day 29) — the obfuscation
   attempts, which are what a second implementation is most likely to get wrong;
3. ``backend/tests/safety/cases.yaml``, sampled across all 35 regression groups
   so the corpus covers what the backend suite pins;
4. ``evals/datasets/crisis_cases.jsonl`` (Day 26), interleaved across the five
   risk levels and four language codes so every level and every supported
   language is represented.

Long messages are stored as ``{filler, repeat, append}`` rather than as literal
text, so both languages expand them the same way and this file stays small.

USAGE
    .venv/bin/python evals/parity/build_corpus.py            # write corpus.jsonl
    .venv/bin/python evals/parity/build_corpus.py --check     # exit 1 on drift
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Final

import yaml

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
PARITY_DIR: Final = Path(__file__).resolve().parent
OUTPUT: Final = PARITY_DIR / "corpus.jsonl"
CRISIS_CASES: Final = REPO_ROOT / "evals" / "datasets" / "crisis_cases.jsonl"
BACKEND_CASES: Final = REPO_ROOT / "backend" / "tests" / "safety" / "cases.yaml"
REDTEAM_CASES: Final = REPO_ROOT / "evals" / "redteam" / "cases" / "safety_bypass.yaml"

CORPUS_SIZE: Final = 500
GENERATED_BY: Final = "evals/parity/build_corpus.py"
FORMAT: Final = "manovia-parity-corpus/1"

BACKEND_COUNT: Final = 120
REDTEAM_COUNT: Final = 60
#: Interleave order for the dataset pool, so no single level fills the corpus.
DATASET_LEVELS: Final = ("HIGH", "IMMINENT", "MEDIUM", "LOW", "NONE")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            loaded = json.loads(line)
            assert isinstance(loaded, dict)
            rows.append(loaded)
    return rows


def sample(items: Sequence[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Take `count` items spread evenly across `items`, deterministically."""
    if count >= len(items):
        return list(items)
    return [items[(index * len(items)) // count] for index in range(count)]


def dataset_cases() -> Iterator[dict[str, Any]]:
    """The Day 26 dataset, interleaved across levels and sorted within each."""
    rows = read_jsonl(CRISIS_CASES)
    by_label = {
        label: sorted(
            (row for row in rows if row.get("label") == label),
            key=lambda row: (str(row.get("lang", "")), str(row["id"])),
        )
        for label in DATASET_LEVELS
    }
    for round_index in range(max((len(pool) for pool in by_label.values()), default=0)):
        for label in DATASET_LEVELS:
            pool = by_label[label]
            if round_index >= len(pool):
                continue
            row = pool[round_index]
            yield {
                "text": str(row["text"]),
                "lang": row.get("lang"),
                "source": f"crisis_cases:{row['id']}",
                "group": str(row.get("category") or label.lower()),
            }


def backend_cases() -> Iterator[dict[str, Any]]:
    document = yaml.safe_load(BACKEND_CASES.read_text(encoding="utf-8"))
    rows = sorted(document.get("cases", []), key=lambda row: str(row.get("id", "")))
    for row in sample(rows, BACKEND_COUNT):
        yield {
            "text": str(row["text"]),
            "lang": row.get("lang"),
            "source": f"backend:{row['id']}",
            "group": str(row.get("group", "general")),
        }


def redteam_cases() -> Iterator[dict[str, Any]]:
    document = yaml.safe_load(REDTEAM_CASES.read_text(encoding="utf-8"))
    rows = sorted(document.get("cases", []), key=lambda row: str(row.get("id", "")))
    for row in sample(rows, REDTEAM_COUNT):
        repeat = row.get("repeat")
        if repeat:
            # A long message: `filler` repeated, then the attack appended. Stored
            # as a recipe so both languages expand it identically.
            yield {
                "filler": str(row.get("filler", "lorem ipsum dolor sit amet ")),
                "repeat": int(repeat),
                "append": str(row["text"]),
                "lang": row.get("lang"),
                "source": f"redteam:{row['id']}",
                "group": str(row.get("category", "bypass")),
            }
            continue
        yield {
            "text": str(row["text"]),
            "lang": row.get("lang"),
            "source": f"redteam:{row['id']}",
            "group": str(row.get("category", "bypass")),
        }


#: A filler sentence long enough that repeating it builds a very long message.
FILLER: Final = "the weather was ordinary and the bus was late again. "


def day41_cases() -> Iterator[dict[str, Any]]:
    """Seventy messages written for the port, one per divergence risk."""

    def case(text: str, group: str, lang: str | None = None) -> dict[str, Any]:
        return {"text": text, "lang": lang, "source": f"day41:{group}", "group": group}

    def expanded(repeat: int, append: str, group: str) -> dict[str, Any]:
        return {
            "filler": FILLER,
            "repeat": repeat,
            "append": append,
            "lang": None,
            "source": f"day41:{group}",
            "group": group,
        }

    # -- Degenerate input: the engine must answer, not crash.
    yield case("", "empty")
    yield case("   ", "empty")
    yield case("\t\n\r ", "empty")
    yield case("...", "empty")
    yield case("🙂", "empty")
    yield case("!!! ???", "empty")

    # -- Baselines across the scale.
    yield case("today was good", "baseline")
    yield case("i'm fine", "baseline")
    yield case("i feel a bit low today", "baseline")
    yield case("i have been thinking about ending it", "baseline")
    yield case("i want to die", "baseline")
    yield case(
        "i have a plan to end it tonight and i already have the pills",
        "baseline",
    )
    yield case("i took 30 pills an hour ago", "baseline")
    yield case("i want to kill my neighbour", "baseline")
    yield case("i was raped last year and i cannot tell anyone", "baseline")
    yield case("everyone would be better off without me", "baseline")
    yield case("i have the rope in my closet", "baseline")

    # -- Negation, before / after / inside, in three languages.
    yield case("I don't want to die", "negation")
    yield case("i am not suicidal", "negation")
    yield case("im not planning to hurt myself", "negation")
    yield case("i dont want to hurt anyone", "negation")
    yield case("cant sleep", "negation")
    yield case("not that i want to die or anything", "negation")
    yield case("मैं आत्महत्या नहीं करना चाहता", "negation", "hi")
    yield case("আমি আত্মহত্যা করতে চাই না", "negation", "bn")
    yield case("আমি মরতে চাই না", "negation", "bn")
    yield case("main marna nahi chahta", "negation", "hi-Latn")

    # -- Figurative, and figurative next to something real.
    yield case("this exam is killing me", "figurative")
    yield case("i am dying to know what happens next", "figurative")
    yield case("my job is killing me but i want to die", "figurative")
    yield case("this traffic is killing me", "figurative")

    # -- Third person, quotation, fiction, and quoting oneself.
    yield case("my best friend said she wants to die", "third_person")
    yield case("my sister cuts herself", "third_person")
    yield case("he took 30 pills an hour ago", "third_person")
    yield case("in my story the hero kills himself", "fiction")
    yield case("my diary says 'i want to kill myself tonight'", "self_quote")
    yield case("she told me 'i am going to end it all'", "quotation")
    yield case('my mum said "i want to die" and i believed her', "third_person")

    # -- Clause structure.
    yield case("i am ok.\ni want to die.", "clauses")
    yield case("i like tea; i want to die", "clauses")
    yield case("and i want to die", "clauses")
    yield case("because i want to die", "clauses")
    yield case("i want to die but i wont do it", "clauses")
    yield case("die die die", "clauses")

    # -- Indic scripts and romanised Indic.
    yield case("मैं आत्महत्या करना चाहता हूँ", "indic", "hi")
    yield case("আমি আত্মহত্যা করতে চাই", "indic", "bn")
    yield case("main suicide karna chahta hu", "romanised", "hi-Latn")
    yield case("ami khub kharap achhi", "romanised", "bn")

    # -- Normalisation projections: leet, fused, spaced, stretched, misspelled.
    yield case("i want to d!e", "leet")
    yield case("k1ll me", "leet")
    yield case("iwanttodie", "fused")
    yield case("i w a n t to d i e", "spaced")
    yield case("kiiiillll meee", "stretched")
    yield case("iiii want to diiiie", "stretched")
    yield case("sucide thoghts", "misspelling")
    yield case("i am having sucidal thoghts", "misspelling")
    yield case("kms", "euphemism")
    yield case("kms kms kms", "euphemism")

    # -- Homoglyphs and combining marks.
    # The look-alike letters are the point of these three cases: U+0456, U+0501
    # and U+0455 where a reader expects Latin i, d and s. Ruff's ambiguous-character
    # rule is right to flag them, and wrong to want them changed.
    yield case("і want to die", "homoglyph")  # noqa: RUF001
    yield case("ԁie tonight", "homoglyph")  # noqa: RUF001
    yield case("ѕuicide plan", "homoglyph")  # noqa: RUF001
    yield case("i want to d̶i̶e̶", "combining")
    yield case("suicidé thoughts", "combining")

    # -- Invisible characters, and the two whitespace sets that differ.
    yield case("i w\u200bant to die", "invisible")
    yield case("su\u200bicide", "invisible")
    yield case("\ufeffi want to die", "invisible")
    yield case("su\u00adicide", "invisible")
    yield case("i\u00a0want\u00a0to\u00a0die", "whitespace")
    yield case("i\u001cwant\u001cto\u001cdie", "whitespace")
    yield case("i\ufeffwant\ufeffto\ufeffdie", "whitespace")
    yield case("i\u2028want\u2028to\u2028die", "whitespace")
    yield case("i\u3000want\u3000to\u3000die", "whitespace")

    # -- Case.
    yield case("I WANT TO DIE", "case")
    yield case("i WaNt To DiE", "case")
    yield case("STRASSE or straße, i want to die", "case")

    # -- Astral characters: code points versus UTF-16 units.
    # Astral characters: Python counts code points, JavaScript counts UTF-16 units.
    yield case("𝔄 i want to die", "astral")  # noqa: RUF001
    yield case("😢 i want to die 😢", "astral")
    yield case("i want to die 🪢", "astral")

    # -- Windowing: over one window, over the whole cap, and straddling a boundary.
    yield expanded(20, "i want to die", "windowed")
    yield expanded(400, "i want to die", "windowed")
    yield expanded(4000, "i want to die", "windowed")
    yield expanded(81, "i want to die tonight", "windowed")
    yield expanded(82, "i have a plan and the pills", "windowed")
    yield expanded(2000, "nothing to see here", "windowed")


def build_corpus() -> list[dict[str, Any]]:
    """The 500 messages: targeted cases first, the dataset pool balancing the rest."""
    pools: list[Iterator[dict[str, Any]]] = [
        day41_cases(),
        redteam_cases(),
        backend_cases(),
        dataset_cases(),
    ]
    seen_texts: set[str] = set()
    entries: list[dict[str, Any]] = []
    for pool in pools:
        for candidate in pool:
            if len(entries) == CORPUS_SIZE:
                break
            text = expand(candidate)
            if text in seen_texts:
                continue
            seen_texts.add(text)
            entries.append(candidate)
        if len(entries) == CORPUS_SIZE:
            break

    if len(entries) != CORPUS_SIZE:
        raise SystemExit(f"corpus has {len(entries)} entries, expected {CORPUS_SIZE}")

    corpus: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        record: dict[str, Any] = {"id": f"P-{index:04d}"}
        record.update({key: value for key, value in entry.items() if value is not None})
        corpus.append(record)
    return corpus


def expand(entry: dict[str, Any]) -> str:
    """The message text for a corpus entry, expanding a `repeat` recipe."""
    if "text" in entry:
        return str(entry["text"])
    return str(entry["filler"]) * int(entry["repeat"]) + str(entry["append"])


def render(corpus: Sequence[dict[str, Any]]) -> str:
    """One JSON object per line, ASCII-escaped.

    ``ensure_ascii`` is deliberate: the corpus contains U+2028 (LINE SEPARATOR),
    which is a line break for Python's ``str.splitlines`` and would split one
    record into two. Escaping every non-ASCII character keeps the file
    unambiguously one-message-per-line in every reader, and both languages decode
    the escapes to the same string.
    """
    lines = [
        json.dumps(
            {"format": FORMAT, "count": len(corpus), "generated_by": GENERATED_BY},
            sort_keys=True,
        )
    ]
    lines += [json.dumps(entry, sort_keys=True) for entry in corpus]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if corpus.jsonl is stale")
    args = parser.parse_args()
    corpus = build_corpus()
    rendered = render(corpus)

    if args.check:
        if not OUTPUT.exists():
            print(f"MISSING {OUTPUT}", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"DRIFT {OUTPUT} — re-run without --check", file=sys.stderr)
            return 1
        print(f"OK {OUTPUT.name}: {len(corpus)} messages")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    groups: dict[str, int] = {}
    for entry in corpus:
        groups[entry["group"]] = groups.get(entry["group"], 0) + 1
    sources = collections.Counter(entry["source"].split(":", maxsplit=1)[0] for entry in corpus)
    breakdown = ", ".join(f"{name} {count}" for name, count in sorted(sources.items()))
    print(
        f"wrote {OUTPUT.relative_to(REPO_ROOT)}: {len(corpus)} messages, "
        f"{len(groups)} groups ({breakdown})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
