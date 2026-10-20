"""Shared plumbing for the Day 47 share tests.

Both the integration test (``tests/integration/test_sharing_flow.py``) and the
security test (``tests/security/test_share_security.py``) need the same thing: a
signed-in person with a month of entries, a live share, and a way to do locally
what the browser does — derive the key from the URL fragment and decrypt the
documents. Duplicating that in two files would mean two places to get wrong, so
it lives here once.

Everything here is test-only: no application module imports it.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt_field
from app.models.cbt import cbt_aad
from app.models.enums import AssessmentBand, AssessmentInstrument, ShareItem
from app.models.user import User
from app.services.sharing import crypto
from app.services.sharing.items import SharePayload

API = "/api/v1/shares"
PUBLIC = "/api/v1/public/shares"

EMAIL = "someone@example.test"
PASSWORD = "Correct-Horse-99!"
NOTE = "I have been sleeping badly and I would like to talk about it."
QUESTIONS = ["Is this normal?", "What should I try next?"]
THOUGHT = {
    "situation": "An exam is tomorrow",
    "automatic_thought": "I will fail and everyone will think I am stupid",
    "evidence_for": "I missed one question in practice",
    "evidence_against": "I studied and passed the other practice tests",
    "balanced_thought": "I might not know every answer, but I prepared",
}


async def person(
    client: httpx.AsyncClient, *, email: str = EMAIL, password: str = PASSWORD
) -> tuple[dict[str, str], str]:
    """A signed-in account that has agreed to the terms and the privacy policy.

    Pass a different ``email`` for a second person: the tests that check
    ownership need two accounts, and an email is unique per account.
    """

    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    consent = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": "2026-10-01", "granted": True}
                for kind in ("terms", "privacy")
            ]
        },
    )
    assert consent.status_code == 201, consent.text
    return headers, body["user"]["id"]


async def seed_entries(session: AsyncSession, user: User) -> uuid.UUID:
    """A month of entries of every kind the share items can draw on.

    The thought record's text goes through the same envelope encryption the CBT
    endpoints use, because the share has to decrypt it — seeding plaintext would
    test nothing.
    """

    from app.models.assessment import AssessmentResult
    from app.models.cbt import CBTRecord
    from app.models.exercise_completion import ExerciseCompletion
    from app.models.mood import MoodEntry

    now = datetime.now(UTC)
    for offset, valence, energy in (
        (25, 2, 2),
        (20, 2, 3),
        (15, 3, 3),
        (9, 3, 4),
        (4, 4, 4),
        (1, 4, 5),
    ):
        session.add(
            MoodEntry(
                user_id=user.id,
                valence=valence,
                energy=energy,
                emotions=["calm"] if valence >= 3 else ["anxious"],
                factors={"sleep": 1, "work": 1} if offset % 2 else {"sleep": 1},
                created_at=now - timedelta(days=offset),
            )
        )
    session.add(
        AssessmentResult(
            user_id=user.id,
            instrument=AssessmentInstrument.PHQ9,
            answers={"q1": 2},
            total=12,
            band=AssessmentBand.MODERATE,
            created_at=now - timedelta(days=8),
        )
    )
    session.add(
        AssessmentResult(
            user_id=user.id,
            instrument=AssessmentInstrument.GAD7,
            answers={"q1": 1},
            total=9,
            band=AssessmentBand.MILD,
            created_at=now - timedelta(days=7),
        )
    )
    record_id = uuid.uuid4()
    session.add(
        CBTRecord(
            id=record_id,
            user_id=user.id,
            emotion="anxiety",
            intensity_before=80,
            intensity_after=45,
            what_helped=["looking at evidence"],
            key_id="test-key",
            created_at=now - timedelta(days=3),
            **{
                f"{field}_encrypted": encrypt_field(
                    text,
                    user_id=user.id,
                    field_name=cbt_aad(field),
                    wrapped_dek=user.data_key_encrypted,
                )
                for field, text in THOUGHT.items()
            },
        )
    )
    # Ratings improve: on this scale (1 very low, 5 very good) an increase is the
    # helpful direction, which is what makes "most helped by" a true sentence.
    for offset, pre, post in ((12, 2, 4), (6, 2, 4), (2, 3, 4)):
        session.add(
            ExerciseCompletion(
                user_id=user.id,
                exercise_id="box-breathing",
                exercise_title="Box breathing",
                definition_sha256="0" * 64,
                duration_seconds=300,
                pre_rating=pre,
                post_rating=post,
                created_at=now - timedelta(days=offset),
            )
        )
    await session.commit()
    return record_id


def selection(record_id: uuid.UUID, *, items: list[str] | None = None) -> dict[str, Any]:
    """A selection that ticks every item, so the document is the full story."""

    return {
        "items": items or [member.value for member in ShareItem],
        "window_days": 30,
        "note": NOTE,
        "questions": list(QUESTIONS),
        "thought_record_ids": [str(record_id)],
    }


async def turn_sharing_on(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    """The switch is off until the person turns it on."""

    response = await client.post(API + "/preferences", headers=headers, json={"enabled": True})
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is True


async def create_share(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    formats: list[str] | None = None,
    consent_sha256: str | None = None,
    view_limit: int | None = None,
    expires_in_days: int | None = None,
) -> dict[str, Any]:
    """Preview, then create with the hash the preview returned.

    Returns the create response body: token, secret, passcode, share_id.
    """

    preview = await client.post(API + "/preview", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    payload: dict[str, Any] = dict(body)
    payload["formats"] = formats or ["pdf", "fhir"]
    payload["consent_sha256"] = consent_sha256 or preview.json()["consent_sha256"]
    if view_limit is not None:
        payload["view_limit"] = view_limit
    if expires_in_days is not None:
        payload["expires_in_days"] = expires_in_days
    created = await client.post(API, headers=headers, json=payload)
    assert created.status_code == 201, created.text
    return _as_dict(created.json())


async def prepared_share(
    client: httpx.AsyncClient,
    session: AsyncSession,
    *,
    formats: list[str] | None = None,
    view_limit: int | None = None,
    email: str = EMAIL,
) -> tuple[dict[str, str], dict[str, Any]]:
    """One signed-in person, a month of entries, sharing on, and one live share."""

    auth, uid = await person(client, email=email)
    await turn_sharing_on(client, auth)
    user = await session.get(User, uuid.UUID(uid))
    assert user is not None
    record_id = await seed_entries(session, user)
    created = await create_share(
        client, auth, selection(record_id), formats=formats, view_limit=view_limit
    )
    return auth, created


# --------------------------------------------------------------------------- #
# what the browser does
# --------------------------------------------------------------------------- #


def derive_key(created: dict[str, Any], passcode: str | None = None) -> bytes:
    """The content key, derived exactly the way the viewer page derives it."""

    return crypto.derive_key(
        secret=crypto.decode_bytes(
            created["secret"], expected_length=crypto.SECRET_BYTES, what="key"
        ),
        passcode=passcode or created["passcode"],
        token=created["token"],
    )


def decrypt_documents(
    key: bytes, created: dict[str, Any], opened: dict[str, Any]
) -> dict[str, bytes]:
    """Every document in an open response, decrypted with AAD bound to the token."""

    from app.models.share import share_aad

    plain: dict[str, bytes] = {}
    for item in opened["documents"]:
        plain[item["name"]] = crypto.decrypt_document(
            key,
            crypto.decode_document(item["ciphertext"], what=f"{item['name']} ciphertext"),
            crypto.decode_bytes(item["nonce"], expected_length=crypto.NONCE_BYTES, what="nonce"),
            aad=share_aad(created["token"], item["name"]),
        )
    return plain


def pdf_text(raw: bytes) -> str:
    """The extracted text of a rendered PDF, page after page.

    ``pypdf`` is a dev extra and is imported here rather than at module scope:
    this module also supplies ``sample_payload`` to ``scripts/export_fhir_sample.py``,
    which has to run on a plain ``pip install -e .`` (that is what the CI
    fhir-validation job installs) and has no reason to need a PDF reader.
    """

    from pypdf import PdfReader  # dev extra, imported on demand

    reader = PdfReader(io.BytesIO(raw))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def payload_of(raw: bytes) -> SharePayload:
    """The decrypted payload document, validated as the strict model."""

    return SharePayload.model_validate(json.loads(raw.decode("utf-8")))


def bundle_of(raw: bytes) -> dict[str, Any]:
    """The decrypted FHIR bundle as JSON."""

    return _as_dict(json.loads(raw.decode("utf-8")))


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow ``json.loads``/``Response.json()`` output to the dict these return."""

    assert isinstance(value, dict)
    return value


