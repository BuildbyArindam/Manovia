"""Unit tests for the FHIR R4 document bundle.

This is the local gate. The authoritative check is the official HL7 validator,
which runs in CI (``fhir-validation`` job) against a committed sample bundle;
this sandbox has no JVM, so what lives here is a dependency-free set of
structural and invariant checks written from the R4 specification:

* ``bdl-3`` — a document bundle has an identifier with a system and a value;
* ``bdl-4`` — a document bundle has a timestamp;
* ``bdl-8`` — a document bundle's first entry is the Composition;
* ``sec-1`` — a Composition section has text or entries (so an empty section is
  never emitted);
* references resolve inside the bundle, and fullUrls are unique;
* ``entry.request`` / ``entry.response`` / ``entry.search`` / ``Bundle.total``
  are forbidden in a document bundle.

On top of the shape, the tests pin the two claims that make this document
trustworthy: the LOINC codes are the ones verified on loinc.org (and agree with
the assessment content that already ships), and the Provenance says a **Patient**
authored it and a **Device** assembled it.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.enums import AssessmentInstrument, ShareItem
from app.services.sharing.fhir import (
    BAND_SYSTEM,
    DOCUMENT_KIND_SYSTEM,
    DOCUMENT_TYPE_SYSTEM,
    FHIR_VERSION,
    LOINC_SYSTEM,
    MANOVIA_SYSTEM,
    MOOD_SYSTEM,
    OBSERVATION_CATEGORY_SYSTEM,
    PATIENT_GENERATED_NOTE,
    PROVENANCE_AGENT_SYSTEM,
    RECORD_SECTIONS,
    UCUM_SYSTEM,
    build_bundle,
    bundle_bytes,
    exercise_text,
    thought_record_text,
)
from app.services.sharing.items import DISCLAIMER, DISCLAIMER_TITLE, SharePayload
from app.services.sharing.terminology import get_terminology, loinc_for
from tests.sharing_helpers import NOTE, QUESTIONS, THOUGHT, sample_payload

BUNDLE_ID = uuid.UUID("3f2b8c1a-6d4e-4a5b-9c8d-7e6f5a4b3c2d")
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)

#: Every code system this bundle is allowed to use. Anything else is a term
#: nobody verified, and a wrong code in a clinical document is worse than an
#: honest local one.
ALLOWED_SYSTEMS = {
    LOINC_SYSTEM,
    UCUM_SYSTEM,
    OBSERVATION_CATEGORY_SYSTEM,
    PROVENANCE_AGENT_SYSTEM,
    DOCUMENT_TYPE_SYSTEM,
    DOCUMENT_KIND_SYSTEM,
    MOOD_SYSTEM,
    BAND_SYSTEM,
    f"{MANOVIA_SYSTEM}/share-bundle",
}

#: Elements deliberately absent, each because there is no honest verified value.
FORBIDDEN_ELEMENTS = {"attester", "confidentiality", "questionnaire"}


def build(payload: SharePayload | None = None, **kwargs: Any) -> dict[str, Any]:
    """One bundle, with the ids and the clock fixed."""

    return build_bundle(
        payload or sample_payload(),
        bundle_id=kwargs.pop("bundle_id", BUNDLE_ID),
        now=kwargs.pop("now", NOW),
    )


def resources(bundle: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Every resource of one type, in bundle order."""

    return [
        entry["resource"] for entry in bundle["entry"] if entry["resource"]["resourceType"] == kind
    ]


def walk(value: Any) -> Iterator[tuple[str, Any]]:
    """Every ``(key, value)`` pair in the bundle, at any depth."""

    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def full_urls(bundle: dict[str, Any]) -> set[str]:
    return {entry["fullUrl"] for entry in bundle["entry"]}


def attachment_text(resource: dict[str, Any]) -> str:
    """The decoded inline text of a DocumentReference."""

    data = resource["content"][0]["attachment"]["data"]
    return base64.b64decode(data).decode("utf-8")


