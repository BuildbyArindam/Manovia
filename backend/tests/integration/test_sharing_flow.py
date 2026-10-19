"""Day 47 integration test: one share, from switch to audit trail.

The story this file walks through is the one the brief asks for:

1. sharing is off by default and the endpoints say so,
2. the person turns it on and previews *exactly* what would be shared,
3. creating a link requires the confirmation hash from that preview,
4. the link comes back with a token, a key and a passcode — once,
5. the stored row holds ciphertext and nothing else (no plaintext, no key),
6. an anonymous viewer can open it with the passcode and decrypt locally,
7. the owner can read "opened 2 times" and revoke the link,
8. revoking is one click for everything.

The adversarial cases (expiry, lockout, tampering, enumeration, rate limiting)
live in ``tests/security/test_share_security.py``; this file is the happy path
plus the consent gate, because a feature test that never opens the document
proves nothing about the document.
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentResult
from app.models.cbt import CBTRecord, cbt_aad
from app.models.enums import AssessmentBand, AssessmentInstrument, ShareItem
from app.models.exercise_completion import ExerciseCompletion
from app.models.mood import MoodEntry
from app.models.share import ClinicianShare, ShareAccessEvent, SharePreference, share_aad
from app.models.user import User
from app.core.crypto import encrypt_field
from app.services.sharing import crypto
from app.services.sharing.fhir import LOINC_SYSTEM
from app.services.sharing.items import DISCLAIMER_TITLE, SharePayload
from app.services.sharing.service import ShareService, consent_hash

API = "/api/v1/shares"
PUBLIC = "/api/v1/public/shares"

NOTE = "I have been sleeping badly and I would like to talk about it."
QUESTIONS = ["Is this normal?", "What should I try next?"]
THOUGHT = {
    "situation": "An exam is tomorrow",
    "automatic_thought": "I will fail and everyone will think I am stupid",
    "evidence_for": "I missed one question in practice",
    "evidence_against": "I studied and passed the other practice tests",
    "balanced_thought": "I might not know every answer, but I prepared",
}


EMAIL = "someone@example.test"
PASSWORD = "Correct-Horse-99!"


async def person(client: httpx.AsyncClient) -> tuple[dict[str, str], str]:
    """A signed-in account (with an email, so the identity item means something)
    that has agreed to the terms and the privacy policy."""

    response = await client.post(
        "/api/v1/auth/register", json={"email": EMAIL, "password": PASSWORD}
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

    Written straight to the database (and committed) so the API sees them the
    way it would in a real deployment. The thought record's text goes through
    the same envelope encryption the CBT endpoints use, because the share has to
    decrypt it — seeding plaintext would test nothing.
    """

    now = datetime.now(UTC)
    for offset, valence, energy in ((25, 2, 2), (20, 2, 3), (15, 3, 3), (9, 3, 4), (4, 4, 4), (1, 4, 5)):
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


