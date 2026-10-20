"""The HL7 FHIR R4 document: what a professional's system can actually read.

The bundle is a ``document`` bundle — Composition first, every entry with a
``urn:uuid`` fullUrl, plus the identifier and timestamp the document invariants
(``bdl-3``, ``bdl-4``, ``bdl-8``) require — because that is the shape an EHR
imports. It is validated in CI with the official HL7 validator
(``hapifhir/org.hl7.fhir.core`` release ``validator_cli.jar``, FHIR version
4.0.1); see the ``fhir-validation`` job in ``.github/workflows/ci.yml`` and
``make fhir-validate`` for the offline fallback.

Terminology policy, in one rule: **a code is either verified or it is ours.**

* The two assessment totals use LOINC codes read off loinc.org on 2026-10-19 and
  pinned, with their source URLs, in ``app/content/sharing/loinc.json``:
  PHQ-9 total score ``44261-6`` (https://loinc.org/44261-6) and GAD-7 total score
  ``70274-6`` (https://loinc.org/70274-6). The unit ``{score}`` is LOINC's own
  example UCUM unit for both terms.
* Everything Manovia-specific — the mood summary, the self-help band labels, the
  document type — is coded in ``https://manovia.app/fhir/CodeSystem/...`` with a
  human ``text``. Borrowing a LOINC or SNOMED term nobody verified would put a
  wrong clinical claim into somebody's record, which is worse than an honest
  local code.
* ``Observation.category`` uses ``survey`` from
  ``http://terminology.hl7.org/CodeSystem/observation-category`` and the
  provenance agents use ``author`` and ``assembler`` from
  ``http://terminology.hl7.org/CodeSystem/provenance-participant-type``
  (https://hl7.org/fhir/R4/provenance-definitions.html,
  https://terminology.hl7.org/CodeSystem-provenance-participant-type.json.html).

Provenance is the point of the exercise: ``Provenance.agent[0]`` is the **Patient**
as ``author`` and ``agent[1]`` is the Manovia app as ``assembler``, so a reader can
see patient-generated data assembled by software rather than a clinician's
observation. The Composition title, every Observation note, every
DocumentReference description and both QuestionnaireResponse narratives repeat
the same sentence: patient-generated, not a diagnosis. (Narrative rather than a
note on the last two: R4 defines no ``note`` element on ``QuestionnaireResponse``,
and the official validator rejects the property outright.)
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from xml.sax.saxutils import escape

from app.models.base import as_utc, utcnow
from app.services.sharing.items import (
    DISCLAIMER,
    DISCLAIMER_TITLE,
    ExerciseHelpfulness,
    MoodTrendSummary,
    SharePayload,
    ThoughtRecordShare,
)

#: FHIR release this bundle is written against.
FHIR_VERSION = "4.0.1"

#: Manovia's own terminology namespace. The domain is the one this project's
#: deployment docs already use (``docs/deploy.md``); nothing here claims a code
#: from a terminology nobody checked.
MANOVIA_SYSTEM = "https://manovia.app/fhir"
DOCUMENT_TYPE_SYSTEM = f"{MANOVIA_SYSTEM}/CodeSystem/share-document-type"
MOOD_SYSTEM = f"{MANOVIA_SYSTEM}/CodeSystem/mood-summary"
BAND_SYSTEM = f"{MANOVIA_SYSTEM}/CodeSystem/assessment-band"
DOCUMENT_KIND_SYSTEM = f"{MANOVIA_SYSTEM}/CodeSystem/share-document-kind"

#: Terminology borrowed from HL7, with the pages it was read from (see module
#: docstring). ``survey`` is one of the observation categories; ``author`` and
#: ``assembler`` are provenance participant types.
OBSERVATION_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
PROVENANCE_AGENT_SYSTEM = "http://terminology.hl7.org/CodeSystem/provenance-participant-type"
LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"

#: The sentence this document exists to say.
PATIENT_GENERATED_NOTE = (
    "Patient-generated data from Manovia, a self-help companion. "
    "Not a diagnosis and not a clinical observation."
)

#: The five thought-record sections, in the order the UI walks them.
RECORD_SECTIONS: tuple[tuple[str, str], ...] = (
    ("situation", "Situation"),
    ("automatic_thought", "Automatic thought"),
    ("evidence_for", "Evidence for"),
    ("evidence_against", "Evidence against"),
    ("balanced_thought", "Balanced thought"),
)


def _instant(value: datetime) -> str:
    """FHIR ``instant``/``dateTime`` with second precision and an explicit zone."""

    return as_utc(value).astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _date(value: datetime) -> str:
    """A date for a human-facing line inside a document."""

    return as_utc(value).astimezone(UTC).date().isoformat()


def _urn(value: uuid.UUID) -> str:
    """The bundle-local reference form for a resource id."""

    return f"urn:uuid:{value}"


def _reference(value: uuid.UUID) -> dict[str, str]:
    return {"reference": _urn(value)}


def _text_data(data: str) -> str:
    """Inline ``text/plain`` attachment data, base64-encoded as FHIR requires."""

    return base64.b64encode(data.encode("utf-8")).decode("ascii")


def _narrative(*paragraphs: str) -> dict[str, str]:
    """R4 human-readable narrative, for a resource that has no ``note`` element.

    ``DomainResource.text`` is where R4 puts prose about a resource, and it must
    be a single valid XHTML ``div``: the official validator parses it and errors
    on anything malformed, which is why the text is escaped rather than
    interpolated. ``QuestionnaireResponse`` is what this exists for — R4 gives it
    no ``note``, so the patient-generated sentence would otherwise have nowhere
    to travel with the answers it qualifies.
    """

    body = "".join(f"<p>{escape(text)}</p>" for text in paragraphs if text)
    return {
        "status": "generated",
        "div": f'<div xmlns="http://www.w3.org/1999/xhtml">{body}</div>',
    }


def _coding(system: str, code: str, display: str) -> dict[str, str]:
    return {"system": system, "code": code, "display": display}


def _quantity(value: float | int, unit: str) -> dict[str, Any]:
    """A UCUM quantity. ``{score}`` is LOINC's example unit for both totals."""

    return {"value": value, "unit": unit, "system": UCUM_SYSTEM, "code": unit}