# --------------------------------------------------------------------------- #
# document-bundle invariants
# --------------------------------------------------------------------------- #


def test_the_bundle_is_a_document_with_the_required_identifier_and_timestamp() -> None:
    """bdl-3 and bdl-4."""

    bundle = build()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "document"
    assert bundle["id"] == str(BUNDLE_ID)
    assert bundle["identifier"]["system"] == f"{MANOVIA_SYSTEM}/share-bundle"
    assert bundle["identifier"]["value"] == str(BUNDLE_ID)
    assert bundle["timestamp"] == "2026-10-19T12:00:00Z"
    assert "total" not in bundle, "a document bundle must not carry a search total"


def test_the_composition_is_the_first_entry_and_the_provenance_the_last() -> None:
    """bdl-8, and the reading order a person expects."""

    bundle = build()
    assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"
    assert bundle["entry"][-1]["resource"]["resourceType"] == "Provenance"
    kinds = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert kinds.count("Composition") == 1
    assert kinds.count("Provenance") == 1
    assert kinds.count("Patient") == 1
    assert kinds.count("Device") == 1


def test_every_entry_has_a_unique_urn_fullurl() -> None:
    """Unique fullUrls are what make the references below resolvable."""

    bundle = build()
    urls = [entry["fullUrl"] for entry in bundle["entry"]]
    assert len(urls) == len(set(urls))
    assert all(url.startswith("urn:uuid:") for url in urls)
    assert all(uuid.UUID(url.removeprefix("urn:uuid:")) for url in urls)
    # And each entry's resource id matches its fullUrl.
    for entry in bundle["entry"]:
        assert entry["fullUrl"] == f"urn:uuid:{entry['resource']['id']}"


def test_no_entry_carries_a_request_response_or_search_block() -> None:
    """Those belong to batch, transaction, history and searchset bundles."""

    bundle = build()
    for entry in bundle["entry"]:
        assert set(entry) == {"fullUrl", "resource"}, sorted(entry)


def test_every_reference_resolves_inside_the_bundle() -> None:
    """A dangling reference is the classic way a document bundle becomes useless."""

    bundle = build()
    urls = full_urls(bundle)
    references = [value for key, value in walk(bundle) if key == "reference"]
    assert references, "the bundle should be full of references"
    for reference in references:
        assert reference in urls, reference


def test_every_code_system_is_one_this_feature_is_allowed_to_use() -> None:
    """No borrowed terminology: verified LOINC, HL7's own value sets, or ours."""

    bundle = build()
    codings = [coding for key, value in walk(bundle) if key == "coding" for coding in value]
    assert codings
    systems = {str(coding["system"]) for coding in codings}
    assert systems <= ALLOWED_SYSTEMS, sorted(systems - ALLOWED_SYSTEMS)
    # Every coding also carries a human display, so a reader without a
    # terminology server still understands the document.
    assert all(str(coding.get("display", "")).strip() for coding in codings)
    # LOINC appears, and only for the two verified totals.
    assert {str(coding["code"]) for coding in codings if coding["system"] == LOINC_SYSTEM} == {
        "44261-6",
        "70274-6",
    }


def test_the_elements_without_an_honest_value_are_absent() -> None:
    """No attester, no confidentiality label, no unresolvable Questionnaire."""

    bundle = build()
    keys = {key for key, _ in walk(bundle)}
    assert not (keys & FORBIDDEN_ELEMENTS), sorted(keys & FORBIDDEN_ELEMENTS)
    # Provenance.entity would claim a source we cannot cite; it is absent too.
    provenance = resources(bundle, "Provenance")[0]
    assert "entity" not in provenance


def test_instants_are_zulu_time_with_second_precision() -> None:
    """FHIR ``instant`` is what the validator will check these against."""

    bundle = build()
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    instants = [
        value
        for key, value in walk(bundle)
        if key
        in {
            "timestamp",
            "recorded",
            "occurredDateTime",
            "effectiveDateTime",
            "date",
            "authored",
            "creation",
        }
    ]
    assert instants
    for value in instants:
        assert pattern.match(str(value)), value


