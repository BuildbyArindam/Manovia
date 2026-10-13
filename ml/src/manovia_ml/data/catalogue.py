"""The dataset catalogue is data, not prose: parse it, check it, render its table.

``ml/data/CATALOGUE.md`` holds one fenced ``yaml`` block per dataset, under a
``catalogue:`` key, and a summary table generated from those blocks. Writing the
table by hand is how a catalogue starts lying: the block says "CC BY-NC" and the
table three screens up says "Apache-2.0".

So the table is generated (:func:`render_table`), ``make ml-catalogue`` rewrites
it, and ``ml/tests/test_catalogue.py`` fails if the committed table does not
match the committed blocks. The same test enforces the rules that make the
catalogue worth having:

* every verdict of ``USE``/``USE WITH CONDITIONS`` has ``allowed_use: commercial``
  and nothing else does — the licence column and the verdict column cannot
  disagree;
* a dataset with personal data is never ``USE``;
* every entry records where its licence was verified, and when, and entries go
  stale after :data:`LICENCE_FRESHNESS_DAYS`;
* a ``DO NOT USE`` dataset is not named in any config under ``ml/configs/``.

That last one is the point. A rule that only lives in a document is a rule that
loses to a deadline.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from manovia_ml.paths import config_dir, data_dir

__all__ = [
    "CATALOGUE_PATH",
    "LICENCE_FRESHNESS_DAYS",
    "TABLE_END",
    "TABLE_START",
    "CatalogueEntry",
    "catalogue_path",
    "format_table",
    "load_catalogue",
    "parse_catalogue",
    "refresh_table",
]

CATALOGUE_PATH = "data/CATALOGUE.md"

#: A licence is a moving target (a re-host changes it, an author adds a
#: non-commercial clause); verification older than this is a warning in the
#: test suite rather than a silent fact.
LICENCE_FRESHNESS_DAYS = 180

TABLE_START = "<!-- catalogue-table:start -->"
TABLE_END = "<!-- catalogue-table:end -->"

_ALLOWED_USE = ("commercial", "research-only", "none")
_VERDICT = ("USE", "USE WITH CONDITIONS", "RESEARCH ONLY", "DO NOT USE")
_STATUS = ("used", "candidate", "planned", "rejected")


class CatalogueEntry(BaseModel):
    """One dataset. Every field the Day-31 brief asks for is required."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    source: str = Field(min_length=1, description="Where the data actually comes from.")
    licence: str = Field(min_length=1)
    licence_evidence: str = Field(
        min_length=1, description="URL where the licence itself was read (not a mirror's claim)."
    )
    allowed_use: Literal["commercial", "research-only", "none"]
    languages: list[str] = Field(min_length=1)
    size: str = Field(min_length=1)
    label_scheme: str = Field(min_length=1)
    known_biases: str = Field(min_length=1)
    personal_data: bool
    personal_data_notes: str = Field(min_length=1)
    status: Literal["used", "candidate", "planned", "rejected"]
    verdict: Literal["USE", "USE WITH CONDITIONS", "RESEARCH ONLY", "DO NOT USE"]
    verdict_reason: str = Field(min_length=1)
    verified_on: date

    @property
    def is_usable(self) -> bool:
        return self.verdict in {"USE", "USE WITH CONDITIONS"}

    @property
    def is_restricted(self) -> bool:
        return self.verdict in {"RESEARCH ONLY", "DO NOT USE"}


_BLOCK = re.compile(
    r"```yaml\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


def catalogue_path() -> Path:
    return data_dir() / "CATALOGUE.md"


def parse_catalogue(text: str, *, origin: str = "CATALOGUE.md") -> list[CatalogueEntry]:
    """Extract every fenced ``yaml`` block carrying a ``catalogue:`` mapping."""

    entries: list[CatalogueEntry] = []
    for match in _BLOCK.finditer(text):
        try:
            payload = yaml.safe_load(match.group("body"))
        except yaml.YAMLError as exc:  # pragma: no cover - invalid YAML is a hard error
            raise ValueError(f"{origin}: invalid YAML in catalogue block: {exc}") from exc
        if not isinstance(payload, dict) or "catalogue" not in payload:
            continue
        block = payload["catalogue"]
        if not isinstance(block, dict):
            raise ValueError(f"{origin}: `catalogue:` must be a mapping")
        try:
            entries.append(CatalogueEntry.model_validate(block))
        except ValidationError as exc:
            name = block.get("name", "<unnamed>")
            raise ValueError(f"{origin}: invalid catalogue entry {name!r}\n{exc}") from exc

    if not entries:
        raise ValueError(f"{origin}: no catalogue entries found")

    names = [entry.name for entry in entries]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"{origin}: duplicate catalogue entries: {duplicates}")
    return entries


def load_catalogue(path: Path | str | None = None) -> list[CatalogueEntry]:
    resolved = Path(path) if path is not None else catalogue_path()
    return parse_catalogue(resolved.read_text(encoding="utf-8"), origin=str(resolved))