def _short(value: str, limit: int) -> str:
    """Trim a label so a title stays a title."""

    return value if len(value) <= limit else value[: limit - 1] + "…"


class _BundleBuilder:
    """Collects resources, assigns ids, and emits the document bundle.

    A builder rather than a pile of functions because three things have to agree
    across every resource: the ``urn:uuid`` each is referenced by, the
    Composition's section entries, and the Provenance's target list. Keeping the
    ids in one place is what makes a dangling reference impossible.
    """

    def __init__(self, payload: SharePayload, *, bundle_id: uuid.UUID, now: datetime) -> None:
        self.payload = payload
        self.bundle_id = bundle_id
        self.now = now
        #: Deterministic from the bundle id so re-building one share is stable.
        self.patient_id = uuid.uuid5(bundle_id, "patient")
        self.device_id = uuid.uuid5(bundle_id, "device")
        self.composition_id = uuid.uuid5(bundle_id, "composition")
        self.provenance_id = uuid.uuid5(bundle_id, "provenance")
        self._entries: list[tuple[uuid.UUID, dict[str, Any]]] = []
        self._sections: list[dict[str, Any]] = []
        self._counter = 0

    def next_id(self, kind: str) -> uuid.UUID:
        """A deterministic id for one content resource.

        Derived from the bundle id and the resource's position, not from
        ``uuid4()``: the same share rebuilt gives byte-identical JSON, which is
        what makes a committed sample bundle reviewable and a document
        reproducible. Two resources can then never collide, because the counter
        is part of the name.
        """

        self._counter += 1
        return uuid.uuid5(self.bundle_id, f"{kind}:{self._counter}")

    # -- plumbing ----------------------------------------------------------
    @staticmethod
    def with_id(resource: dict[str, Any], resource_id: uuid.UUID) -> dict[str, Any]:
        """The same resource with ``resourceType`` first and its ``id`` second.

        Every entry in a document bundle carries the id its ``fullUrl`` names:
        a resource without one cannot be pointed at from a section, a
        ``derivedFrom`` or the Provenance target list, and the official
        validator says so.
        """

        kind = resource["resourceType"]
        rest = {key: value for key, value in resource.items() if key not in ("resourceType", "id")}
        return {"resourceType": kind, "id": str(resource_id), **rest}

    def add(self, resource_id: uuid.UUID, resource: dict[str, Any]) -> uuid.UUID:
        """Register one resource; returns its id so callers can reference it."""

        self._entries.append((resource_id, self.with_id(resource, resource_id)))
        return resource_id

    def section(self, title: str, entries: list[uuid.UUID], *, code: str) -> None:
        """Add a Composition section. Empty sections are skipped, never emitted.

        R4's ``sec-1`` (a section must have a text or entries) is why: a section
        with neither is invalid, and a heading with nothing under it is noise.
        Each section's author is the patient — that is who wrote it.
        """

        if not entries:
            return
        self._sections.append(
            {
                "title": title,
                "code": {"coding": [_coding(DOCUMENT_KIND_SYSTEM, code, title)], "text": title},
                "author": [_reference(self.patient_id)],
                "entry": [_reference(entry) for entry in entries],
            }
        )

    def document_reference(self, *, title: str, body: str, kind: str) -> uuid.UUID:
        """One inline ``text/plain`` DocumentReference: Manovia's narrative items."""

        return self.add(
            self.next_id("document-reference"),
            {
                "resourceType": "DocumentReference",
                "status": "current",
                "docStatus": "final",
                "category": [
                    {"coding": [_coding(DOCUMENT_KIND_SYSTEM, kind, title)], "text": title}
                ],
                "subject": _reference(self.patient_id),
                "date": _instant(self.now),
                "author": [_reference(self.patient_id)],
                "description": f"{title} — {PATIENT_GENERATED_NOTE}",
                "content": [
                    {
                        "attachment": {
                            "contentType": "text/plain",
                            "language": "en",
                            "data": _text_data(body),
                            "title": title,
                            "creation": _instant(self.now),
                        }
                    }
                ],
            },
        )

    def observation(
        self,
        *,
        code: dict[str, Any],
        value: dict[str, Any],
        effective: datetime,
        note: str = PATIENT_GENERATED_NOTE,
        derived_from: list[uuid.UUID] | None = None,
    ) -> uuid.UUID:
        """One final, patient-reported ``survey`` Observation."""

        resource: dict[str, Any] = {
            "resourceType": "Observation",
            "status": "final",
            "category": [
                {
                    "coding": [_coding(OBSERVATION_CATEGORY_SYSTEM, "survey", "Survey")],
                    "text": "Patient-reported survey data",
                }
            ],
            "code": code,
            "subject": _reference(self.patient_id),
            "effectiveDateTime": _instant(effective),
            "note": [{"text": note}],
            **value,
        }
        if derived_from:
            resource["derivedFrom"] = [_reference(item) for item in derived_from]
        return self.add(self.next_id("observation"), resource)

    # -- bundle ------------------------------------------------------------
    def build(self) -> dict[str, Any]:
        """The document bundle: Composition first, then content, then Provenance."""

        entries: list[dict[str, Any]] = [
            {
                "fullUrl": _urn(self.composition_id),
                "resource": self.with_id(self._composition(), self.composition_id),
            },
            *(
                {"fullUrl": _urn(resource_id), "resource": resource}
                for resource_id, resource in self._entries
            ),
            {
                "fullUrl": _urn(self.provenance_id),
                "resource": self.with_id(self._provenance(), self.provenance_id),
            },
        ]
        return {
            "resourceType": "Bundle",
            "id": str(self.bundle_id),
            # ``bdl-3``: a document must have an identifier with a system and a value.
            "identifier": {
                "system": f"{MANOVIA_SYSTEM}/share-bundle",
                "value": str(self.bundle_id),
            },
            "type": "document",
            # ``bdl-4``: a document must have a date.
            "timestamp": _instant(self.now),
            "entry": entries,
        }

    def _composition(self) -> dict[str, Any]:
        """The document's face: title, author, and one section per shared item.

        No ``attester`` and no ``confidentiality``: both carry required bindings,
        and neither has an honest value here. A patient-generated summary is not
        legally attested by anybody, and labelling its confidentiality with a code
        nobody verified would be a claim, not a description.
        """

        resource: dict[str, Any] = {
            "resourceType": "Composition",
            "status": "final",
            "type": {
                "coding": [
                    _coding(
                        DOCUMENT_TYPE_SYSTEM,
                        "appointment-summary",
                        "Appointment summary (patient-generated)",
                    )
                ],
                "text": "Appointment summary prepared by the patient in Manovia",
            },
            "subject": _reference(self.patient_id),
            "date": _instant(self.payload.generated_at),
            "author": [_reference(self.patient_id)],
            "title": f"{DISCLAIMER_TITLE}: appointment summary prepared in Manovia",
        }
        # An empty ``section`` array is legal but says nothing; omitting it keeps
        # the Composition to the elements that are true.
        if self._sections:
            resource["section"] = list(self._sections)
        return resource

    def _provenance(self) -> dict[str, Any]:
        """Patient as author, Manovia as assembler — the whole point of the export."""

        return {
            "resourceType": "Provenance",
            "target": [
                _reference(self.composition_id),
                *(_reference(resource_id) for resource_id, _ in self._entries),
            ],
            "occurredDateTime": _instant(self.payload.generated_at),
            "recorded": _instant(self.now),
            "agent": [
                {
                    "type": {
                        "coding": [_coding(PROVENANCE_AGENT_SYSTEM, "author", "Author")],
                        "text": "The person the data is about entered it themselves",
                    },
                    "who": _reference(self.patient_id),
                },
                {
                    "type": {
                        "coding": [_coding(PROVENANCE_AGENT_SYSTEM, "assembler", "Assembler")],
                        "text": "Software assembled the document from stored entries",
                    },
                    "who": _reference(self.device_id),
                },
            ],
        }