# --------------------------------------------------------------------------- #
# Composition and sections
# --------------------------------------------------------------------------- #


def test_the_composition_names_what_this_document_is() -> None:
    """Patient-generated, in the title, and authored by the patient."""

    bundle = build()
    composition = resources(bundle, "Composition")[0]
    assert composition["status"] == "final"
    assert DISCLAIMER_TITLE in composition["title"]
    assert composition["type"]["coding"][0]["system"] == DOCUMENT_TYPE_SYSTEM
    assert composition["type"]["coding"][0]["code"] == "appointment-summary"
    assert composition["date"] == "2026-10-19T12:00:00Z"
    patient_url = resources(bundle, "Patient")[0]["id"]
    assert composition["author"] == [{"reference": f"urn:uuid:{patient_url}"}]
    assert composition["subject"] == composition["author"][0]


def test_sections_exist_only_for_items_that_have_content() -> None:
    """sec-1: never a heading with nothing under it."""

    bundle = build()
    sections = resources(bundle, "Composition")[0]["section"]
    assert [section["title"] for section in sections] == [
        "Mood summary",
        "Screening history",
        "Thought records",
        "Exercise helpfulness",
        "Note for the appointment",
        "Questions to ask",
    ]
    urls = full_urls(bundle)
    for section in sections:
        assert section["entry"], section["title"]
        assert section["code"]["coding"][0]["system"] == DOCUMENT_KIND_SYSTEM
        assert all(reference["reference"] in urls for reference in section["entry"])
        # Every section entry is also a Provenance target.
        targets = {target["reference"] for target in resources(bundle, "Provenance")[0]["target"]}
        assert {reference["reference"] for reference in section["entry"]} <= targets


def test_a_payload_with_one_item_produces_one_section() -> None:
    """The bundle is as small as the selection."""

    bundle = build(
        sample_payload(
            items=[ShareItem.NOTE],
            mood_trend=None,
            assessments=None,
            thought_records=None,
            exercise_helpfulness=None,
            questions=None,
            identity=None,
        )
    )
    sections = resources(bundle, "Composition")[0]["section"]
    assert [section["title"] for section in sections] == ["Note for the appointment"]
    assert resources(bundle, "Observation") == []
    assert resources(bundle, "QuestionnaireResponse") == []


def test_a_payload_with_no_content_has_no_section_element_at_all() -> None:
    """An empty array would be legal but would say nothing."""

    bundle = build(
        sample_payload(
            items=[],
            mood_trend=None,
            assessments=None,
            thought_records=None,
            exercise_helpfulness=None,
            note=None,
            questions=None,
            identity=None,
        )
    )
    assert "section" not in resources(bundle, "Composition")[0]
    kinds = [entry["resource"]["resourceType"] for entry in bundle["entry"]]
    assert kinds == ["Composition", "Patient", "Device", "Provenance"]


# --------------------------------------------------------------------------- #
# provenance: the point of the exercise
# --------------------------------------------------------------------------- #


def test_the_provenance_says_a_patient_wrote_it_and_a_device_assembled_it() -> None:
    """Two agents, in that order, from HL7's own participant-type value set."""

    bundle = build()
    provenance = resources(bundle, "Provenance")[0]
    agents = provenance["agent"]
    assert [agent["type"]["coding"][0]["code"] for agent in agents] == ["author", "assembler"]
    assert all(agent["type"]["coding"][0]["system"] == PROVENANCE_AGENT_SYSTEM for agent in agents)

    by_kind = {entry["resource"]["resourceType"]: entry["fullUrl"] for entry in bundle["entry"]}
    assert agents[0]["who"] == {"reference": by_kind["Patient"]}
    assert agents[1]["who"] == {"reference": by_kind["Device"]}
    assert provenance["occurredDateTime"] == "2026-10-19T12:00:00Z"
    assert provenance["recorded"] == "2026-10-19T12:00:00Z"
    # Every content resource is a target, and the Composition is the first.
    targets = [target["reference"] for target in provenance["target"]]
    assert targets[0] == by_kind["Composition"]
    assert len(targets) == len(bundle["entry"]) - 1, "everything but the Provenance itself"