def sample_payload(**overrides: Any) -> SharePayload:
    """A full, realistic payload for the dependency-free unit tests.

    Everything the PDF and the FHIR builder can be asked to render is present,
    so a test can override one field (``sample_payload(note=None)``) and still
    have a document to look at. The numbers match the entries
    :func:`seed_entries` writes, so the two suites describe the same person.
    """

    from app.models.enums import AssessmentInstrument
    from app.services.sharing.items import (
        DIRECTION_NOTE,
        AssessmentShare,
        ExerciseHelpfulness,
        ExerciseHelpfulnessRow,
        LabelCount,
        MoodTrendSummary,
        ThoughtRecordShare,
    )
    from app.services.sharing.terminology import loinc_for

    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    phq9 = loinc_for(AssessmentInstrument.PHQ9)
    gad7 = loinc_for(AssessmentInstrument.GAD7)
    values: dict[str, Any] = {
        "generated_at": now,
        "window_days": 30,
        "items": list(ShareItem),
        "identity": EMAIL,
        "mood_trend": MoodTrendSummary(
            window_days=30,
            window_start=now - timedelta(days=30),
            entries=6,
            first_check_in=now - timedelta(days=25),
            last_check_in=now - timedelta(days=1),
            mean_valence=3.0,
            mean_energy=3.5,
            earlier_mean_valence=2.33,
            recent_mean_valence=3.67,
            direction="higher",
            direction_note=DIRECTION_NOTE["higher"],
            top_emotions=[LabelCount(label="calm", count=3), LabelCount(label="anxious", count=3)],
            top_factors=[LabelCount(label="sleep", count=6), LabelCount(label="work", count=3)],
        ),
        "assessments": [
            AssessmentShare(
                instrument=AssessmentInstrument.GAD7,
                instrument_name="GAD-7",
                completed_at=now - timedelta(days=7),
                total=9,
                band="mild",
                band_label="Mild",
                loinc_code=gad7.code,
                loinc_display=gad7.display,
                loinc_source_url=gad7.source_url,
                ucum_unit=gad7.ucum_unit,
            ),
            AssessmentShare(
                instrument=AssessmentInstrument.PHQ9,
                instrument_name="PHQ-9",
                completed_at=now - timedelta(days=8),
                total=12,
                band="moderate",
                band_label="Moderate",
                loinc_code=phq9.code,
                loinc_display=phq9.display,
                loinc_source_url=phq9.source_url,
                ucum_unit=phq9.ucum_unit,
            ),
        ],
        "thought_records": [
            ThoughtRecordShare(
                id=uuid.UUID("7b1f0d5e-9c3a-4a1e-9f2b-6d0c1e2f3a4b"),
                created_at=now - timedelta(days=3),
                emotion="anxiety",
                intensity_before=80,
                intensity_after=45,
                what_helped=["looking at evidence"],
                **THOUGHT,
            )
        ],
        "exercise_helpfulness": ExerciseHelpfulness(
            window_days=30,
            window_start=now - timedelta(days=30),
            completions=3,
            rated_completions=3,
            exercises=[
                ExerciseHelpfulnessRow(
                    title="Box breathing",
                    times=3,
                    mean_before=2.33,
                    mean_after=4.0,
                    mean_change=1.67,
                )
            ],
            most_helpful="Box breathing",
        ),
        "note": NOTE,
        "questions": list(QUESTIONS),
        "counts": {"mood_check_ins": 6},
    }
    values.update(overrides)
    return SharePayload.model_validate(values)


def flip_bit(raw: bytes, index: int = 0) -> bytes:
    """One byte of ``raw``, changed. Used to prove tampering is detected."""

    changed = bytearray(raw)
    changed[index] ^= 0x01
    return bytes(changed)
