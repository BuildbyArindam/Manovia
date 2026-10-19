"""Offline structural check of a Manovia FHIR R4 share bundle (Day 47).

Usage (from ``backend/``)::

    python scripts/fhir_offline_check.py                 # check the committed sample
    python scripts/fhir_offline_check.py path/to/x.json  # check any bundle file

**What this is.** The fallback half of ``make fhir-validate``: a fast,
dependency-free check that a bundle Manovia produced still has the shape the R4
document invariants demand, that nothing in it points at a resource that is not
there, and that no code claims a terminology Manovia did not verify. It runs in
the offline test suite (``tests/unit/test_share_fhir_sample.py``), so a change to
the builder that breaks the document shape fails locally rather than in CI.

**What this is not.** A FHIR validator. It does not know R4's cardinalities,
value sets, slicing or type rules for anything except the seven resource types
``app/services/sharing/fhir.py`` emits, and it will happily pass a bundle the
specification rejects for a reason it does not model. The authority is the
official HL7 validator — ``hapifhir/org.hl7.fhir.core``'s ``validator_cli.jar``
against FHIR 4.0.1 — which the ``fhir-validation`` job in
``.github/workflows/ci.yml`` runs on this same committed file. This script needs
no JVM and no 204 MB download, which is the only reason it exists.

Rule sources, cited where each is used:

* R4 document-bundle invariants, https://hl7.org/fhir/R4/bundle.html —
  Composition first, a persistent identifier, a timestamp, unique ``urn:uuid``
  fullUrls, and no ``total``/``entry.request``/``entry.response``/``entry.search``
  outside a batch, transaction, history or searchset.
* R4 Provenance, https://hl7.org/fhir/R4/provenance.html — ``agent.who`` may be a
  Patient or a Device, and ``agent.type`` binds to
  ``provenance-participant-type``, whose ``author`` and ``assembler`` codes are
  what make "patient-generated, assembled by software" machine-readable.
* This project's terminology policy (``docs/adr/0047-clinician-share-bridge.md``):
  a code is either verified — the LOINC totals pinned in
  ``app/content/sharing/loinc.json`` with their loinc.org source URLs — or it is
  ours, in ``https://manovia.app/fhir/CodeSystem/...``. Anything else is a claim
  nobody checked.
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.sharing.fhir import (
    BAND_SYSTEM,
    DOCUMENT_KIND_SYSTEM,
    DOCUMENT_TYPE_SYSTEM,
    LOINC_SYSTEM,
    MANOVIA_SYSTEM,
    MOOD_SYSTEM,
    OBSERVATION_CATEGORY_SYSTEM,
    PATIENT_GENERATED_NOTE,
    PROVENANCE_AGENT_SYSTEM,
    UCUM_SYSTEM,
)
from app.services.sharing.items import DISCLAIMER_TITLE
from app.services.sharing.terminology import get_terminology

BACKEND_DIR = Path(__file__).resolve().parents[1]
#: The artifact CI validates; the default target so `make fhir-validate` and the
#: offline suite check the same bytes.
DEFAULT_BUNDLE = BACKEND_DIR / "tests" / "fixtures" / "fhir" / "sample_share_bundle.json"

#: Every code system a Manovia share bundle may use. Borrowing a term from
#: anywhere else — SNOMED, a LOINC code nobody read, a local system invented in a
#: hurry — is the failure this set exists to catch.
ALLOWED_SYSTEMS: frozenset[str] = frozenset(
    {
        LOINC_SYSTEM,
        UCUM_SYSTEM,
        OBSERVATION_CATEGORY_SYSTEM,
        PROVENANCE_AGENT_SYSTEM,
        MANOVIA_SYSTEM,
        DOCUMENT_TYPE_SYSTEM,
        DOCUMENT_KIND_SYSTEM,
        MOOD_SYSTEM,
        BAND_SYSTEM,
    }
)

#: Elements that belong to a batch/transaction/history/searchset bundle and not
#: to a document. https://hl7.org/fhir/R4/bundle.html
BUNDLE_FORBIDDEN: tuple[str, ...] = ("total", "link")
ENTRY_FORBIDDEN: tuple[str, ...] = ("request", "response", "search", "link")

#: Required elements per resource type, from the R4 definition pages, for the
#: types this builder emits. ``title`` on Composition and ``description`` on
#: DocumentReference are optional in R4 but mandatory here: they are where the
#: "patient-generated, not a diagnosis" sentence lives, and a document that
#: drops it is not the document this feature promises.
REQUIRED_ELEMENTS: dict[str, tuple[str, ...]] = {
    "Composition": ("status", "type", "subject", "date", "author", "title"),
    "Device": ("status",),
    "DocumentReference": ("status", "content", "subject", "description"),
    "Observation": ("status", "code", "subject", "category", "effectiveDateTime"),
    "Patient": ("active",),
    "Provenance": ("target", "recorded", "agent"),
    "QuestionnaireResponse": ("status", "subject", "authored"),
}


def _walk(value: Any) -> Iterator[tuple[str, Any]]:
    """Every ``(key, value)`` pair in a nested JSON structure, depth-first."""

    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _codings(value: Any) -> Iterator[dict[str, Any]]:
    """Every coding in a structure, wherever the CodeableConcepts sit."""

    for key, item in _walk(value):
        if key == "coding" and isinstance(item, list):
            yield from (coding for coding in item if isinstance(coding, dict))


def _references(value: Any) -> Iterator[str]:
    """Every ``reference`` string in a structure."""

    for key, item in _walk(value):
        if key == "reference" and isinstance(item, str):
            yield item


def _is_instant(value: Any) -> bool:
    """A FHIR ``instant``: RFC 3339, with an explicit zone."""

    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_uuid(value: Any) -> bool:
    """A resource id in the ``uuid5`` form this builder emits."""

    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _coding_text(coding: dict[str, Any]) -> str:
    """``system|code`` for a failure message."""

    return f"{coding.get('system')}|{coding.get('code')}"


def check_bundle(bundle: Any) -> list[str]:
    """Every rule this checker knows, as human-readable failure strings.

    An empty list means the bundle satisfied all of them — which is a statement
    about *these* rules, not about FHIR conformance (see the module docstring).
    """

    if not isinstance(bundle, dict):
        return ["bundle: not a JSON object"]

    failures: list[str] = []

    # -- the bundle itself -------------------------------------------------
    if bundle.get("resourceType") != "Bundle":
        failures.append(
            f"bundle.resourceType: expected 'Bundle', got {bundle.get('resourceType')!r}"
        )
    if bundle.get("type") != "document":
        failures.append(f"bundle.type: a share bundle is a document, got {bundle.get('type')!r}")
    identifier = bundle.get("identifier")
    if (
        not isinstance(identifier, dict)
        or not identifier.get("system")
        or not identifier.get("value")
    ):
        failures.append(
            "bundle.identifier: a document bundle needs a persistent identifier "
            "with both a system and a value (bdl-3, hl7.org/fhir/R4/bundle.html)"
        )
    if not _is_instant(bundle.get("timestamp")):
        failures.append(
            "bundle.timestamp: a document bundle needs a timestamp with an explicit "
            "zone (bdl-4, hl7.org/fhir/R4/bundle.html)"
        )
    for key in BUNDLE_FORBIDDEN:
        if key in bundle:
            failures.append(f"bundle.{key}: belongs to a search/history bundle, not a document")

    entries = bundle.get("entry")
    if not isinstance(entries, list) or not entries:
        failures.append("bundle.entry: a document bundle needs at least one entry")
        return failures

    # -- entries: fullUrls, ids, order -------------------------------------
    resources: list[dict[str, Any]] = []
    full_urls: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            failures.append(f"entry[{index}]: not an object")
            continue
        for key in ENTRY_FORBIDDEN:
            if key in entry:
                failures.append(
                    f"entry[{index}].{key}: belongs to a batch/transaction/history/"
                    f"searchset bundle, not a document"
                )
        full_url = entry.get("fullUrl")
        resource = entry.get("resource")
        if not isinstance(full_url, str) or not full_url.startswith("urn:uuid:"):
            failures.append(f"entry[{index}].fullUrl: expected urn:uuid:…, got {full_url!r}")
        elif full_url in full_urls:
            failures.append(f"entry[{index}].fullUrl: duplicate fullUrl {full_url}")
        else:
            full_urls.append(full_url)
        if not isinstance(resource, dict) or not isinstance(resource.get("resourceType"), str):
            failures.append(f"entry[{index}].resource: missing resourceType")
            continue
        kind = str(resource["resourceType"])
        resource_id = resource.get("id")
        if not _is_uuid(resource_id):
            failures.append(f"entry[{index}] {kind}.id: expected a uuid, got {resource_id!r}")
        elif full_url != f"urn:uuid:{resource_id}":
            failures.append(f"entry[{index}] {kind}.id: does not match its fullUrl {full_url}")
        resources.append(resource)

    kinds = [str(resource["resourceType"]) for resource in resources]
    if not kinds or kinds[0] != "Composition":
        failures.append(
            "entry order: the first entry of a document bundle must be the Composition "
            f"(found {kinds[0] if kinds else 'nothing'})"
        )
    if kinds.count("Composition") != 1:
        failures.append(f"entry order: exactly one Composition, found {kinds.count('Composition')}")
    if kinds[-1] != "Provenance":
        failures.append(f"entry order: Provenance comes last, found {kinds[-1]!r}")

    # -- references resolve ------------------------------------------------
    known = set(full_urls)
    for index, resource in enumerate(resources):
        for reference in _references(resource):
            if reference.startswith("urn:uuid:") and reference not in known:
                failures.append(
                    f"{resource.get('resourceType')}[{index}]: reference {reference} "
                    "points at an entry that is not in the bundle"
                )
            elif not reference.startswith("urn:uuid:"):
                failures.append(
                    f"{resource.get('resourceType')}[{index}]: reference {reference!r} is not "
                    "a bundle-local urn:uuid, so a reader cannot resolve it offline"
                )

    # -- per-resource shape ------------------------------------------------
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for resource in resources:
        by_kind.setdefault(str(resource["resourceType"]), []).append(resource)

    for resource in resources:
        kind = str(resource["resourceType"])
        for element in REQUIRED_ELEMENTS.get(kind, ()):
            if element not in resource:
                failures.append(f"{kind} {resource.get('id')}: missing required {element}")
        if kind not in REQUIRED_ELEMENTS:
            failures.append(f"{kind}: not a resource type Manovia's share builder emits")

    # -- terminology: verified or ours -------------------------------------
    verified = {code.code: code for code in get_terminology().codes}
    for resource in resources:
        kind = str(resource["resourceType"])
        for coding in _codings(resource):
            system = coding.get("system")
            code = coding.get("code")
            if not isinstance(system, str) or system not in ALLOWED_SYSTEMS:
                failures.append(
                    f"{kind} {resource.get('id')}: code system {system!r} is neither LOINC "
                    "(verified in app/content/sharing/loinc.json), UCUM, one of the two "
                    "terminology.hl7.org systems used here, nor Manovia's own namespace"
                )
                continue
            if system != LOINC_SYSTEM:
                continue
            term = verified.get(str(code))
            if term is None:
                failures.append(
                    f"{kind} {resource.get('id')}: LOINC code {code!r} is not one of the "
                    "verified terms in app/content/sharing/loinc.json "
                    f"({', '.join(sorted(verified))})"
                )
            elif coding.get("display") != term.display:
                failures.append(
                    f"{kind} {resource.get('id')}: display for LOINC {code} is "
                    f"{coding.get('display')!r}, loinc.org says {term.display!r}"
                )

    # -- Observations: survey category, UCUM unit, the disclaimer ----------
    for observation in by_kind.get("Observation", []):
        where = f"Observation {observation.get('id')}"
        categories = [
            _coding_text(coding)
            for coding in _codings(observation.get("category"))
            if coding.get("system") == OBSERVATION_CATEGORY_SYSTEM
            and coding.get("code") == "survey"
        ]
        if not categories:
            failures.append(f"{where}: category must include observation-category|survey")
        quantity = observation.get("valueQuantity")
        if isinstance(quantity, dict):
            if quantity.get("system") != UCUM_SYSTEM:
                failures.append(f"{where}: valueQuantity.system must be {UCUM_SYSTEM}")
            loinc_codes = [
                str(coding.get("code"))
                for coding in _codings(observation.get("code"))
                if coding.get("system") == LOINC_SYSTEM
            ]
            expected_unit = (
                verified[loinc_codes[0]].ucum_unit
                if loinc_codes and loinc_codes[0] in verified
                else None
            )
            if expected_unit is not None and quantity.get("unit") != expected_unit:
                failures.append(
                    f"{where}: LOINC {loinc_codes[0]}'s unit is {expected_unit}, "
                    f"got {quantity.get('unit')!r}"
                )
        notes = [note.get("text") for note in observation.get("note", []) if isinstance(note, dict)]
        if not any(isinstance(text, str) and PATIENT_GENERATED_NOTE in text for text in notes):
            failures.append(f"{where}: note must carry the patient-generated sentence")

    # -- DocumentReferences: the disclaimer in the description -------------
    for document in by_kind.get("DocumentReference", []):
        where = f"DocumentReference {document.get('id')}"
        description = document.get("description")
        if not isinstance(description, str) or PATIENT_GENERATED_NOTE not in description:
            failures.append(f"{where}: description must carry the patient-generated sentence")
        content = document.get("content")
        if not isinstance(content, list) or not content:
            failures.append(f"{where}: content must hold at least one attachment")
            continue
        attachment = content[0].get("attachment") if isinstance(content[0], dict) else None
        if not isinstance(attachment, dict) or not attachment.get("contentType"):
            failures.append(f"{where}: content[0].attachment needs a contentType")
        elif not attachment.get("data"):
            failures.append(f"{where}: content[0].attachment carries no data")

    # -- Composition: the document's face ---------------------------------
    for composition in by_kind.get("Composition", []):
        where = f"Composition {composition.get('id')}"
        title = composition.get("title")
        if not isinstance(title, str) or DISCLAIMER_TITLE not in title:
            failures.append(f"{where}: title must say {DISCLAIMER_TITLE!r}")
        sections = composition.get("section", [])
        if not isinstance(sections, list) or not sections:
            failures.append(f"{where}: a summary with no sections says nothing")
            continue
        for position, section in enumerate(sections):
            if not isinstance(section, dict):
                failures.append(f"{where}: section[{position}] is not an object")
                continue
            entries_in_section = section.get("entry")
            # sec-1: a section needs a text or entries; this builder only ever
            # emits sections that have entries (fhir.py skips empty ones).
            if not isinstance(entries_in_section, list) or not entries_in_section:
                failures.append(
                    f"{where}: section[{position}] ({section.get('title')!r}) has no entries "
                    "(R4 sec-1 allows a section only with entries or a text)"
                )
            if not section.get("title"):
                failures.append(f"{where}: section[{position}] has no title")

    # -- Provenance: patient-generated, assembled by software --------------
    for provenance in by_kind.get("Provenance", []):
        where = f"Provenance {provenance.get('id')}"
        if not _is_instant(provenance.get("recorded")):
            failures.append(f"{where}: recorded must be an instant")
        targets = [reference for reference in _references(provenance.get("target")) if reference]
        composition_urls = {
            f"urn:uuid:{composition.get('id')}" for composition in by_kind.get("Composition", [])
        }
        if not composition_urls & set(targets):
            failures.append(f"{where}: target must include the Composition")
        agents = provenance.get("agent")
        if not isinstance(agents, list) or len(agents) != 2:
            failures.append(f"{where}: expected exactly two agents (author, assembler)")
            continue
        roles: dict[str, str] = {}
        for position, agent in enumerate(agents):
            if not isinstance(agent, dict):
                failures.append(f"{where}: agent[{position}] is not an object")
                continue
            codes = [
                str(coding.get("code"))
                for coding in _codings(agent.get("type"))
                if coding.get("system") == PROVENANCE_AGENT_SYSTEM
            ]
            if len(codes) != 1:
                failures.append(
                    f"{where}: agent[{position}].type must carry one "
                    f"provenance-participant-type code, found {codes}"
                )
                continue
            who = next(_references(agent.get("who")), None)
            if who is None:
                failures.append(f"{where}: agent[{position}] has no who")
                continue
            roles[codes[0]] = who
        # The claim the whole export exists to make, in machine-readable form:
        # a Patient authored it, a Device assembled it.
        # https://hl7.org/fhir/R4/provenance.html
        patient_urls = {f"urn:uuid:{item.get('id')}" for item in by_kind.get("Patient", [])}
        device_urls = {f"urn:uuid:{item.get('id')}" for item in by_kind.get("Device", [])}
        if roles.get("author") not in patient_urls:
            failures.append(
                f"{where}: the author agent must be the Patient, got {roles.get('author')!r}"
            )
        if roles.get("assembler") not in device_urls:
            failures.append(
                f"{where}: the assembler agent must be the Device, got {roles.get('assembler')!r}"
            )

    return failures


def summarise(bundle: dict[str, Any]) -> str:
    """One line describing what the bundle holds, for a passing run."""

    entries = bundle.get("entry", [])
    kinds = Counter(
        str(entry["resource"]["resourceType"])
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)
    )
    codes = sorted(
        {
            str(coding.get("code"))
            for coding in _codings(bundle)
            if coding.get("system") == LOINC_SYSTEM
        }
    )
    verified = {term.code: term for term in get_terminology().codes}
    loinc = ", ".join(
        f'{code} "{verified[code].display}"' if code in verified else code for code in codes
    )
    listing = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
    return f"{len(entries)} entries ({listing}); LOINC totals: {loinc or 'none'}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "bundles",
        nargs="*",
        type=Path,
        help=f"bundle files to check (default: {DEFAULT_BUNDLE.name})",
    )
    args = parser.parse_args()
    paths: list[Path] = list(args.bundles) or [DEFAULT_BUNDLE]

    exit_code = 0
    for path in paths:
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{path}: cannot read — {exc}")
            exit_code = 1
            continue
        failures = check_bundle(bundle)
        if failures:
            exit_code = 1
            print(f"{path}: {len(failures)} failure(s)")
            for failure in failures:
                print(f"  - {failure}")
        elif isinstance(bundle, dict):
            print(f"{path}: ok — {summarise(bundle)}")
        else:
            print(f"{path}: ok")

    if exit_code == 0:
        print(
            "offline structural check passed. This is not FHIR conformance: the "
            "official HL7 validator (hapifhir validator_cli.jar, FHIR 4.0.1) runs "
            "on the same file in the CI `fhir-validation` job."
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
