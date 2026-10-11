"""The shipped instruments: wording, bands, provenance, and the guards.

Two jobs here.

First, **wording and bands are pinned against the official source**. The item
text is repeated verbatim in this file on purpose: it is a second, independent
copy, so an edit to the JSON — a typo fix, a "clearer" paraphrase, a translation
that quietly becomes the default — fails here and has to be justified against the
URL recorded in ``provenance``. A screener whose questions have drifted is not
the validated instrument any more, and nothing else in the suite would notice.

Second, **the content validators are exercised**. A band table with a hole, an
item id from the wrong instrument, or a response option set that is not 0-3 must
refuse to load rather than score somebody's answers against a broken table.
"""

from __future__ import annotations

import json
from copy import deepcopy
from importlib.resources import files
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.enums import AssessmentBand, AssessmentInstrument
from app.services.assessments import (
    AssessmentContentError,
    InstrumentDefinition,
    get_instruments,
    load_instruments,
)

PHQ9_ITEMS = (
    "Little interest or pleasure in doing things",
    "Feeling down, depressed, or hopeless",
    "Trouble falling or staying asleep, or sleeping too much",
    "Feeling tired or having little energy",
    "Poor appetite or overeating",
    "Feeling bad about yourself \u2014 or that you are a failure or have let yourself or your "
    "family down",
    "Trouble concentrating on things, such as reading the newspaper or watching television",
    "Moving or speaking so slowly that other people could have noticed? Or the opposite \u2014 "
    "being so fidgety or restless that you have been moving around a lot more than usual",
    "Thoughts that you would be better off dead or of hurting yourself in some way",
)

GAD7_ITEMS = (
    "Feeling nervous, anxious or on edge",
    "Not being able to stop or control worrying",
    "Worrying too much about different things",
    "Trouble relaxing",
    "Being so restless that it is hard to sit still",
    "Becoming easily annoyed or irritable",
    "Feeling afraid as if something awful might happen",
)

OFFICIAL_OPTIONS = ("Not at all", "Several days", "More than half the days", "Nearly every day")

OFFICIAL_BANDS: dict[AssessmentInstrument, tuple[tuple[AssessmentBand, int, int], ...]] = {
    AssessmentInstrument.PHQ9: (
        (AssessmentBand.MINIMAL, 0, 4),
        (AssessmentBand.MILD, 5, 9),
        (AssessmentBand.MODERATE, 10, 14),
        (AssessmentBand.MODERATELY_SEVERE, 15, 19),
        (AssessmentBand.SEVERE, 20, 27),
    ),
    AssessmentInstrument.GAD7: (
        (AssessmentBand.MINIMAL, 0, 4),
        (AssessmentBand.MILD, 5, 9),
        (AssessmentBand.MODERATE, 10, 14),
        (AssessmentBand.SEVERE, 15, 21),
    ),
}

LICENCE_STATEMENT = "No permission required to reproduce, translate, display or distribute."


def raw_content(instrument: str) -> dict[str, Any]:
    """The shipped JSON, as written on disk."""
    path = files("app.content") / "assessments" / f"{instrument}.json"
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def test_both_instruments_load() -> None:
    loaded = get_instruments()
    assert set(loaded) == {AssessmentInstrument.PHQ9, AssessmentInstrument.GAD7}
    assert loaded[AssessmentInstrument.PHQ9].short_name == "PHQ-9"
    assert loaded[AssessmentInstrument.GAD7].short_name == "GAD-7"


@pytest.mark.parametrize(
    ("instrument", "expected_items"),
    [
        (AssessmentInstrument.PHQ9, PHQ9_ITEMS),
        (AssessmentInstrument.GAD7, GAD7_ITEMS),
    ],
)
def test_item_wording_matches_the_official_sheet(
    instrument: AssessmentInstrument, expected_items: tuple[str, ...]
) -> None:
    definition = get_instruments()[instrument]
    assert tuple(item.text for item in definition.items) == expected_items


@pytest.mark.parametrize("instrument", [AssessmentInstrument.PHQ9, AssessmentInstrument.GAD7])
def test_response_options_match_the_official_sheet(instrument: AssessmentInstrument) -> None:
    definition = get_instruments()[instrument]
    assert tuple(option.label for option in definition.response_options) == OFFICIAL_OPTIONS
    assert tuple(option.value for option in definition.response_options) == (0, 1, 2, 3)


@pytest.mark.parametrize(
    ("instrument", "expected"),
    list(OFFICIAL_BANDS.items()),
)
def test_bands_match_the_official_cutpoints(
    instrument: AssessmentInstrument, expected: tuple[tuple[AssessmentBand, int, int], ...]
) -> None:
    definition = get_instruments()[instrument]
    actual = tuple(
        (band.id, band.min, band.max)
        for band in sorted(definition.scoring.bands, key=lambda band: band.min)
    )
    assert actual == expected


@pytest.mark.parametrize("instrument", [AssessmentInstrument.PHQ9, AssessmentInstrument.GAD7])
def test_every_band_carries_reviewable_copy(instrument: AssessmentInstrument) -> None:
    """A band without an explanation, or without help, is not shippable."""
    for band in get_instruments()[instrument].scoring.bands:
        assert band.label
        assert len(band.explanation) > 40
        assert band.what_helps
        assert len(band.when_to_seek_help) > 40