def build_bundle(
    payload: SharePayload,
    *,
    bundle_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Turn one :class:`SharePayload` into an R4 document bundle."""

    moment = now or utcnow()
    identifier = bundle_id or uuid.uuid4()
    builder = _BundleBuilder(payload, bundle_id=identifier, now=moment)

    patient: dict[str, Any] = {"resourceType": "Patient", "active": True}
    if payload.identity and "@" in payload.identity:
        # Only when the person ticked "Identify me"; otherwise the document is
        # anonymous by construction, not by omission.
        patient["telecom"] = [{"system": "email", "value": payload.identity}]
    builder.add(builder.patient_id, patient)
    builder.add(
        builder.device_id,
        {
            "resourceType": "Device",
            "status": "active",
            "deviceName": [{"name": payload.app, "type": "user-friendly-name"}],
            "type": {
                "coding": [
                    _coding(DOCUMENT_TYPE_SYSTEM, "self-help-app", "Self-help companion app")
                ],
                "text": "Self-help companion application (not a medical device, not an EHR)",
            },
            "version": [{"value": payload.app_version}],
        },
    )

    if payload.mood_trend is not None:
        builder.section(
            "Mood summary", _mood_entries(builder, payload.mood_trend), code="mood-summary"
        )
    if payload.assessments:
        builder.section(
            "Screening history",
            _assessment_entries(builder, payload.assessments),
            code="assessments",
        )
    if payload.thought_records:
        builder.section(
            "Thought records",
            [
                builder.document_reference(
                    title=(
                        f"Thought record — {_short(record.emotion, 40)} "
                        f"({_date(record.created_at)})"
                    ),
                    body=thought_record_text(record),
                    kind="thought-record",
                )
                for record in payload.thought_records
            ],
            code="thought-records",
        )
    if payload.exercise_helpfulness is not None:
        builder.section(
            "Exercise helpfulness",
            [
                builder.document_reference(
                    title="Exercise helpfulness summary",
                    body=exercise_text(payload.exercise_helpfulness),
                    kind="exercise-helpfulness",
                )
            ],
            code="exercise-helpfulness",
        )
    if payload.note is not None:
        builder.section(
            "Note for the appointment",
            [
                builder.document_reference(
                    title="Note for the appointment", body=payload.note, kind="note"
                )
            ],
            code="note",
        )
    if payload.questions:
        builder.section(
            "Questions to ask",
            [
                builder.document_reference(
                    title="Questions to ask",
                    body="\n".join(f"- {question}" for question in payload.questions),
                    kind="questions",
                )
            ],
            code="questions",
        )
    return builder.build()


def _mood_entries(builder: _BundleBuilder, trend: MoodTrendSummary) -> list[uuid.UUID]:
    """The mood item as Observations: two means, a count and a direction."""

    generated = builder.payload.generated_at
    window_note = (
        f"{trend.entries} mood check-ins over {trend.window_days} days, self-rated "
        f"1 (low) to 5 (high). {PATIENT_GENERATED_NOTE}"
    )
    entries: list[uuid.UUID] = []
    for code, display, text, value in (
        (
            "mean-valence",
            "Mean mood rating",
            "Mean self-rated mood over the shared window (1 low to 5 high)",
            trend.mean_valence,
        ),
        (
            "mean-energy",
            "Mean energy rating",
            "Mean self-rated energy over the shared window (1 low to 5 high)",
            trend.mean_energy,
        ),
    ):
        if value is None:
            continue
        entries.append(
            builder.observation(
                code={"coding": [_coding(MOOD_SYSTEM, code, display)], "text": text},
                value={"valueQuantity": _quantity(value, "1")},
                effective=generated,
                note=window_note,
            )
        )
    entries.append(
        builder.observation(
            code={
                "coding": [_coding(MOOD_SYSTEM, "check-in-count", "Number of mood check-ins")],
                "text": "How many mood check-ins this summary is based on",
            },
            value={"valueQuantity": _quantity(trend.entries, "1")},
            effective=generated,
            note=window_note,
        )
    )
    entries.append(
        builder.observation(
            code={
                "coding": [_coding(MOOD_SYSTEM, "direction", "Direction of mood ratings")],
                "text": (
                    "Whether the second half of the window was higher, lower or much the same "
                    "as the first"
                ),
            },
            value={
                "valueCodeableConcept": {
                    "coding": [_coding(MOOD_SYSTEM, trend.direction, trend.direction)],
                    "text": trend.direction_note,
                }
            },
            effective=generated,
            note=window_note,
        )
    )
    return entries


def _assessment_entries(builder: _BundleBuilder, assessments: list[Any]) -> list[uuid.UUID]:
    """One QuestionnaireResponse and one LOINC-coded Observation per result."""

    entries: list[uuid.UUID] = []
    for assessment in assessments:
        response_id = builder.next_id("questionnaire-response")
        builder.add(
            response_id,
            {
                "resourceType": "QuestionnaireResponse",
                # R4 defines no `note` on QuestionnaireResponse, and the official
                # validator says so outright ("Unrecognized property 'note'") —
                # which is how this bundle first failed its own CI gate. The
                # sentence travels in the narrative instead: that is the element
                # R4 reserves for prose about a resource, it is what a FHIR
                # viewer renders, and it reaches anybody who opens this resource
                # without the Observation derived from it.
                "text": _narrative(
                    f"{assessment.instrument_name} total score, self-administered in "
                    f"Manovia. The band label ({assessment.band_label}) is a self-help "
                    "label for reflection, not a diagnosis. Individual item answers are "
                    f"not shared. {PATIENT_GENERATED_NOTE}"
                ),
                "status": "completed",
                "subject": _reference(builder.patient_id),
                "authored": _instant(assessment.completed_at),
                "author": _reference(builder.patient_id),
                "source": _reference(builder.patient_id),
                "item": [
                    {
                        # The LOINC total-score term is the linkId, as the US Core
                        # PHQ-9 example does, so a reader can trace the answer.
                        "linkId": assessment.loinc_code,
                        "text": assessment.loinc_display,
                        "answer": [{"valueInteger": assessment.total}],
                    }
                ],
            },
        )
        entries.append(response_id)
        entries.append(
            builder.observation(
                code={
                    "coding": [
                        _coding(LOINC_SYSTEM, assessment.loinc_code, assessment.loinc_display)
                    ],
                    "text": assessment.loinc_display,
                },
                value={
                    "valueQuantity": _quantity(assessment.total, assessment.ucum_unit),
                    "interpretation": [
                        {
                            "coding": [
                                _coding(BAND_SYSTEM, assessment.band, assessment.band_label)
                            ],
                            "text": (
                                f"{assessment.band_label} — self-help band label, not a diagnosis"
                            ),
                        }
                    ],
                },
                effective=assessment.completed_at,
                note=(
                    "Total score reported by the person in Manovia; LOINC term verified at "
                    f"{assessment.loinc_source_url}. {PATIENT_GENERATED_NOTE}"
                ),
                derived_from=[response_id],
            )
        )
    return entries


def thought_record_text(record: ThoughtRecordShare) -> str:
    """The record as plain text: five headed sections, the ratings, the disclaimer."""

    lines = [
        f"Date: {_date(record.created_at)}",
        f"Emotion: {record.emotion}",
        f"Intensity before: {record.intensity_before}/100",
        f"Intensity after: {record.intensity_after}/100",
    ]
    if record.what_helped:
        lines.append("What helped: " + ", ".join(record.what_helped))
    for field, label in RECORD_SECTIONS:
        lines += ["", f"{label}:", str(getattr(record, field))]
    lines += ["", DISCLAIMER]
    return "\n".join(lines)


def exercise_text(summary: ExerciseHelpfulness) -> str:
    """The exercise item as plain text (it is a Manovia-specific summary)."""

    lines = [
        f"Window: the last {summary.window_days} days (from {_date(summary.window_start)})",
        f"Completions: {summary.completions} ({summary.rated_completions} with both ratings)",
        f"Rating scale: {summary.rating_scale}",
        "",
    ]
    for row in summary.exercises:
        change = "n/a" if row.mean_change is None else f"{row.mean_change:+.2f}"
        before = "n/a" if row.mean_before is None else f"{row.mean_before:.2f}"
        after = "n/a" if row.mean_after is None else f"{row.mean_after:.2f}"
        lines.append(
            f"- {row.title}: {row.times} time(s), mean before {before}, "
            f"mean after {after}, mean change {change}"
        )
    if summary.most_helpful:
        lines += ["", f"Largest average improvement: {summary.most_helpful}"]
    lines += ["", summary.change_caveat, "", DISCLAIMER]
    return "\n".join(lines)


def bundle_bytes(
    payload: SharePayload, *, bundle_id: uuid.UUID | None = None, now: datetime | None = None
) -> bytes:
    """The bundle as UTF-8 JSON, two-space indented, ``resourceType`` first."""

    bundle = build_bundle(payload, bundle_id=bundle_id, now=now)
    return json.dumps(bundle, indent=2, ensure_ascii=False).encode("utf-8")


__all__ = [
    "BAND_SYSTEM",
    "DISCLAIMER",
    "DISCLAIMER_TITLE",
    "DOCUMENT_KIND_SYSTEM",
    "DOCUMENT_TYPE_SYSTEM",
    "FHIR_VERSION",
    "LOINC_SYSTEM",
    "MANOVIA_SYSTEM",
    "MOOD_SYSTEM",
    "OBSERVATION_CATEGORY_SYSTEM",
    "PATIENT_GENERATED_NOTE",
    "PROVENANCE_AGENT_SYSTEM",
    "RECORD_SECTIONS",
    "UCUM_SYSTEM",
    "build_bundle",
    "bundle_bytes",
    "exercise_text",
    "thought_record_text",
]
