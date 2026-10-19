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
from pypdf import PdfReader
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
    for offset, pre, post in ((12, 4, 2), (6, 4, 2), (2, 3, 2)):
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
    return created.json()


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
    """The extracted text of a rendered PDF, page after page."""

    reader = PdfReader(io.BytesIO(raw))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def payload_of(raw: bytes) -> SharePayload:
    """The decrypted payload document, validated as the strict model."""

    return SharePayload.model_validate(json.loads(raw.decode("utf-8")))


def bundle_of(raw: bytes) -> dict[str, Any]:
    """The decrypted FHIR bundle as JSON."""

    return json.loads(raw.decode("utf-8"))


def flip_bit(raw: bytes, index: int = 0) -> bytes:
    """One byte of ``raw``, changed. Used to prove tampering is detected."""

    changed = bytearray(raw)
    changed[index] ^= 0x01
    return bytes(changed)
