"""Unit tests for the verified terminology the FHIR export is allowed to claim.

The point of this module is that a clinical code in a document somebody may act
on is not a free-text choice. These tests pin the two codes to the pages they
were read from, check that the loader refuses a file that cannot be trusted, and
check that the codes agree with the assessment content that already ships — so
the two places LOINC appears in this codebase cannot drift apart.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.models.enums import AssessmentInstrument
from app.services.sharing.terminology import (
    LOINC_FILE,
    LOINC_SYSTEM,
    Terminology,
    TerminologyError,
    get_terminology,
    load_terminology,
    loinc_for,
)

CONTENT_DIR = Path(__file__).resolve().parents[2] / "app" / "content"


def shipped() -> dict[str, Any]:
    """The shipped file, as JSON, for tests that mutate a copy of it."""

    raw = json.loads((CONTENT_DIR / "sharing" / LOINC_FILE).read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def write(tmp_path: Path, raw: dict[str, Any]) -> Path:
    """Write a candidate terminology file and return its directory."""

    (tmp_path / LOINC_FILE).write_text(json.dumps(raw), encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------- #
# the shipped facts
# --------------------------------------------------------------------------- #


def test_the_two_codes_are_the_verified_total_scores() -> None:
    """PHQ-9 total 44261-6 and GAD-7 total 70274-6, exactly as loinc.org lists them."""

    terms = get_terminology()
    assert terms.code_system == LOINC_SYSTEM == "http://loinc.org"
    assert terms.verified_against == "https://loinc.org"
    assert terms.verified_on == date(2026, 10, 19)
    assert len(terms.codes) == 2

    phq9 = loinc_for(AssessmentInstrument.PHQ9)
    assert phq9.code == "44261-6"
    assert phq9.key == "phq9_total"
    assert phq9.display == "Patient Health Questionnaire 9 item (PHQ-9) total score [Reported]"
    assert phq9.source_url == "https://loinc.org/44261-6"
    assert phq9.panel_code == "44249-1", "the panel code is recorded but never used as the total"

    gad7 = loinc_for(AssessmentInstrument.GAD7)
    assert gad7.code == "70274-6"
    assert gad7.key == "gad7_total"
    assert gad7.display == "Generalized anxiety disorder 7 item (GAD-7) total score [Reported.PHQ]"
    assert gad7.source_url == "https://loinc.org/70274-6"
    assert gad7.panel_code == "69737-5"

    for term in (phq9, gad7):
        assert term.status == "active"
        assert term.scale == "Qn"
        assert term.method == "Reported.PHQ"
        assert term.ucum_unit == "{score}"
        # The fully specified name carries LOINC's six axes; the two that matter
        # here are the scale (a score) and the method (self-reported).
        assert ":Score:Pt:" in term.fully_specified_name
        assert term.fully_specified_name.endswith("Qn:Reported.PHQ")


def test_the_codes_agree_with_the_assessment_content_that_already_ships() -> None:
    """One set of facts, two files: they must not contradict each other."""

    for instrument, filename, expected in (
        (AssessmentInstrument.PHQ9, "phq9.json", "44261-6"),
        (AssessmentInstrument.GAD7, "gad7.json", "70274-6"),
    ):
        content = json.loads((CONTENT_DIR / "assessments" / filename).read_text(encoding="utf-8"))
        provenance = content["provenance"]
        loinc_entries = provenance["loinc"]
        codes = (
            [entry["code"] if isinstance(entry, dict) else entry for entry in loinc_entries]
            if isinstance(loinc_entries, list)
            else [loinc_entries]
        )
        assert expected in json.dumps(loinc_entries), (filename, codes)
        assert loinc_for(instrument).code == expected


def test_the_note_records_why_the_panel_codes_are_not_used() -> None:
    """The file explains itself, so the next reader does not 'fix' it."""

    note = get_terminology().note.lower()
    assert "loinc" in note
    assert "44249-1" in note and "69737-5" in note
    assert "not" in note


# --------------------------------------------------------------------------- #
# the loader refuses a file it cannot trust
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda raw: raw.update(verified_against="https://example.com"), id="wrong-site"
        ),
        pytest.param(
            lambda raw: raw.update(code_system="http://snomed.info/sct"), id="wrong-system"
        ),
        pytest.param(lambda raw: raw.update(verified_on="19/10/2026"), id="bad-date"),
        pytest.param(lambda raw: raw.update(schema_version=2), id="unknown-schema"),
        pytest.param(
            lambda raw: raw["codes"][0].update(source_url="https://loinc.org/99999-9"),
            id="url-cites-another-code",
        ),
        pytest.param(
            lambda raw: raw["codes"][0].update(source_url="https://example.com/44261-6"),
            id="url-not-loinc",
        ),
        pytest.param(
            lambda raw: raw["codes"][0].update(source_url="http://loinc.org/44261-6"),
            id="url-not-https",
        ),
        pytest.param(
            lambda raw: raw["codes"][0].update(panel_source_url="https://loinc.org/44261-6"),
            id="panel-url-mismatch",
        ),
        pytest.param(
            lambda raw: raw["codes"][0].update(code="44261"), id="code-without-check-digit"
        ),
        pytest.param(lambda raw: raw["codes"][0].update(status="deprecated"), id="inactive-code"),
        pytest.param(lambda raw: raw["codes"][0].update(ucum_unit="1"), id="wrong-unit"),
        pytest.param(lambda raw: raw["codes"][0].update(scale="Ord"), id="wrong-scale"),
        pytest.param(lambda raw: raw["codes"][0].update(display=""), id="empty-display"),
        pytest.param(lambda raw: raw["codes"][0].update(surprise="x"), id="unknown-field"),
        pytest.param(
            lambda raw: raw["codes"][1].update(instrument="phq9"), id="duplicate-instrument"
        ),
        pytest.param(lambda raw: raw["codes"][1].update(key="phq9_total"), id="duplicate-key"),
        pytest.param(lambda raw: raw.update(codes=raw["codes"][:1]), id="only-one-code"),
        pytest.param(lambda raw: raw.update(codes=[]), id="no-codes"),
        pytest.param(lambda raw: raw.pop("note"), id="no-note"),
    ],
)
def test_a_file_that_cannot_be_trusted_is_refused(tmp_path: Path, mutate: Any) -> None:
    """Every way of getting terminology wrong fails at load, not at export."""

    raw = shipped()
    mutate(raw)
    directory = write(tmp_path, raw)
    with pytest.raises(TerminologyError):
        load_terminology(directory)


def test_a_missing_or_unreadable_file_is_refused(tmp_path: Path) -> None:
    """No silent fallback to an empty bundle."""

    with pytest.raises(TerminologyError):
        load_terminology(tmp_path)
    (tmp_path / LOINC_FILE).write_text("{not json", encoding="utf-8")
    with pytest.raises(TerminologyError):
        load_terminology(tmp_path)


def test_a_valid_replacement_file_is_accepted(tmp_path: Path) -> None:
    """The loader is strict, not broken: a well-formed file loads."""

    raw = shipped()
    raw["codes"][0]["code"] = "99999-9"
    raw["codes"][0]["source_url"] = "https://loinc.org/99999-9"
    directory = write(tmp_path, raw)
    terms = load_terminology(directory)
    assert isinstance(terms, Terminology)
    assert terms.codes[0].code == "99999-9"


def test_the_shipped_file_loads_and_is_cached() -> None:
    """One validation per process for release content."""

    assert load_terminology() == get_terminology()
    assert get_terminology() is get_terminology()