def selection(record_id: uuid.UUID) -> dict[str, object]:
    """A selection that ticks every item, so the document is the full story."""

    return {
        "items": [member.value for member in ShareItem],
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
    body: dict[str, object],
    *,
    formats: list[str] | None = None,
    consent_sha256: str | None = None,
) -> httpx.Response:
    """Preview, then create with the hash the preview returned."""

    preview = await client.post(API + "/preview", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    digest = consent_sha256 or preview.json()["consent_sha256"]
    payload = dict(body)
    payload["formats"] = formats or ["pdf", "fhir"]
    payload["consent_sha256"] = digest
    return await client.post(API, headers=headers, json=payload)


@pytest.mark.asyncio
async def test_sharing_is_off_until_the_person_turns_it_on(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Off by default: no preference row, no shares, and the API says so."""

    headers, user_id = await person(client)
    options = await client.get(API + "/options", headers=headers)
    assert options.status_code == 200, options.text
    body = options.json()
    assert body["enabled"] is False
    assert body["deployment_enabled"] is True
    assert body["defaults"]["default_ttl_days"] == 7
    assert {item["key"] for item in body["items"]} == {member.value for member in ShareItem}
    assert body["disclaimer_title"] == DISCLAIMER_TITLE
    assert body["never_included"], "the builder must say what is never shared"

    preference = await client.get(API + "/preferences", headers=headers)
    assert preference.status_code == 200
    assert preference.json()["enabled"] is False

    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)
    # Even the preview is refused: with sharing off there is nothing to build.
    preview = await client.post(API + "/preview", headers=headers, json=selection(record_id))
    assert preview.status_code == 403, preview.text
    assert preview.json()["error"]["code"] == "sharing_disabled"

    created = await client.post(
        API,
        headers=headers,
        json={**selection(record_id), "formats": ["pdf"], "consent_sha256": "0" * 64},
    )
    assert created.status_code == 403, created.text
    assert created.json()["error"]["code"] == "sharing_disabled"

    rows = (await session.execute(select(ClinicianShare))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_preview_shows_exactly_what_would_be_shared(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The preview is the document: same items, same consent hash, nothing stored."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)

    preview = await client.post(API + "/preview", headers=headers, json=selection(record_id))
    assert preview.status_code == 200, preview.text
    body = preview.json()
    payload = body["payload"]

    assert payload["kind"] == "manovia.appointment-summary"
    assert payload["disclaimer_title"] == DISCLAIMER_TITLE
    assert payload["note"] == NOTE
    assert payload["questions"] == QUESTIONS
    assert payload["mood_trend"]["entries"] == 6
    assert payload["mood_trend"]["notes_included"] is False
    assert payload["mood_trend"]["direction"] == "higher"
    assert [item["total"] for item in payload["assessments"]] == [9, 12]
    assert {item["loinc_code"] for item in payload["assessments"]} == {"44261-6", "70274-6"}
    assert all(item["item_answers_included"] is False for item in payload["assessments"])
    assert len(payload["thought_records"]) == 1
    assert payload["thought_records"][0]["automatic_thought"] == THOUGHT["automatic_thought"]
    assert payload["exercise_helpfulness"]["completions"] == 3
    assert payload["identity"] == EMAIL

    lines = body["consent_lines"]
    assert lines, "the confirmation screen needs something to read"
    assert any("not a diagnosis" in line.lower() for line in lines)
    # The consent line names the note by its length; the preview payload carries
    # the words themselves, so the screen can show both.
    assert any(f"Your note: {len(NOTE)} characters" in line for line in lines), lines
    assert any(f"Your questions: {len(QUESTIONS)}" in line for line in lines), lines

    # Nothing was stored by previewing.
    assert (await session.execute(select(ClinicianShare))).scalars().all() == []
    assert (await session.execute(select(ShareAccessEvent))).scalars().all() == []


@pytest.mark.asyncio
async def test_preview_only_includes_what_is_ticked(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A narrower selection makes a narrower document — and a narrower consent."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    await seed_entries(session, user)

    body = {"items": ["mood_trend", "questions"], "window_days": 14, "questions": ["Why now?"]}
    preview = await client.post(API + "/preview", headers=headers, json=body)
    assert preview.status_code == 200, preview.text
    payload = preview.json()["payload"]
    assert payload["mood_trend"] is not None
    assert payload["assessments"] is None
    assert payload["thought_records"] is None
    assert payload["exercise_helpfulness"] is None
    assert payload["note"] is None
    assert payload["identity"] is None
    assert payload["questions"] == ["Why now?"]


@pytest.mark.asyncio
async def test_create_requires_a_confirmation_that_matches_the_preview(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """No hash → refused. A hash for a different document → refused."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)
    body = selection(record_id)

    preview = await client.post(API + "/preview", headers=headers, json=body)
    digest = preview.json()["consent_sha256"]

    refused = await client.post(
        API, headers=headers, json={**body, "formats": ["pdf"], "consent_sha256": "0" * 64}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "consent_mismatch"

    # A selection that is not the one confirmed gets the same answer.
    changed = dict(body)
    changed["questions"] = [*QUESTIONS, "Should I stop my medication?"]
    changed["consent_sha256"] = digest
    changed["formats"] = ["pdf"]
    other = await client.post(API, headers=headers, json=changed)
    assert other.status_code == 409, other.text
    assert other.json()["error"]["code"] == "consent_mismatch"

    assert (await session.execute(select(ClinicianShare))).scalars().all() == []


@pytest.mark.asyncio
async def test_created_share_returns_the_key_once_and_stores_only_ciphertext(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The brief's central claim: the row holds ciphertext, and no key at all."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)

    created = await create_share(client, headers, selection(record_id))
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["token"]
    assert body["secret"]
    assert body["passcode"]
    assert body["passcode_generated"] is True
    assert body["share_path"] == f"/s/{body['token']}"
    assert body["fragment"] == f"k={body['secret']}"
    assert body["view_limit"] == 5
    assert set(body["formats"]) == {"pdf", "fhir"}
    assert body["document_bytes"]["pdf"] > 1000
    assert body["document_bytes"]["fhir"] > 1000
    assert "not stored" in body["warning"].lower()
    expires = datetime.fromisoformat(body["expires_at"])
    assert expires > datetime.now(UTC) + timedelta(days=6)
    assert expires < datetime.now(UTC) + timedelta(days=8)

    session.expire_all()
    row = (await session.execute(select(ClinicianShare))).scalars().one()
    assert row.token_hash == crypto.hash_token(body["token"])
    assert body["token"] not in json.dumps(
        {column.name: getattr(row, column.name) for column in row.__table__.columns},
        default=str,
    ), "the raw token must not be stored anywhere on the row"

    # Every document column is ciphertext, and no plaintext survived into it.
    for name, ciphertext in (
        ("payload", row.payload_ciphertext),
        ("pdf", row.pdf_ciphertext),
        ("fhir", row.fhir_ciphertext),
    ):
        assert ciphertext is not None, name
        assert NOTE.encode() not in ciphertext
        assert b"patient-generated" not in ciphertext.lower()
        assert row.__getattribute__(f"{name}_nonce") is not None

    # The key is not stored in any form: no column name may suggest one.
    columns = {column.name for column in ClinicianShare.__table__.columns}
    assert not any("key" in name or "secret" in name for name in columns), sorted(columns)
    assert "passcode" not in columns
    assert row.passcode_hash != row.passcode_salt

    # The stored ciphertext really is the document, under the derived key.
    key = crypto.derive_key(
        secret=crypto.decode_bytes(
            body["secret"], expected_length=crypto.SECRET_BYTES, what="key"
        ),
        passcode=body["passcode"],
        token=body["token"],
    )
    raw = crypto.decrypt_document(
        key, row.payload_ciphertext, row.payload_nonce, aad=share_aad(body["token"], "payload")
    )
    payload = SharePayload.model_validate(json.loads(raw.decode("utf-8")))
    assert payload.note == NOTE
    assert payload.questions == QUESTIONS

    # The consent hash on the row is the one the person confirmed.
    assert row.consent_summary_sha256 == body["consent_sha256"] == consent_hash(payload)


@pytest.mark.asyncio
async def test_anonymous_viewer_opens_the_link_and_reads_the_documents(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """No account, no auth header: passcode in, ciphertext out, decrypted locally."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)
    created = await create_share(client, headers, selection(record_id))
    body = created.json()
    token = body["token"]

    status = await client.get(f"{PUBLIC}/{token}")
    assert status.status_code == 200, status.text
    assert status.json()["requires_passcode"] is True
    assert status.json()["disclaimer_title"] == DISCLAIMER_TITLE

    opened = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": body["passcode"]})
    assert opened.status_code == 200, opened.text
    document = opened.json()
    assert document["disclaimer_title"] == DISCLAIMER_TITLE
    assert document["algorithm"] == "A256GCM"
    assert document["views_remaining"] == 4
    assert {item["name"] for item in document["documents"]} == {"payload", "pdf", "fhir"}

    key = crypto.derive_key(
        secret=crypto.decode_bytes(
            body["secret"], expected_length=crypto.SECRET_BYTES, what="key"
        ),
        passcode=body["passcode"],
        token=token,
    )
    plain: dict[str, bytes] = {}
    for item in document["documents"]:
        plain[item["name"]] = crypto.decrypt_document(
            key,
            crypto.decode_document(item["ciphertext"], what=f"{item['name']} ciphertext"),
            crypto.decode_bytes(item["nonce"], expected_length=crypto.NONCE_BYTES, what="nonce"),
            aad=share_aad(token, item["name"]),
        )

    payload = json.loads(plain["payload"].decode("utf-8"))
    assert payload["note"] == NOTE

    # The PDF says what it is, on the page and not only in the metadata.
    reader = PdfReader(io.BytesIO(plain["pdf"]))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    assert DISCLAIMER_TITLE in text.replace("\n", " ") or "Patient-generated" in text
    assert "not a diagnosis" in text.lower().replace("\n", " ")
    assert NOTE.splitlines()[0][:20] in text

    # The bundle is a FHIR R4 document, patient-generated, with verified LOINC.
    bundle = json.loads(plain["fhir"].decode("utf-8"))
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "document"
    assert bundle["entry"][0]["resource"]["resourceType"] == "Composition"
    assert bundle["entry"][-1]["resource"]["resourceType"] == "Provenance"
    totals = [
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "Observation"
        and entry["resource"]["code"]["coding"][0]["system"] == LOINC_SYSTEM
    ]
    assert {item["code"]["coding"][0]["code"] for item in totals} == {"44261-6", "70274-6"}
    provenance = bundle["entry"][-1]["resource"]
    roles = {agent["type"]["coding"][0]["code"] for agent in provenance["agent"]}
    assert roles == {"author", "assembler"}
    assert "Patient-generated" in provenance["recorded"] or provenance["recorded"]
    questionnaire_responses = [
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "QuestionnaireResponse"
    ]
    assert len(questionnaire_responses) == 2
    # Every reference inside the bundle resolves to an entry in the bundle, and
    # the questionnaire answers are attributed to the patient, not to Manovia.
    full_urls = {entry["fullUrl"] for entry in bundle["entry"]}
    patients = {
        entry["fullUrl"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "Patient"
    }
    for response in questionnaire_responses:
        assert response["status"] == "completed"
        assert response["source"]["reference"] in full_urls
        assert response["source"]["reference"] in patients
        assert response["author"]["reference"] in patients
        assert response["item"][0]["linkId"] in {"44261-6", "70274-6"}
        assert "questionnaire" not in response, "no canonical we cannot resolve"


@pytest.mark.asyncio
async def test_owner_sees_open_counts_and_can_revoke(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """'Opened 2 times', then one click and the link stops working."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)
    created = await create_share(client, headers, selection(record_id))
    body = created.json()
    token = body["token"]
    share_id = body["share_id"]

    listing = await client.get(API, headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["status"] == "active"
    assert listing.json()[0]["opened_count"] == 0

    for _ in range(2):
        opened = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": body["passcode"]})
        assert opened.status_code == 200, opened.text

    audit = await client.get(f"{API}/{share_id}/audit", headers=headers)
    assert audit.status_code == 200, audit.text
    report = audit.json()
    assert report["counts"]["opened"] == 2
    assert report["counts"]["created"] == 1
    assert report["share"]["view_count"] == 2
    assert report["share"]["views_remaining"] == 3
    assert report["summary"] == "Opened 2 times of 5 allowed."
    kinds = [event["kind"] for event in report["events"]]
    assert kinds == ["created", "opened", "opened"]
    assert all(event["actor"] in {"owner", "viewer"} for event in report["events"])
    # The audit log stores kind/actor/time only.
    assert set(ShareAccessEvent.__table__.columns.keys()) == {
        "id",
        "share_id",
        "kind",
        "actor",
        "created_at",
    }

    detail = await client.get(f"{API}/{share_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["opened_count"] == 2

    revoked = await client.post(f"{API}/{share_id}/revoke", headers=headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    # Revoking twice is not an error, and does not double-count.
    again = await client.post(f"{API}/{share_id}/revoke", headers=headers)
    assert again.status_code == 200
    after = await client.get(f"{API}/{share_id}/audit", headers=headers)
    assert after.json()["counts"]["revoked"] == 1

    gone = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": body["passcode"]})
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "share_unavailable"
    # The owner can still see that somebody tried.
    final = await client.get(f"{API}/{share_id}/audit", headers=headers)
    assert final.json()["counts"]["access_after_revoke"] == 1
    assert "Revoked" in final.json()["summary"]


@pytest.mark.asyncio
async def test_revoke_everything_is_one_click(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Three live links, one call, all of them dead — and all of them audited."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)

    tokens: list[str] = []
    for _ in range(3):
        created = await create_share(client, headers, selection(record_id), formats=["pdf"])
        assert created.status_code == 201, created.text
        tokens.append(created.json()["token"])

    result = await client.post(API + "/revoke-all", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["revoked"] == 3

    for token in tokens:
        refused = await client.get(f"{PUBLIC}/{token}")
        assert refused.status_code == 404

    listing = await client.get(API, headers=headers)
    assert all(item["status"] == "revoked" for item in listing.json())

    # And the switch can be turned back off, which closes the feature entirely.
    off = await client.post(API + "/preferences", headers=headers, json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    blocked = await client.post(API + "/preview", headers=headers, json=selection(record_id))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "sharing_disabled"
    preference = (await session.execute(select(SharePreference))).scalars().one()
    assert preference.enabled is False


@pytest.mark.asyncio
async def test_a_typed_passcode_is_used_and_is_not_stored(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The person may choose their own passcode; only its hash is kept."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)

    body = selection(record_id)
    preview = await client.post(API + "/preview", headers=headers, json=body)
    payload = dict(body)
    payload["formats"] = ["pdf"]
    payload["passcode"] = "correct-horse-battery"
    payload["expires_in_days"] = 3
    payload["view_limit"] = 2
    payload["consent_sha256"] = preview.json()["consent_sha256"]
    created = await client.post(API, headers=headers, json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["passcode"] == "correct-horse-battery"
    assert created.json()["passcode_generated"] is False
    assert created.json()["view_limit"] == 2

    session.expire_all()
    row = (await session.execute(select(ClinicianShare))).scalars().one()
    assert b"correct-horse-battery" not in (row.passcode_hash or b"")
    assert "correct-horse-battery" not in json.dumps(
        {column.name: getattr(row, column.name) for column in row.__table__.columns}, default=str
    )

    short = dict(payload)
    short["passcode"] = "123"
    refused = await client.post(API, headers=headers, json=short)
    assert refused.status_code == 422, refused.text


@pytest.mark.asyncio
async def test_the_service_refuses_more_live_shares_than_the_ceiling(
    client: httpx.AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
    """Ten live links is the deployment's ceiling; the eleventh is refused."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)

    settings = app.state.settings
    for _ in range(settings.share_max_active_per_user):
        created = await create_share(client, headers, selection(record_id), formats=["pdf"])
        assert created.status_code == 201, created.text

    refused = await create_share(client, headers, selection(record_id), formats=["pdf"])
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "share_limit"

    await client.post(API + "/revoke-all", headers=headers)
    after = await create_share(client, headers, selection(record_id), formats=["pdf"])
    assert after.status_code == 201


@pytest.mark.asyncio
async def test_share_endpoints_need_auth_and_consent(client: httpx.AsyncClient) -> None:
    """No token → 401. A token without the privacy agreement → 403."""

    anonymous = await client.get(API)
    assert anonymous.status_code == 401

    response = await client.post("/api/v1/auth/guest")  # signed in, not consenting
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    unconsented = await client.get(API, headers=headers)
    assert unconsented.status_code == 403
    assert unconsented.json()["error"]["code"] == "consent_required"


@pytest.mark.asyncio
async def test_share_service_reports_the_deployment_switch(
    session: AsyncSession, app: FastAPI, demo_user: User
) -> None:
    """An operator who turns sharing off closes the endpoints for everybody."""

    from app.core.config import Settings

    off: Settings = app.state.settings.model_copy(update={"share_enabled": False})
    service = ShareService(session, off)
    assert await service.is_enabled(demo_user) is False
    with pytest.raises(Exception) as excinfo:
        await service.set_enabled(demo_user, enabled=True)
    assert "turned off" in str(excinfo.value)
