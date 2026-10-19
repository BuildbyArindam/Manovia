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
proves nothing about the document. Shared plumbing is in
``tests/sharing_helpers.py``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.share import (
    ClinicianShare,
    ShareAccessEvent,
    SharePreference,
    share_aad,
)
from app.models.user import User
from app.services.sharing import crypto
from app.services.sharing.fhir import LOINC_SYSTEM
from app.services.sharing.items import DISCLAIMER_TITLE
from app.services.sharing.service import ShareService, consent_hash
from tests.sharing_helpers import (
    API,
    EMAIL,
    NOTE,
    PUBLIC,
    QUESTIONS,
    THOUGHT,
    bundle_of,
    create_share,
    decrypt_documents,
    derive_key,
    payload_of,
    pdf_text,
    person,
    prepared_share,
    seed_entries,
    selection,
    turn_sharing_on,
)


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
    assert {item["key"] for item in body["items"]} == {
        "mood_trend",
        "assessments",
        "thought_records",
        "exercise_helpfulness",
        "note",
        "questions",
        "identity",
    }
    assert body["disclaimer_title"] == DISCLAIMER_TITLE
    assert body["never_included"], "the builder must say what is never shared"
    assert body["limits"]["windows"] == [14, 30, 90]

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

    assert (await session.execute(select(ClinicianShare))).scalars().all() == []
    assert (await session.execute(select(SharePreference))).scalars().all() == []


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
    assert {item["loinc_source_url"] for item in payload["assessments"]} == {
        "https://loinc.org/44261-6",
        "https://loinc.org/70274-6",
    }
    assert all(item["item_answers_included"] is False for item in payload["assessments"])
    assert len(payload["thought_records"]) == 1
    assert payload["thought_records"][0]["automatic_thought"] == THOUGHT["automatic_thought"]
    assert payload["exercise_helpfulness"]["completions"] == 3
    assert payload["exercise_helpfulness"]["most_helpful"] == "Box breathing"
    assert payload["identity"] == EMAIL

    lines = body["consent_lines"]
    assert lines, "the confirmation screen needs something to read"
    assert any("not a diagnosis" in line.lower() for line in lines)
    # The consent line names the note by its length; the preview payload carries
    # the words themselves, so the screen can show both.
    assert any(f"Your note: {len(NOTE)} characters" in line for line in lines), lines
    assert any(f"Your questions: {len(QUESTIONS)}" in line for line in lines), lines
    assert any("Never included" in line for line in lines), lines

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
    assert payload["items"] == ["mood_trend", "questions"]