def test_the_device_does_not_claim_to_be_a_medical_device_or_an_ehr() -> None:
    """The text a human reads says what Manovia is and is not."""

    bundle = build()
    device = resources(bundle, "Device")[0]
    assert device["status"] == "active"
    assert device["deviceName"][0]["name"] == "Manovia"
    assert device["version"][0]["value"] == "1.0.0"
    assert "not a medical device" in device["type"]["text"]
    assert "not an EHR" in device["type"]["text"]


def test_the_patient_carries_an_email_only_when_the_person_ticked_identity() -> None:
    """Anonymous by construction, not by omission."""

    bundle = build()
    patient = resources(bundle, "Patient")[0]
    assert patient["active"] is True
    assert patient["telecom"] == [{"system": "email", "value": "someone@example.test"}]
    assert "name" not in patient, "a name is never in the payload, so never here"
    assert "birthDate" not in patient
    assert "gender" not in patient

    anonymous = build(sample_payload(identity=None))
    assert "telecom" not in resources(anonymous, "Patient")[0]

    guest = build(sample_payload(identity="Anonymous account (no email on file)"))
    assert "telecom" not in resources(guest, "Patient")[0]


# --------------------------------------------------------------------------- #
# assessments: the LOINC-coded part
# --------------------------------------------------------------------------- #


def test_each_screening_becomes_a_questionnaire_response_and_a_loinc_observation() -> None:
    """Two results in, two pairs out, with the totals and the bands."""

    bundle = build()
    responses = resources(bundle, "QuestionnaireResponse")
    observations = [
        resource
        for resource in resources(bundle, "Observation")
        if resource["code"]["coding"][0]["system"] == LOINC_SYSTEM
    ]
    assert len(responses) == 2
    assert len(observations) == 2

    terms = {
        instrument: loinc_for(instrument)
        for instrument in (AssessmentInstrument.PHQ9, AssessmentInstrument.GAD7)
    }
    by_code = {resource["code"]["coding"][0]["code"]: resource for resource in observations}
    assert set(by_code) == {"44261-6", "70274-6"}

    for instrument, total, band in (
        (AssessmentInstrument.GAD7, 9, "Mild"),
        (AssessmentInstrument.PHQ9, 12, "Moderate"),
    ):
        term = terms[instrument]
        observation = by_code[term.code]
        coding = observation["code"]["coding"][0]
        assert coding["display"] == term.display
        assert observation["status"] == "final"
        assert observation["category"][0]["coding"][0]["code"] == "survey"
        assert observation["category"][0]["coding"][0]["system"] == OBSERVATION_CATEGORY_SYSTEM
        quantity = observation["valueQuantity"]
        assert quantity["value"] == total
        assert quantity["unit"] == "{score}"
        assert quantity["code"] == "{score}"
        assert quantity["system"] == UCUM_SYSTEM
        interpretation = observation["interpretation"][0]
        assert interpretation["coding"][0]["system"] == BAND_SYSTEM
        assert band.lower() in interpretation["coding"][0]["code"]
        assert "not a diagnosis" in interpretation["text"].lower()
        assert "not a diagnosis" in observation["note"][0]["text"].lower()


def test_the_observation_is_derived_from_its_own_questionnaire_response() -> None:
    """The total and the answers-it-came-from are linked, not duplicated."""

    bundle = build()
    responses = {
        entry["fullUrl"]: entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "QuestionnaireResponse"
    }
    observations = [
        resource
        for resource in resources(bundle, "Observation")
        if resource["code"]["coding"][0]["system"] == LOINC_SYSTEM
    ]
    for observation in observations:
        derived = observation["derivedFrom"]
        assert len(derived) == 1
        assert derived[0]["reference"] in responses
        response = responses[derived[0]["reference"]]
        assert response["item"][0]["linkId"] == observation["code"]["coding"][0]["code"]
        assert (
            response["item"][0]["answer"][0]["valueInteger"]
            == observation["valueQuantity"]["value"]
        )


