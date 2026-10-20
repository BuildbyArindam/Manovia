"""The committed FHIR sample bundle: current, valid, and worth validating.

Three claims are pinned here, and each has a reason to exist as a test:

* ``backend/tests/fixtures/fhir/sample_share_bundle.json`` is what the *current*
  builder produces. The CI ``fhir-validation`` job validates that file with the
  official HL7 validator, so a stale artifact would mean CI blessing a document
  shape the code no longer emits. The builder is deterministic (``uuid5`` ids,
  pinned instant, two-space JSON), so this is a byte comparison and not a
  fuzzy one.
* The file passes ``backend/scripts/fhir_offline_check.py``, the dependency-free
  fallback behind ``make fhir-validate``.
* That checker actually catches things. A structural checker that passes
  whatever it is handed is worse than none — it reads like a guarantee — so every
  rule family gets a mutation that must be reported.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = BACKEND / "scripts"
SAMPLE = BACKEND / "tests" / "fixtures" / "fhir" / "sample_share_bundle.json"


def _load(name: str) -> ModuleType:
    """Import a script the way ``test_api_docs_export.py`` does."""

    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def export_sample() -> ModuleType:
    return _load("export_fhir_sample")


@pytest.fixture(scope="module")
def offline_check() -> ModuleType:
    return _load("fhir_offline_check")


@pytest.fixture
def bundle() -> dict[str, Any]:
    """The committed sample as JSON, deep-copied so a test may mutate it."""

    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    document = copy.deepcopy(raw)
    return cast("dict[str, Any]", document)


def _entry_of(bundle: dict[str, Any], kind: str) -> dict[str, Any]:
    for entry in bundle["entry"]:
        if entry["resource"]["resourceType"] == kind:
            return cast("dict[str, Any]", entry)
    raise AssertionError(f"the sample has no {kind}")


def _failures(checker: ModuleType, bundle: Any) -> list[str]:
    """`Any` on purpose: one test hands the checker a list to prove it refuses it."""

    return cast("list[str]", checker.check_bundle(bundle))


# -- the artifact is the code's output ------------------------------------


def test_the_committed_sample_is_byte_identical_to_what_the_builder_produces(
    export_sample: ModuleType,
) -> None:
    assert SAMPLE.is_file(), "run backend/scripts/export_fhir_sample.py"
    assert SAMPLE.read_text(encoding="utf-8") == export_sample.render_sample()


def test_the_export_script_reports_the_sample_as_current(export_sample: ModuleType) -> None:
    """``--check`` is what CI and `make fhir-validate` call, so it gets tested."""

    argv = [sys.argv[0], "--check"]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sys, "argv", argv)
        assert export_sample.main() == 0


def test_the_sample_ids_are_derived_not_random(
    export_sample: ModuleType, bundle: dict[str, Any]
) -> None:
    """A committed artifact whose ids moved on every run could not be reviewed."""

    assert bundle["id"] == str(export_sample.SAMPLE_BUNDLE_ID)
    assert bundle["identifier"]["value"] == str(export_sample.SAMPLE_BUNDLE_ID)
    assert bundle["timestamp"] == "2026-10-19T09:30:00Z"


# -- the offline checker passes the real document -------------------------


def test_the_sample_passes_the_offline_structural_check(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    assert _failures(offline_check, bundle) == []


def test_the_checker_cli_passes_on_the_sample() -> None:
    """The documented command, run for real, exits 0."""

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "fhir_offline_check.py")],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "offline structural check passed" in result.stdout


def test_the_checker_cli_fails_on_a_damaged_bundle(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text('{"resourceType": "Bundle", "type": "searchset"}', encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "fhir_offline_check.py"), str(broken)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "bundle.type" in result.stdout


# -- and catches what it claims to ---------------------------------------


def test_composition_must_come_first(offline_check: ModuleType, bundle: dict[str, Any]) -> None:
    bundle["entry"][0], bundle["entry"][1] = bundle["entry"][1], bundle["entry"][0]

    failures = _failures(offline_check, bundle)
    assert any("first entry" in failure for failure in failures)


def test_provenance_must_come_last(offline_check: ModuleType, bundle: dict[str, Any]) -> None:
    provenance = bundle["entry"].pop()
    bundle["entry"].insert(0, provenance)

    assert any("Provenance comes last" in f for f in _failures(offline_check, bundle))


def test_a_document_bundle_carries_no_transaction_fields(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    bundle["total"] = len(bundle["entry"])
    bundle["entry"][1]["request"] = {"method": "PUT", "url": "Patient/1"}

    failures = _failures(offline_check, bundle)
    assert any("bundle.total" in failure for failure in failures)
    assert any("entry[1].request" in failure for failure in failures)


def test_the_identifier_needs_a_system_and_a_value(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    del bundle["identifier"]["system"]

    assert any("bdl-3" in failure for failure in _failures(offline_check, bundle))


def test_the_timestamp_needs_an_explicit_zone(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    bundle["timestamp"] = "2026-10-19T09:30:00"

    assert any("bdl-4" in failure for failure in _failures(offline_check, bundle))


def test_a_dangling_reference_is_reported(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    composition = _entry_of(bundle, "Composition")["resource"]
    composition["section"][0]["entry"][0]["reference"] = "urn:uuid:" + "0" * 32

    failures = _failures(offline_check, bundle)
    assert any("not in the bundle" in failure for failure in failures)


def test_duplicate_fullurls_are_reported(offline_check: ModuleType, bundle: dict[str, Any]) -> None:
    bundle["entry"][2]["fullUrl"] = bundle["entry"][1]["fullUrl"]

    assert any("duplicate fullUrl" in failure for failure in _failures(offline_check, bundle))


def test_a_resource_id_must_match_its_fullurl(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    entry = bundle["entry"][1]
    entry["resource"]["id"] = "11111111-1111-1111-1111-111111111111"

    assert any(
        "does not match its fullUrl" in failure for failure in _failures(offline_check, bundle)
    )


def test_an_unverified_loinc_code_is_refused(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    """The panel code, not the total: the exact mistake the terminology file warns about."""

    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] != "Observation":
            continue
        coding = resource["code"]["coding"][0]
        if coding.get("system") == "http://loinc.org":
            coding["code"] = "44249-1"
            break
    else:  # pragma: no cover - the sample always has a LOINC total
        raise AssertionError("no LOINC-coded Observation in the sample")

    failures = _failures(offline_check, bundle)
    assert any(
        "44249-1" in failure and "not one of the verified terms" in failure for failure in failures
    )


def test_a_borrowed_code_system_is_refused(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    """SNOMED nobody verified is worse than an honest local code."""

    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] != "Observation":
            continue
        coding = resource["code"]["coding"][0]
        if coding.get("system") == "http://loinc.org":
            coding["system"] = "http://snomed.info/sct"
            break
    else:  # pragma: no cover - the sample always has a LOINC total
        raise AssertionError("no LOINC-coded Observation in the sample")

    assert any("snomed" in failure for failure in _failures(offline_check, bundle))


def test_a_verified_code_must_carry_its_verified_display(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource["resourceType"] != "Observation":
            continue
        coding = resource["code"]["coding"][0]
        if coding.get("system") == "http://loinc.org":
            coding["display"] = "Depression score"
            break
    else:  # pragma: no cover
        raise AssertionError("no LOINC-coded Observation in the sample")

    assert any("loinc.org says" in failure for failure in _failures(offline_check, bundle))


def test_an_observation_without_the_disclaimer_note_is_refused(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    observation = next(
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "Observation"
    )
    observation["note"] = [{"text": "Score"}]

    assert any(
        "patient-generated sentence" in failure for failure in _failures(offline_check, bundle)
    )


def test_a_questionnaire_response_note_is_refused_offline(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    """The property that failed the official validator fails here too, offline.

    ``validator_cli.jar`` 7.0.2 on FHIR 4.0.1 rejected ``note`` on a
    ``QuestionnaireResponse`` ("Unrecognized property") and exited 1. This suite
    has no JVM, so ``UNDEFINED_PROPERTIES`` is what makes the same mistake a
    local failure rather than a CI surprise.
    """

    response = _entry_of(bundle, "QuestionnaireResponse")["resource"]
    response["note"] = [{"text": "Total score, self-administered."}]

    failures = _failures(offline_check, bundle)
    assert any("not an element of QuestionnaireResponse" in failure for failure in failures)


def test_a_questionnaire_response_without_narrative_is_refused_offline(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    """Removing the note is not enough; the sentence has to land somewhere."""

    response = _entry_of(bundle, "QuestionnaireResponse")["resource"]
    del response["text"]

    assert any("narrative is required" in failure for failure in _failures(offline_check, bundle))


def test_an_unescaped_narrative_is_refused_offline(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    """The validator parses narrative as XHTML, so raw markup must not survive."""

    response = _entry_of(bundle, "QuestionnaireResponse")["resource"]
    response["text"]["div"] = (
        '<div xmlns="http://www.w3.org/1999/xhtml"><p>Mild <b>band</b></p></div>'
    )

    assert any("not escaped" in failure for failure in _failures(offline_check, bundle))


def test_a_document_reference_without_the_disclaimer_is_refused(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    document = _entry_of(bundle, "DocumentReference")["resource"]
    document["description"] = "Thought record"

    assert any(
        "description must carry the patient-generated sentence" in failure
        for failure in _failures(offline_check, bundle)
    )


def test_the_composition_title_must_say_it_is_not_a_diagnosis(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    composition = _entry_of(bundle, "Composition")["resource"]
    composition["title"] = "Appointment summary"

    assert any(
        "Patient-generated, not a diagnosis" in failure
        for failure in _failures(offline_check, bundle)
    )


def test_an_empty_section_is_refused(offline_check: ModuleType, bundle: dict[str, Any]) -> None:
    composition = _entry_of(bundle, "Composition")["resource"]
    composition["section"][0]["entry"] = []

    assert any("has no entries" in failure for failure in _failures(offline_check, bundle))


def test_provenance_must_name_the_patient_as_author(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    """Swapping the agents is the failure that would quietly undo the feature."""

    provenance = _entry_of(bundle, "Provenance")["resource"]
    device = _entry_of(bundle, "Device")["resource"]
    provenance["agent"][0]["who"]["reference"] = f"urn:uuid:{device['id']}"

    failures = _failures(offline_check, bundle)
    assert any("author agent must be the Patient" in failure for failure in failures)


def test_provenance_must_target_the_composition(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    provenance = _entry_of(bundle, "Provenance")["resource"]
    provenance["target"] = provenance["target"][1:]

    assert any(
        "target must include the Composition" in failure
        for failure in _failures(offline_check, bundle)
    )


def test_a_resource_type_the_builder_does_not_emit_is_refused(
    offline_check: ModuleType, bundle: dict[str, Any]
) -> None:
    patient = _entry_of(bundle, "Patient")
    patient["resource"] = {
        "resourceType": "MedicationRequest",
        "id": patient["resource"]["id"],
        "status": "active",
        "intent": "order",
    }

    failures = _failures(offline_check, bundle)
    assert any("not a resource type" in failure for failure in failures)


def test_a_non_object_bundle_is_refused(offline_check: ModuleType) -> None:
    assert _failures(offline_check, ["not", "a", "bundle"]) == ["bundle: not a JSON object"]