@pytest.mark.asyncio
async def test_selection_rules_are_enforced_before_anything_is_built(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A note without the tick, an unknown record, a 900-day window: all refused."""

    headers, user_id = await person(client)
    await turn_sharing_on(client, headers)
    user = await session.get(User, uuid.UUID(user_id))
    assert user is not None
    record_id = await seed_entries(session, user)

    cases: list[dict[str, object]] = [
        {"items": ["mood_trend"], "window_days": 30, "note": NOTE},  # unticked note
        {"items": ["note"], "window_days": 30},  # ticked, nothing written
        {"items": ["questions"], "window_days": 30, "questions": []},  # ticked, empty
        {"items": ["thought_records"], "window_days": 30},  # ticked, none chosen
        {
            "items": ["thought_records"],
            "window_days": 30,
            "thought_record_ids": [str(uuid.uuid4())],  # somebody else's, or nobody's
        },
        {"items": ["mood_trend"], "window_days": 365},  # not one of the windows
        {"items": []},  # nothing at all
        {"items": ["mood_trend", "mood_trend"], "window_days": 30},  # repeated
    ]
    for case in cases:
        response = await client.post(API + "/preview", headers=headers, json=case)
        assert response.status_code == 422, (case, response.text)

    # A record that exists but belongs to somebody else is refused too.
    stranger_headers, _ = await person(client, email="stranger@example.test")
    await turn_sharing_on(client, stranger_headers)
    refused = await client.post(
        API + "/preview",
        headers=stranger_headers,
        json={
            "items": ["thought_records"],
            "window_days": 30,
            "thought_record_ids": [str(record_id)],
        },
    )
    assert refused.status_code == 422, refused.text
    assert "could not be found" in refused.json()["error"]["message"]


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
    changed = await client.post(
        API,
        headers=headers,
        json={
            **body,
            "questions": [*QUESTIONS, "Should I stop my medication?"],
            "formats": ["pdf"],
            "consent_sha256": digest,
        },
    )
    assert changed.status_code == 409, changed.text
    assert changed.json()["error"]["code"] == "consent_mismatch"

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
    body = await create_share(client, headers, selection(record_id))

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
    stored = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    assert row.token_hash == crypto.hash_token(body["token"])
    assert body["token"] not in json.dumps(stored, default=str), (
        "the raw token must not be stored anywhere on the row"
    )
    assert NOTE not in json.dumps(stored, default=str)

    # Every document column is ciphertext, and no plaintext survived into it.
    for name, ciphertext in (
        ("payload", row.payload_ciphertext),
        ("pdf", row.pdf_ciphertext),
        ("fhir", row.fhir_ciphertext),
    ):
        assert ciphertext is not None, name
        assert NOTE.encode() not in ciphertext
        assert b"patient-generated" not in ciphertext.lower()
        assert getattr(row, f"{name}_nonce") is not None

    # The key is not stored in any form: no column name may suggest one.
    columns = set(stored)
    assert not any("key" in name or "secret" in name for name in columns), sorted(columns)
    assert "passcode" not in columns
    assert row.passcode_hash != row.passcode_salt

    # The stored ciphertext really is the document, under the derived key.
    key = derive_key(body)
    raw = crypto.decrypt_document(
        key, row.payload_ciphertext, row.payload_nonce, aad=share_aad(body["token"], "payload")
    )
    payload = payload_of(raw)
    assert payload.note == NOTE
    assert payload.questions == QUESTIONS

    # The consent hash on the row is the one the person confirmed.
    assert row.consent_summary_sha256 == body["consent_sha256"] == consent_hash(payload)


@pytest.mark.asyncio
async def test_anonymous_viewer_opens_the_link_and_reads_the_documents(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """No account, no auth header: passcode in, ciphertext out, decrypted locally."""

    _, created = await prepared_share(client, session)
    token = created["token"]

    anonymous = await client.get(API)
    assert anonymous.status_code == 401, "the owner side still needs an account"

    status = await client.get(f"{PUBLIC}/{token}")
    assert status.status_code == 200, status.text
    assert status.json()["requires_passcode"] is True
    assert status.json()["disclaimer_title"] == DISCLAIMER_TITLE
    assert set(status.json()["formats"]) == {"pdf", "fhir"}
    # Nothing about the contents is revealed before the passcode.
    assert "note" not in status.text.lower()
    assert NOTE not in status.text

    opened = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": created["passcode"]})
    assert opened.status_code == 200, opened.text
    document = opened.json()
    assert document["disclaimer_title"] == DISCLAIMER_TITLE
    assert document["algorithm"] == "A256GCM"
    assert document["views_remaining"] == 4
    assert {item["name"] for item in document["documents"]} == {"payload", "pdf", "fhir"}
    assert {item["content_type"] for item in document["documents"]} == {
        "application/json",
        "application/pdf",
        "application/fhir+json",
    }

    plain = decrypt_documents(derive_key(created), created, document)
    payload = payload_of(plain["payload"])
    assert payload.note == NOTE

    # The PDF says what it is, on the page and not only in the metadata.
    text = pdf_text(plain["pdf"])
    assert DISCLAIMER_TITLE in text
    assert "not a diagnosis" in text.lower()
    assert NOTE.splitlines()[0][:20] in text
    assert QUESTIONS[0] in text

    # The bundle is a FHIR R4 document, patient-generated, with verified LOINC.
    bundle = bundle_of(plain["fhir"])
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
    questionnaire_responses = [
        entry["resource"]
        for entry in bundle["entry"]
        if entry["resource"]["resourceType"] == "QuestionnaireResponse"
    ]
    assert len(questionnaire_responses) == 2
    # Every reference inside the bundle resolves to an entry in the bundle, and
    # the answers are attributed to the patient, not to Manovia.
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

    headers, created = await prepared_share(client, session)
    token = created["token"]
    share_id = created["share_id"]

    listing = await client.get(API, headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert listing.json()[0]["status"] == "active"
    assert listing.json()[0]["opened_count"] == 0
    assert listing.json()[0]["summary"] == "Opened 0 times of 5 allowed."

    for _ in range(2):
        opened = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": created["passcode"]})
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

    gone = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": created["passcode"]})
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

    tokens = [
        (await create_share(client, headers, selection(record_id), formats=["pdf"]))["token"]
        for _ in range(3)
    ]

    result = await client.post(API + "/revoke-all", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["revoked"] == 3

    for token in tokens:
        refused = await client.get(f"{PUBLIC}/{token}")
        assert refused.status_code == 404

    listing = await client.get(API, headers=headers)
    assert all(item["status"] == "revoked" for item in listing.json())
    audits = [
        await client.get(f"{API}/{item['id']}/audit", headers=headers) for item in listing.json()
    ]
    assert all(response.json()["counts"].get("revoked") == 1 for response in audits)

    # A second revoke-all has nothing left to do.
    empty = await client.post(API + "/revoke-all", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["revoked"] == 0

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
    payload: dict[str, object] = dict(body)
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
    expires = datetime.fromisoformat(created.json()["expires_at"])
    assert expires < datetime.now(UTC) + timedelta(days=4)

    session.expire_all()
    row = (await session.execute(select(ClinicianShare))).scalars().one()
    stored = json.dumps(
        {column.name: getattr(row, column.name) for column in row.__table__.columns}, default=str
    )
    assert b"correct-horse-battery" not in (row.passcode_hash or b"")
    assert "correct-horse-battery" not in stored
    assert row.passcode_failures == 0

    short = dict(payload)
    short["passcode"] = "123"
    refused = await client.post(API, headers=headers, json=short)
    assert refused.status_code == 422, refused.text

    # Settings are enforced too: a 90-day link and a 999-view link are refused.
    for field_name, value in (("expires_in_days", 90), ("view_limit", 999)):
        bad = dict(body)
        bad["formats"] = ["pdf"]
        bad["consent_sha256"] = preview.json()["consent_sha256"]
        bad[field_name] = value
        rejected = await client.post(API, headers=headers, json=bad)
        assert rejected.status_code == 422, (field_name, rejected.text)


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
    body = selection(record_id)

    settings = app.state.settings
    for _ in range(settings.share_max_active_per_user):
        await create_share(client, headers, body, formats=["pdf"])

    preview = await client.post(API + "/preview", headers=headers, json=body)
    refused = await client.post(
        API,
        headers=headers,
        json={
            **body,
            "formats": ["pdf"],
            "consent_sha256": preview.json()["consent_sha256"],
        },
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "share_limit"

    await client.post(API + "/revoke-all", headers=headers)
    await create_share(client, headers, body, formats=["pdf"])


@pytest.mark.asyncio
async def test_share_endpoints_need_auth_and_consent(client: httpx.AsyncClient) -> None:
    """No token → 401. A token without the privacy agreement → 403."""

    anonymous = await client.get(API)
    assert anonymous.status_code == 401

    response = await client.post("/api/v1/auth/guest")  # signed in, not consenting
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    for path in (API, API + "/options", API + "/preferences"):
        unconsented = await client.get(path, headers=headers)
        assert unconsented.status_code == 403, path
        assert unconsented.json()["error"]["code"] == "consent_required"

    written = await client.post(API + "/preferences", headers=headers, json={"enabled": True})
    assert written.status_code == 403
    assert written.json()["error"]["code"] == "consent_required"


@pytest.mark.asyncio
async def test_one_person_cannot_read_another_persons_share(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Ownership is checked on every owner-side id, including the audit log."""

    headers, created = await prepared_share(client, session)
    share_id = created["share_id"]

    stranger, _ = await person(client, email="stranger@example.test")
    for path in (f"{API}/{share_id}", f"{API}/{share_id}/audit"):
        response = await client.get(path, headers=stranger)
        assert response.status_code == 404, (path, response.text)
        assert response.json()["error"]["code"] == "share_not_found"
    revoked = await client.post(f"{API}/{share_id}/revoke", headers=stranger)
    assert revoked.status_code == 404

    # The owner's own list is unaffected, and the stranger's is empty.
    assert len((await client.get(API, headers=headers)).json()) == 1
    assert (await client.get(API, headers=stranger)).json() == []


@pytest.mark.asyncio
async def test_share_service_reports_the_deployment_switch(
    session: AsyncSession, app: FastAPI, demo_user: User
) -> None:
    """An operator who turns sharing off closes the endpoints for everybody."""

    off = app.state.settings.model_copy(update={"share_enabled": False})
    service = ShareService(session, off)
    assert await service.is_enabled(demo_user) is False
    with pytest.raises(Exception) as excinfo:
        await service.set_enabled(demo_user, enabled=True)
    assert "turned off" in str(excinfo.value)