def test_the_questionnaire_response_is_attributed_to_the_patient() -> None:
    """Self-administered, self-reported, and no canonical we cannot resolve."""

    bundle = build()
    patient = resources(bundle, "Patient")[0]["id"]
    for response in resources(bundle, "QuestionnaireResponse"):
        assert response["status"] == "completed"
        assert response["author"] == {"reference": f"urn:uuid:{patient}"}
        assert response["source"] == {"reference": f"urn:uuid:{patient}"}
        assert response["subject"] == response["author"]
        assert response["authored"] in {"2026-10-12T12:00:00Z", "2026-10-11T12:00:00Z"}
        assert "Individual item answers are not shared" in response["text"]["div"]
        assert len(response["item"]) == 1, "the total, never the ten questions"


def test_a_questionnaire_response_has_no_note_because_r4_has_no_such_element() -> None:
    """The property the official validator rejected, asserted absent.

    ``validator_cli.jar`` 7.0.2 against FHIR 4.0.1 answered this bundle with
    "Error @ Bundle.entry[7].resource: Unrecognized property 'note'" and exited
    1, which is what turned the CI ``fhir-validation`` job red. R4 puts prose
    about a resource in ``DomainResource.text``; of the types emitted here only
    ``Observation`` has a ``note``. ``scripts/fhir_offline_check.py`` carries the
    same rule in ``UNDEFINED_PROPERTIES`` so it fails offline too.
    """

    bundle = build()
    responses = resources(bundle, "QuestionnaireResponse")
    assert responses, "the sample payload has two screening results"
    for response in responses:
        assert "note" not in response
        assert PATIENT_GENERATED_NOTE in response["text"]["div"]
    # The Observation that derives from each response still carries its own note,
    # so the sentence reaches a reader who opens either resource.
    for observation in resources(bundle, "Observation"):
        if observation.get("derivedFrom"):
            assert PATIENT_GENERATED_NOTE in observation["note"][0]["text"]


def test_the_questionnaire_response_narrative_is_escaped_xhtml() -> None:
    """Narrative is parsed as XHTML by the validator, so it must be well-formed.

    The instrument name and band label come from assessment content files rather
    than from a person, but the escaping is what keeps a stray ``<`` in either
    from producing a document the validator rejects — or one that renders as
    markup in a clinician's viewer.
    """

    payload = sample_payload()
    assessments = payload.assessments
    assert assessments is not None, "the sample payload carries both screening results"
    hostile = [
        assessment.model_copy(
            update={
                "instrument_name": 'PHQ-9 & <b id="x">',
                "band_label": "<script>alert('x')</script>",
            }
        )
        for assessment in assessments
    ]
    bundle = build(payload.model_copy(update={"assessments": hostile}))
    for response in resources(bundle, "QuestionnaireResponse"):
        narrative = response["text"]
        assert narrative["status"] == "generated"
        div: str = narrative["div"]
        assert div.startswith('<div xmlns="http://www.w3.org/1999/xhtml">')
        assert div.endswith("</div>")
        # Well-formed XML, and the only elements in it are the paragraphs.
        parsed = ElementTree.fromstring(div)
        assert {child.tag.rpartition("}")[2] for child in parsed} == {"p"}
        assert "<script>" not in div and "&lt;script&gt;" in div
        assert 'PHQ-9 &amp; &lt;b id="x"&gt;' in div, "quotes stay literal in text content"
        assert PATIENT_GENERATED_NOTE in div