@pytest.mark.parametrize("instrument", [AssessmentInstrument.PHQ9, AssessmentInstrument.GAD7])
def test_every_instrument_says_it_is_not_a_diagnosis(instrument: AssessmentInstrument) -> None:
    definition = get_instruments()[instrument]
    assert "not a diagnosis" in definition.disclaimers.screening_not_diagnosis
    assert definition.disclaimers.how_to_read


@pytest.mark.parametrize("instrument", [AssessmentInstrument.PHQ9, AssessmentInstrument.GAD7])
def test_provenance_records_source_licence_and_loinc(instrument: AssessmentInstrument) -> None:
    provenance = get_instruments()[instrument].provenance
    assert provenance.instrument_id is instrument
    assert provenance.licence_statement == LICENCE_STATEMENT
    assert provenance.licence_source_url.startswith("https://www.phqscreeners.com/")
    assert provenance.item_wording_source_url.startswith("https://www.phqscreeners.com/")
    assert provenance.scoring_source_url.startswith("https://www.phqscreeners.com/")
    assert provenance.item_wording_verified_on == "2026-10-11"
    assert provenance.scoring_source_quote
    assert provenance.citation
    assert provenance.loinc and all(entry["code"] for entry in provenance.loinc)
    assert "not affiliated" in provenance.disclaimer
    # The availability note is the sentence a reviewer needs, so it must say what
    # the terms are and what Manovia does and does not do with them.
    assert "no permission is required" in provenance.availability_note.lower()
    assert "does not relicense" in provenance.availability_note


def test_the_shipped_files_are_what_the_loader_validates() -> None:
    for instrument in AssessmentInstrument:
        raw = raw_content(instrument.value)
        assert raw["instrument"] == instrument.value
        assert raw["schema_version"] == 1
        assert InstrumentDefinition.model_validate(raw).instrument is instrument


def test_phq9_declares_exactly_one_safety_item() -> None:
    definition = get_instruments()[AssessmentInstrument.PHQ9]
    assert definition.safety is not None
    assert definition.safety.item_id == "phq9_9"
    assert definition.safety.rule == "answer_greater_than_zero"
    assert definition.safety.level == "high"
    assert definition.safety.rationale_code == "assessment.phq9_item9"
    flagged = [item.id for item in definition.items if item.creates_crisis_response]
    assert flagged == ["phq9_9"]


def test_gad7_has_no_safety_block_and_no_flagged_item() -> None:
    definition = get_instruments()[AssessmentInstrument.GAD7]
    assert definition.safety is None
    assert not any(item.creates_crisis_response for item in definition.items)


# --------------------------------------------------------------------------- #
# The validators, on mutated copies of the real content
# --------------------------------------------------------------------------- #


def mutated(instrument: str, **changes: Any) -> dict[str, Any]:
    document = deepcopy(raw_content(instrument))
    document.update(changes)
    return document


def test_a_gap_in_the_band_table_is_refused() -> None:
    document = mutated("gad7")
    document["scoring"]["bands"] = [
        band for band in document["scoring"]["bands"] if band["min"] != 10
    ]
    with pytest.raises(ValidationError, match="contiguous"):
        InstrumentDefinition.model_validate(document)


def test_a_band_table_that_stops_short_is_refused() -> None:
    document = mutated("gad7")
    document["scoring"]["bands"][-1]["max"] = 20
    with pytest.raises(ValidationError, match="end at total_max"):
        InstrumentDefinition.model_validate(document)


def test_a_missing_item_is_refused() -> None:
    document = mutated("phq9")
    document["items"] = document["items"][:-1]
    with pytest.raises(ValidationError):
        InstrumentDefinition.model_validate(document)


def test_an_id_from_the_other_instrument_is_refused() -> None:
    document = mutated("phq9")
    document["items"][0]["id"] = "gad7_1"
    with pytest.raises(ValidationError, match="must start with"):
        InstrumentDefinition.model_validate(document)


def test_a_mislabelled_response_option_is_refused() -> None:
    document = mutated("gad7")
    document["response_options"][1]["value"] = 2
    with pytest.raises(ValidationError, match="exactly 0, 1, 2 and 3"):
        InstrumentDefinition.model_validate(document)


def test_an_unflagged_safety_item_is_refused() -> None:
    """A safety block whose item is not flagged would never fire — refuse it."""
    document = mutated("phq9")
    for item in document["items"]:
        item.pop("creates_crisis_response", None)
    with pytest.raises(ValidationError, match="creates_crisis_response"):
        InstrumentDefinition.model_validate(document)


def test_a_wrong_total_range_is_refused() -> None:
    document = mutated("phq9")
    document["scoring"]["total_max"] = 26
    with pytest.raises(ValidationError, match="end at total_max"):
        InstrumentDefinition.model_validate(document)


def test_a_missing_instrument_file_is_refused(tmp_path: Any) -> None:
    with pytest.raises(AssessmentContentError, match="no assessment content"):
        load_instruments(tmp_path)


def test_loading_only_one_instrument_is_refused(tmp_path: Any) -> None:
    (tmp_path / "phq9.json").write_text(json.dumps(raw_content("phq9")), encoding="utf-8")
    with pytest.raises(AssessmentContentError, match="missing instruments"):
        load_instruments(tmp_path)
