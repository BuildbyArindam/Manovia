"""The catalogue is enforced, not trusted.

The interesting tests here are the last three: the generated table cannot drift,
a licence/verdict pair cannot contradict itself, and a DO NOT USE dataset cannot
be named in a config. That third one is the whole reason the catalogue is
machine-readable.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from manovia_ml.data.catalogue import (
    LICENCE_FRESHNESS_DAYS,
    TABLE_END,
    TABLE_START,
    CatalogueEntry,
    catalogue_path,
    entries_named_in_configs,
    format_table,
    load_catalogue,
    parse_catalogue,
    refresh_table,
    restricted_names,
    validate_entries,
)

MINIMAL = """
### 1. Something

```yaml
catalogue:
  name: Something
  source: https://example.invalid/something
  licence: CC0-1.0
  licence_evidence: https://example.invalid/licence
  allowed_use: commercial
  languages: [en]
  size: "10 rows"
  label_scheme: "two labels"
  known_biases: "tiny"
  personal_data: false
  personal_data_notes: "invented"
  status: used
  verdict: USE
  verdict_reason: "because"
  verified_on: 2026-10-13
```
"""


def test_the_shipped_catalogue_parses_and_passes_its_rules() -> None:
    entries = load_catalogue()

    assert len(entries) >= 14
    assert validate_entries(entries) == []
    assert refresh_table(write=False) is True


def test_shipped_catalogue_has_the_expected_verdict_spread() -> None:
    entries = {entry.name: entry for entry in load_catalogue()}

    assert entries["GoEmotions"].verdict == "USE WITH CONDITIONS"
    assert entries["GoEmotions"].licence == "Apache-2.0"
    assert entries["EmoNoBa"].verdict == "USE WITH CONDITIONS"
    assert entries["emoHind-58K"].verdict == "DO NOT USE"
    assert entries["Manovia fixtures (evals/datasets/*, ml/data/toy/*)"].verdict == "USE"


def test_every_non_commercial_dataset_is_restricted() -> None:
    for entry in load_catalogue():
        if entry.allowed_use in {"research-only", "none"}:
            assert entry.verdict in {"RESEARCH ONLY", "DO NOT USE"}, entry.name


def test_no_restricted_dataset_is_named_in_a_config() -> None:
    """The rule that loses to a deadline when it is only written in prose."""

    names = restricted_names(load_catalogue())
    hits = entries_named_in_configs(names)

    assert hits == {}, f"restricted datasets named in configs: {hits}"


def test_generated_table_matches_the_committed_one() -> None:
    entries = load_catalogue()
    text = catalogue_path().read_text(encoding="utf-8")

    assert format_table(entries) in text
    assert text.index(TABLE_START) < text.index(TABLE_END)


def test_refresh_table_detects_and_fixes_drift(tmp_path: Path) -> None:
    path = tmp_path / "CATALOGUE.md"
    path.write_text(
        MINIMAL + f"\n{TABLE_START}\n| stale | table |\n{TABLE_END}\n", encoding="utf-8"
    )

    assert refresh_table(path, write=False) is False
    assert refresh_table(path, write=True) is True
    assert "Something" in path.read_text(encoding="utf-8")
    assert "stale" not in path.read_text(encoding="utf-8")


def test_parse_rejects_a_missing_required_field() -> None:
    broken = MINIMAL.replace('  known_biases: "tiny"\n', "")

    with pytest.raises(ValueError, match="known_biases"):
        parse_catalogue(broken)


def test_parse_rejects_an_unknown_field() -> None:
    broken = MINIMAL.replace("  status: used\n", "  status: used\n  vibes: good\n")

    with pytest.raises(ValueError, match="vibes"):
        parse_catalogue(broken)


def test_parse_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_catalogue(MINIMAL + MINIMAL)


def test_parse_rejects_a_file_with_no_entries() -> None:
    with pytest.raises(ValueError, match="no catalogue entries"):
        parse_catalogue("# nothing here\n")


def test_verdict_and_allowed_use_cannot_contradict() -> None:
    entry = CatalogueEntry.model_validate(
        {
            "name": "Bad",
            "source": "https://example.invalid",
            "licence": "CC BY-NC 4.0",
            "licence_evidence": "https://example.invalid",
            "allowed_use": "research-only",
            "languages": ["en"],
            "size": "1",
            "label_scheme": "one",
            "known_biases": "many",
            "personal_data": False,
            "personal_data_notes": "none",
            "status": "rejected",
            "verdict": "USE",
            "verdict_reason": "none",
            "verified_on": "2026-10-13",
        }
    )

    problems = validate_entries([entry])

    assert any("allowed_use" in problem for problem in problems)


def test_personal_data_cannot_be_plain_use() -> None:
    entry = CatalogueEntry.model_validate(
        {
            "name": "PeopleData",
            "source": "https://example.invalid",
            "licence": "CC BY 4.0",
            "licence_evidence": "https://example.invalid",
            "allowed_use": "commercial",
            "languages": ["en"],
            "size": "1",
            "label_scheme": "one",
            "known_biases": "many",
            "personal_data": True,
            "personal_data_notes": "real people",
            "status": "candidate",
            "verdict": "USE",
            "verdict_reason": "none",
            "verified_on": "2026-10-13",
        }
    )

    problems = validate_entries([entry])

    assert any("personal data" in problem for problem in problems)


def test_licence_verification_goes_stale() -> None:
    entry = load_catalogue()[0]
    stale = entry.model_copy(
        update={"verified_on": date.today() - timedelta(days=LICENCE_FRESHNESS_DAYS + 1)}
    )

    problems = validate_entries([stale])

    assert any("re-verify" in problem for problem in problems)


def test_entries_named_in_configs_is_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text("name: goemotions-run\n", encoding="utf-8")

    hits = entries_named_in_configs(["GoEmotions"], directory=tmp_path)

    assert hits == {"GoEmotions": ["x.yaml"]}


def test_catalogue_has_a_freshness_note() -> None:
    text = catalogue_path().read_text(encoding="utf-8")

    assert "Next verification due" in text