def test_the_loinc_codes_are_the_verified_ones_and_agree_with_the_content_files() -> None:
    """One set of facts: the terminology file, the assessment content, the bundle."""

    terms = get_terminology()
    for code in terms.codes:
        assert code.source_url == f"https://loinc.org/{code.code}"
        assert code.status == "active"

    bundle_json = json.dumps(build())
    for instrument in (AssessmentInstrument.PHQ9, AssessmentInstrument.GAD7):
        assert loinc_for(instrument).code in bundle_json

    content_dir = Path(__file__).resolve().parents[2] / "app" / "content"
    for filename, expected in (("phq9.json", "44261-6"), ("gad7.json", "70274-6")):
        content = json.loads((content_dir / "assessments" / filename).read_text(encoding="utf-8"))
        assert expected in json.dumps(content["provenance"]), filename


# --------------------------------------------------------------------------- #
# mood, thought records, exercise, note, questions
# --------------------------------------------------------------------------- #


def test_the_mood_summary_is_observations_about_numbers_only() -> None:
    """Two means, a count and a direction — never a check-in's note."""

    bundle = build()
    mood = [
        resource
        for resource in resources(bundle, "Observation")
        if resource["code"]["coding"][0]["system"] == MOOD_SYSTEM
    ]
    codes = [resource["code"]["coding"][0]["code"] for resource in mood]
    assert codes == ["mean-valence", "mean-energy", "check-in-count", "direction"]
    by_code = dict(zip(codes, mood, strict=True))
    assert by_code["mean-valence"]["valueQuantity"]["value"] == 3.0
    assert by_code["mean-energy"]["valueQuantity"]["value"] == 3.5
    assert by_code["check-in-count"]["valueQuantity"]["value"] == 6
    assert by_code["check-in-count"]["valueQuantity"]["unit"] == "1"
    direction = by_code["direction"]["valueCodeableConcept"]
    assert direction["coding"][0]["code"] == "higher"
    assert "second half" in direction["text"]
    for resource in mood:
        assert resource["status"] == "final"
        assert "mood check-ins" in resource["note"][0]["text"]
        assert PATIENT_GENERATED_NOTE in resource["note"][0]["text"]


def test_a_mood_summary_without_means_omits_those_observations() -> None:
    """No check-ins means no average, and an invented 0 would be a lie."""

    trend = sample_payload().mood_trend
    assert trend is not None
    payload = sample_payload(
        mood_trend=trend.model_copy(
            update={"mean_valence": None, "mean_energy": None, "entries": 0}
        )
    )
    mood = [
        resource
        for resource in resources(build(payload), "Observation")
        if resource["code"]["coding"][0]["system"] == MOOD_SYSTEM
    ]
    codes = [resource["code"]["coding"][0]["code"] for resource in mood]
    assert codes == ["check-in-count", "direction"]


def test_thought_records_become_inline_text_documents_in_full() -> None:
    """All five sections, the intensities, what helped — and the banner."""

    bundle = build()
    documents = [
        resource
        for resource in resources(bundle, "DocumentReference")
        if resource["category"][0]["coding"][0]["code"] == "thought-record"
    ]
    assert len(documents) == 1
    text = attachment_text(documents[0])
    for _, heading in RECORD_SECTIONS:
        assert heading in text
    for value in THOUGHT.values():
        assert value in text
    assert "80" in text and "45" in text
    assert "looking at evidence" in text
    assert DISCLAIMER in text
    assert documents[0]["status"] == "current"
    assert documents[0]["docStatus"] == "final"
    assert documents[0]["content"][0]["attachment"]["contentType"] == "text/plain"
    assert PATIENT_GENERATED_NOTE in documents[0]["description"]


def test_the_note_and_questions_are_carried_verbatim() -> None:
    """What the person wrote, not a paraphrase of it."""

    bundle = build()
    by_kind = {
        resource["category"][0]["coding"][0]["code"]: attachment_text(resource)
        for resource in resources(bundle, "DocumentReference")
    }
    assert by_kind["note"] == NOTE
    assert by_kind["questions"] == "\n".join(f"- {question}" for question in QUESTIONS)
    assert "association" in by_kind["exercise-helpfulness"]
    assert "Largest average improvement: Box breathing" in by_kind["exercise-helpfulness"]
    assert DISCLAIMER in by_kind["exercise-helpfulness"]