def _cell(value: str) -> str:
    """Markdown-table-safe cell: pipes and newlines do not survive a table row."""

    return value.replace("|", "\\|").replace("\n", " ").strip()


def render_table(entries: list[CatalogueEntry]) -> str:
    """The summary table, in catalogue order (which is a deliberate order)."""

    header = (
        "| # | Dataset | Languages | Licence | Allowed use | Personal data | Verdict | Verified |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    rows = [
        "| {i} | {name} | {langs} | {licence} | {use} | {pd} | {verdict} | {when} |".format(
            i=index,
            name=_cell(entry.name),
            langs=_cell(", ".join(entry.languages)),
            licence=_cell(entry.licence),
            use=_cell(entry.allowed_use),
            pd="yes" if entry.personal_data else "no",
            verdict=_cell(entry.verdict),
            when=entry.verified_on.isoformat(),
        )
        for index, entry in enumerate(entries, start=1)
    ]
    return "\n".join([header, *rows])


def format_table(entries: list[CatalogueEntry]) -> str:
    """The table wrapped in its markers, exactly as it must appear in the file."""

    return f"{TABLE_START}\n{render_table(entries)}\n{TABLE_END}"


def current_table(text: str) -> str | None:
    if TABLE_START not in text or TABLE_END not in text:
        return None
    start = text.index(TABLE_START)
    end = text.index(TABLE_END) + len(TABLE_END)
    return text[start:end]


def refresh_table(path: Path | str | None = None, *, write: bool = False) -> bool:
    """Check (or write) the generated table; returns ``True`` when it is current."""

    resolved = Path(path) if path is not None else catalogue_path()
    text = resolved.read_text(encoding="utf-8")
    entries = parse_catalogue(text, origin=str(resolved))
    expected = format_table(entries)
    actual = current_table(text)
    if actual == expected:
        return True
    if not write:
        return False
    if actual is None:
        raise ValueError(f"{resolved}: missing {TABLE_START} / {TABLE_END} markers")
    resolved.write_text(text.replace(actual, expected), encoding="utf-8")
    return True


def restricted_names(entries: list[CatalogueEntry]) -> list[str]:
    return [entry.name for entry in entries if entry.is_restricted]


def entries_named_in_configs(
    names: list[str], *, directory: Path | None = None
) -> dict[str, list[str]]:
    """Which restricted dataset names appear in ``ml/configs/*.yaml`` (must be none)."""

    hits: dict[str, list[str]] = {}
    for path in sorted((directory or config_dir()).glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        for name in names:
            if name and name.lower() in text.lower():
                hits.setdefault(name, []).append(path.name)
    return hits


def validate_entries(entries: list[CatalogueEntry], *, today: date | None = None) -> list[str]:
    """Rule checks that do not need the rest of the repository."""

    problems: list[str] = []
    now = today or date.today()
    for entry in entries:
        if entry.is_usable and entry.allowed_use != "commercial":
            problems.append(
                f"{entry.name}: verdict {entry.verdict!r} but allowed_use={entry.allowed_use!r}"
            )
        if entry.verdict in {"RESEARCH ONLY", "DO NOT USE"} and entry.allowed_use == "commercial":
            problems.append(f"{entry.name}: verdict {entry.verdict!r} cannot be commercial use")
        if entry.personal_data and entry.verdict == "USE":
            problems.append(
                f"{entry.name}: contains personal data and is marked plain USE "
                "(needs at least 'USE WITH CONDITIONS')"
            )
        age = (now - entry.verified_on).days
        if age > LICENCE_FRESHNESS_DAYS:
            problems.append(
                f"{entry.name}: licence last verified {entry.verified_on} "
                f"({age} days ago) — re-verify"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.data.catalogue",
        description="Check or regenerate the summary table in ml/data/CATALOGUE.md.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="fail if the table is stale")
    group.add_argument("--write", action="store_true", help="rewrite the table in place")
    group.add_argument("--table", action="store_true", help="print the table to stdout")
    parser.add_argument("--path", default=None)
    args = parser.parse_args(argv)

    entries = load_catalogue(args.path)

    if args.table:
        print(format_table(entries))
        return 0

    if args.write:
        refresh_table(args.path, write=True)
        print(f"catalogue table updated ({len(entries)} entries)")
        return 0

    problems = validate_entries(entries)
    fresh = refresh_table(args.path, write=False)
    for problem in problems:
        print(f"RULE: {problem}", file=sys.stderr)
    if not fresh:
        print("TABLE: out of date — run `make ml-catalogue-write`", file=sys.stderr)
    if problems or not fresh:
        return 1
    restricted = restricted_names(entries)
    print(
        f"catalogue ok: {len(entries)} entries, table current, "
        f"{len(restricted)} restricted ({', '.join(restricted)})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