def test_the_narrative_helpers_are_plain_text_with_the_banner() -> None:
    """Used by the DocumentReferences; tested on their own so a change is visible."""

    payload = sample_payload()
    record = (payload.thought_records or [])[0]
    record_text = thought_record_text(record)
    # The heading lives in the DocumentReference title; the body starts with the
    # date and ends with the banner.
    assert record_text.startswith("Date: 2026-10-16")
    assert record_text.endswith(DISCLAIMER)
    assert all(heading in record_text for _, heading in RECORD_SECTIONS)

    summary = payload.exercise_helpfulness
    assert summary is not None
    text = exercise_text(summary)
    assert "Window: the last 30 days" in text
    assert "mean change +1.67" in text
    assert text.endswith(DISCLAIMER)


# --------------------------------------------------------------------------- #
# determinism and the wire format
# --------------------------------------------------------------------------- #


def test_the_same_inputs_give_byte_identical_output() -> None:
    """Re-building a share must not change the document the owner confirmed."""

    payload = sample_payload()
    first = bundle_bytes(payload, bundle_id=BUNDLE_ID, now=NOW)
    second = bundle_bytes(payload, bundle_id=BUNDLE_ID, now=NOW)
    assert first == second
    assert isinstance(first, bytes)
    assert json.loads(first) == build(payload)


def test_resource_ids_follow_the_bundle_id_and_nothing_else() -> None:
    """Different share, different ids — same shape."""

    other = build(bundle_id=uuid.uuid4())
    assert other["id"] != str(BUNDLE_ID)
    assert full_urls(other) != full_urls(build())
    assert [entry["resource"]["resourceType"] for entry in other["entry"]] == [
        entry["resource"]["resourceType"] for entry in build()["entry"]
    ]


def test_resource_type_is_always_the_first_key() -> None:
    """Readability in a hand-inspected bundle, and the FHIR convention."""

    raw = bundle_bytes(sample_payload(), bundle_id=BUNDLE_ID, now=NOW).decode("utf-8")
    # The bundle itself and every resource inside it open with their type.
    assert raw.lstrip().startswith('{\n  "resourceType": "Bundle"')
    occurrences = raw.count('"resourceType":')
    first_keys = len(re.findall(r'\{\s*\n\s*"resourceType":', raw))
    assert first_keys == occurrences, "every resourceType is the first key of its object"


def test_the_bundle_survives_a_json_round_trip_with_unicode_intact() -> None:
    """``ensure_ascii=False``: text in another script stays readable, not escaped.

    The narrative items travel base64-encoded inside their DocumentReference (as
    FHIR requires for inline attachments), so the note is checked after decoding;
    the identity line is a plain JSON string and is checked in the raw bytes.
    """

    note = 'हिन्दी और বাংলা text with an em dash — and a quote "'
    payload = sample_payload(note=note, identity="हिन्दी <someone@example.test>")
    raw = bundle_bytes(payload, bundle_id=BUNDLE_ID, now=NOW)
    text = raw.decode("utf-8")
    assert "हिन्दी" in text, "ensure_ascii=False keeps the script readable"
    assert "\\u0939" not in text

    restored = json.loads(text)
    documents = [
        resource
        for resource in resources(restored, "DocumentReference")
        if resource["category"][0]["coding"][0]["code"] == "note"
    ]
    assert attachment_text(documents[0]) == note
    assert resources(restored, "Patient")[0]["telecom"][0]["value"] == payload.identity


def test_the_fhir_version_constant_is_r4() -> None:
    """The CI validator is invoked with ``-version 4.0.1``; keep them together."""

    assert FHIR_VERSION == "4.0.1"
