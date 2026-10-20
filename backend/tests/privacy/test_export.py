"""The export: every feature's rows, decrypted, for the owner only.

The archive is asserted structurally (which files exist) *and* by content (the
sentence that was written is the sentence that comes back). A test that only
checked "a ZIP with twelve entries" would pass for an archive of empty files,
which is exactly the failure mode a privacy feature must not have.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import date
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_field, encrypt_field
from app.db.repos import (
    AssessmentRepository,
    ChatRepository,
    JournalRepository,
    MoodRepository,
    PlanRepository,
    SettingsRepository,
    UserRepository,
)
from app.db.repos.exercises import ExerciseRepository
from app.models.cbt import CBTRecord, cbt_aad
from app.models.enums import (
    AssessmentBand,
    AssessmentInstrument,
    MessageRole,
    PlanItemKind,
    RiskLevel,
)
from app.models.journal import JournalEntry
from app.models.user import User
from app.services.exercises.engine import get_catalog
from tests.privacy.conftest import PRIVACY, Person, guest

EXPORT = f"{PRIVACY}/export"

JOURNAL_BODY = "The export sentinel: I walked to the lake and counted six herons."
JOURNAL_TITLE = "Herons"
MOOD_NOTE = "Export sentinel note: tired but lighter than yesterday."
CHAT_USER = "Export sentinel message: I have an exam on Friday and I feel behind."
CHAT_REPLY = "That sounds like a lot to carry. What is the smallest piece you could start with?"
MEMORY = "Exams are on their mind; they feel behind."
CBT_THOUGHT = "Everyone will see that I failed."
PLAN_WHY = "You checked in twice this week."


async def _fill_every_feature(
    session: AsyncSession, client: httpx.AsyncClient, person: Person
) -> None:
    """Create at least one row in every feature the export promises to include."""
    user: User = await UserRepository(session).get(person.id)  # type: ignore[assignment]
    await session.refresh(user)

    journals = JournalRepository(session)
    await journals.create(
        user_id=user.id,
        title=JOURNAL_TITLE,
        body=JOURNAL_BODY,
        sentiment=0.4,
        emotion_scores={"calm": 0.7, "joy": 0.3},
        template="free",
        prompt_id="prompt-1",
    )

    await MoodRepository(session).record(
        user_id=user.id,
        valence=4,
        energy=3,
        emotions=["calm"],
        factors={"sleep": 4},
        note=MOOD_NOTE,
    )

    chats = ChatRepository(session)
    chat_session = await chats.create(user_id=user.id)
    await chats.add_message(
        session_id=chat_session.id,
        role=MessageRole.USER,
        content=CHAT_USER,
        risk_level=RiskLevel.NONE,
    )
    await chats.add_message(
        session_id=chat_session.id,
        role=MessageRole.ASSISTANT,
        content=CHAT_REPLY,
        risk_level=RiskLevel.NONE,
        emotion="calm",
    )
    await chats.update_memory(chat_session.id, MEMORY)

    await AssessmentRepository(session).record(
        user_id=user.id,
        instrument=AssessmentInstrument.PHQ9,
        answers={f"phq9_{i}": 1 for i in range(1, 10)},
        total=9,
        band=AssessmentBand.MILD,
    )

    catalog = get_catalog()
    definition = catalog.get("box-breathing")
    assert definition is not None, "the shipped catalogue must contain box-breathing"
    await ExerciseRepository(session).record(
        completion_id=uuid.uuid4(),
        user_id=user.id,
        exercise=definition,
        pre_rating=2,
        post_rating=4,
    )

    wrapped, key_id = await UserRepository(session).ensure_data_key(user.id)
    session.add(
        CBTRecord(
            user_id=user.id,
            situation_encrypted=encrypt_field(
                "A presentation in front of the class",
                user_id=user.id,
                field_name=cbt_aad("situation"),
                wrapped_dek=wrapped,
            ),
            automatic_thought_encrypted=encrypt_field(
                CBT_THOUGHT,
                user_id=user.id,
                field_name=cbt_aad("automatic_thought"),
                wrapped_dek=wrapped,
            ),
            evidence_for_encrypted=encrypt_field(
                "I stumbled once last time.",
                user_id=user.id,
                field_name=cbt_aad("evidence_for"),
                wrapped_dek=wrapped,
            ),
            evidence_against_encrypted=encrypt_field(
                "I answered questions well.",
                user_id=user.id,
                field_name=cbt_aad("evidence_against"),
                wrapped_dek=wrapped,
            ),
            balanced_thought_encrypted=encrypt_field(
                "One stumble is not the whole talk.",
                user_id=user.id,
                field_name=cbt_aad("balanced_thought"),
                wrapped_dek=wrapped,
            ),
            emotion="anxiety",
            intensity_before=80,
            intensity_after=45,
            what_helped=["evidence", "breathing"],
            key_id=key_id,
        )
    )

    await PlanRepository(session).create(
        user_id=user.id,
        plan_date=date(2026, 10, 11),
        kind=PlanItemKind.CHECK_IN,
        title="A quick check-in",
        why=PLAN_WHY,
    )
    await SettingsRepository(session).upsert(
        user_id=user.id,
        preferred_exercise_tags=["breathing"],
        quiet_hours_enabled=True,
        quiet_start="21:00",
        quiet_end="06:00",
        mood_outlook_enabled=True,
    )
    await session.commit()


def _zip_entries(payload: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        assert all(name.startswith("manovia-export/") for name in names)
        return {
            name.split("/", 1)[1]: json.loads(archive.read(name).decode("utf-8"))
            if name.endswith(".json")
            else archive.read(name).decode("utf-8")
            for name in names
        }


async def test_export_zip_contains_every_feature_decrypted(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await guest(client, kinds=("terms", "privacy", "ai_disclosure", "store_chat"))
    await _fill_every_feature(session, client, person)

    response = await client.get(EXPORT, headers=person.headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"

    files = _zip_entries(response.content)
    assert set(files) == {
        "README.txt",
        "manifest.json",
        "profile.json",
        "consents.json",
        "privacy-settings.json",
        "user-settings.json",
        "chat.json",
        "journal.json",
        "mood.json",
        "assessments.json",
        "exercises.json",
        "cbt.json",
        "plan.json",
        "safety-events.json",
        # Day 47: the decisions and the audit trail of any share link, never the
        # document inside one (it is ciphertext, and its key was never stored).
        "clinician-shares.json",
    }

    manifest = files["manifest.json"]
    assert manifest["format_version"] == "manovia-export-2"
    assert manifest["contents_are_decrypted"] is True
    assert manifest["counts"]["journal_entries"] == 1
    assert manifest["counts"]["mood_entries"] == 1
    assert manifest["counts"]["messages"] == 2
    assert manifest["truncated_tables"] == {}
    assert manifest["unreadable_rows"] == {"messages": 0, "journal_entries": 0, "cbt_records": 0}

    profile = files["profile.json"]
    assert profile["id"] == str(person.id)
    assert profile["is_anonymous"] is True
    assert "password_hash" not in profile

    journal = files["journal.json"]
    assert journal[0]["title"] == JOURNAL_TITLE
    assert journal[0]["body"] == JOURNAL_BODY
    assert journal[0]["sentiment"] == 0.4
    assert journal[0]["emotion_scores"] == {"calm": 0.7, "joy": 0.3}

    mood = files["mood.json"]
    assert mood[0]["note"] == MOOD_NOTE
    assert mood[0]["valence"] == 4 and mood[0]["emotions"] == ["calm"]

    chat = files["chat.json"]
    assert [turn["text"] for turn in chat[0]["messages"]] == [CHAT_USER, CHAT_REPLY]
    assert chat[0]["memory_summary"] == MEMORY
    assert chat[0]["messages"][1]["emotion"] == "calm"

    cbt = files["cbt.json"]
    assert cbt[0]["automatic_thought"] == CBT_THOUGHT
    assert cbt[0]["intensity_before"] == 80 and cbt[0]["intensity_after"] == 45

    exercises = files["exercises.json"]
    assert exercises[0]["exercise_id"] == "box-breathing"
    assert (exercises[0]["pre_rating"], exercises[0]["post_rating"]) == (2, 4)

    plan = files["plan.json"]
    assert plan[0]["why"] == PLAN_WHY and plan[0]["kind"] == "check_in"

    user_settings = files["user-settings.json"]
    assert user_settings["preferred_exercise_tags"] == ["breathing"]
    assert user_settings["quiet_hours_enabled"] is True
    assert user_settings["mood_outlook_enabled"] is True
    assert user_settings["been_saved"] is True

    assessments = files["assessments.json"]
    assert assessments[0]["instrument"] == "phq9" and assessments[0]["total"] == 9

    assert "Your data was encrypted at rest" in files["README.txt"]


async def test_export_json_format_is_the_same_document(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await guest(client)
    await _fill_every_feature(session, client, person)

    response = await client.get(f"{EXPORT}?format=json", headers=person.headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]

    document = response.json()
    assert document["format_version"] == "manovia-export-2"
    assert document["files"]["journal.json"][0]["body"] == JOURNAL_BODY
    assert document["files"]["chat.json"][0]["messages"][0]["text"] == CHAT_USER


async def test_export_contains_only_the_callers_rows(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Two accounts, one export: nothing of the other person may appear."""
    mine = await guest(client)
    theirs = await guest(client)
    await _fill_every_feature(session, client, theirs)

    response = await client.get(f"{EXPORT}?format=json", headers=mine.headers)
    assert response.status_code == 200
    body = response.text
    assert JOURNAL_BODY not in body
    assert CHAT_USER not in body
    assert MOOD_NOTE not in body
    assert response.json()["counts"]["journal_entries"] == 0


async def test_export_reports_unreadable_rows_instead_of_dropping_them(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rotated-away key must be visible in the manifest, not silently omitted.

    A row is corrupted here by re-encrypting a body with a *different* account's
    key (which is exactly what an authentication failure looks like), and the
    export must say so.
    """
    person = await guest(client)
    await _fill_every_feature(session, client, person)

    other = await UserRepository(session).create(is_anonymous=True)
    await session.commit()
    await session.refresh(other)
    wrapped_other, _ = await UserRepository(session).ensure_data_key(other.id)

    row = (await session.execute(select(JournalEntry))).scalars().one()
    # Encrypt the *same* text under the other user's key: AAD (user_id, "body")
    # no longer matches, so decryption fails exactly as it would with a rotated key.
    row.body_encrypted = encrypt_field(
        JOURNAL_BODY, user_id=other.id, field_name="body", wrapped_dek=wrapped_other
    )
    await session.commit()

    response = await client.get(f"{EXPORT}?format=json", headers=person.headers)
    assert response.status_code == 200
    document = response.json()
    entry = document["files"]["journal.json"][0]
    assert entry["body"] is None
    assert entry["decryption_failed"] is True
    assert entry["title"] == JOURNAL_TITLE  # the readable half is still exported
    assert document["unreadable"]["journal_entries"] == 1
    assert document["counts"]["journal_entries"] == 1


async def test_export_is_empty_for_a_new_account(client: httpx.AsyncClient) -> None:
    person = await guest(client)
    response = await client.get(f"{EXPORT}?format=json", headers=person.headers)
    assert response.status_code == 200
    document = response.json()
    assert document["counts"]["journal_entries"] == 0
    assert document["files"]["chat.json"] == []
    assert document["files"]["privacy-settings.json"]["been_saved"] is False
    assert document["files"]["user-settings.json"]["mood_outlook_enabled"] is False
    assert document["files"]["user-settings.json"]["been_saved"] is False


async def test_export_truncates_with_a_stated_ceiling(
    client: httpx.AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
    """The per-table ceiling is reported, never silently applied."""
    person = await guest(client)
    repo = MoodRepository(session)
    for index in range(4):
        await repo.record(
            user_id=person.id, valence=3, energy=3, emotions=[], factors={}, note=f"note {index}"
        )
    await session.commit()

    app.state.settings.privacy_export_max_rows = 2
    response = await client.get(f"{EXPORT}?format=json", headers=person.headers)
    assert response.status_code == 200
    document = response.json()
    assert len(document["files"]["mood.json"]) == 2
    assert document["truncated"]["mood_entries"] == 2
    assert document["counts"]["mood_entries"] == 2


async def test_export_does_not_leak_into_the_logs(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Log lines carry counts; the export's contents never reach a logger."""
    import io

    import structlog

    from app.core.logging import configure_logging

    person = await guest(client)
    await _fill_every_feature(session, client, person)

    buffer = io.StringIO()
    configure_logging()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(buffer))
    try:
        response = await client.get(EXPORT, headers=person.headers)
        assert response.status_code == 200
    finally:
        configure_logging()

    output = buffer.getvalue()
    assert "privacy.export" in output
    assert JOURNAL_BODY not in output
    assert CHAT_USER not in output
    assert MOOD_NOTE not in output
    assert CBT_THOUGHT not in output


async def test_a_decrypted_field_is_really_encrypted_at_rest_first(
    session: AsyncSession,
) -> None:
    """Sanity guard for the export tests: the fixture text is ciphertext in the row."""
    user = await UserRepository(session).create(is_anonymous=True)
    await session.commit()
    await session.refresh(user)
    entry = await JournalRepository(session).create(
        user_id=user.id, title=JOURNAL_TITLE, body=JOURNAL_BODY
    )
    await session.commit()
    assert JOURNAL_BODY.encode("utf-8") not in entry.body_encrypted
    assert (
        decrypt_field(
            entry.body_encrypted,
            user_id=user.id,
            field_name="body",
            wrapped_dek=user.data_key_encrypted,
        )
        == JOURNAL_BODY
    )


async def test_a_thought_record_saved_through_the_api_exports_decrypted(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The regression test for a real bug: export and write path disagreed on AAD.

    Every thought record exported as ``null`` with ``decryption_failed``, because
    the write path binds AES-GCM Associated Data ``cbt.<field>`` while the export
    read it back under the bare field name. The tests that existed did not catch
    it: their fixture built ``CBTRecord`` rows itself, using the export's
    spelling, so the fixture and the export agreed with each other and disagreed
    with the code that actually writes a user's row.

    So this test does not build a row. It posts the record the way a person
    does — ``POST /cbt/records``, through the API, with the real screening and
    the real encryption — and then exports it and reads the words back. A test
    that shares the implementation's blind spot cannot find it; one that goes
    through the endpoint the UI uses can.
    """
    person = await guest(client, kinds=("terms", "privacy"))
    response = await client.post(
        "/api/v1/cbt/records",
        json={
            "situation": "SENTINEL-API-CBT-SITUATION",
            "automatic_thought": "SENTINEL-API-CBT-THOUGHT",
            "evidence_for": "The fixture agreed with the bug.",
            "evidence_against": "This test goes through the endpoint.",
            "balanced_thought": "One mismatched string, one empty export field.",
            "emotion": "anxiety",
            "intensity_before": 70,
            "intensity_after": 40,
            "what_helped": ["looking at evidence"],
        },
        headers=person.headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["record"] is not None

    export = await client.get(f"{PRIVACY}/export", headers=person.headers)
    assert export.status_code == 200, export.text
    files = _zip_entries(export.content)

    records = files["cbt.json"]
    assert len(records) == 1
    assert records[0]["situation"] == "SENTINEL-API-CBT-SITUATION"
    assert records[0]["automatic_thought"] == "SENTINEL-API-CBT-THOUGHT"
    assert "decryption_failed" not in records[0], (
        "a thought record written seconds ago must decrypt in its owner's export"
    )
    manifest = files["manifest.json"]
    assert manifest["unreadable_rows"]["cbt_records"] == 0


async def test_a_share_is_exported_as_decisions_not_as_its_document(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The archive tells the owner what they shared; it cannot leak the document.

    A share row holds ciphertext, a token hash and a passcode hash. The export
    carries the *decisions* — which items, which formats, when it expired, how
    many times it was opened — and must carry none of the cryptographic
    material, because the archive is a plain-text file that gets emailed to
    people and left on desktops.
    """

    from datetime import UTC, datetime, timedelta

    from app.models.enums import ShareCipher, ShareEventKind
    from app.models.share import ClinicianShare, ShareAccessEvent
    from app.services.sharing import crypto

    person = await guest(client, kinds=("terms", "privacy"))
    plaintext = b'{"note": "Export sentinel: the document itself must not appear."}'
    # A real token, generated rather than typed: the point of the test is that a
    # token-shaped string cannot leak into the archive, so it should have the
    # shape the product actually produces.
    token = crypto.new_token()
    share = ClinicianShare(
        id=uuid.uuid4(),
        user_id=person.id,
        token_hash="c" * 64,
        token_prefix=token[:8],
        passcode_hash=b"d" * 32,
        passcode_salt=b"e" * 16,
        expires_at=datetime.now(UTC) + timedelta(days=7),
        view_limit=5,
        view_count=2,
        formats=["pdf", "fhir"],
        items=["mood_trend", "note"],
        payload_ciphertext=plaintext,
        payload_nonce=b"f" * 12,
        pdf_ciphertext=plaintext,
        pdf_nonce=b"f" * 12,
        cipher=ShareCipher.A256GCM,
        consent_summary_sha256="a" * 64,
    )
    session.add(share)
    await session.flush()
    for kind in (ShareEventKind.CREATED, ShareEventKind.OPENED, ShareEventKind.OPENED):
        session.add(ShareAccessEvent(share_id=share.id, kind=kind, actor="viewer"))
    await session.commit()

    response = await client.get(EXPORT, headers=person.headers)
    assert response.status_code == 200
    files = _zip_entries(response.content)
    assert "clinician-shares.json" in files

    shares = files["clinician-shares.json"]
    assert isinstance(shares, list)
    assert len(shares) == 1
    row = shares[0]
    assert row["id"] == str(share.id)
    assert row["status"] == "active"
    assert row["formats"] == ["pdf", "fhir"]
    assert row["items"] == ["mood_trend", "note"]
    assert row["view_count"] == 2
    assert row["view_limit"] == 5
    assert row["token_prefix"] == token[:8]
    assert [event["kind"] for event in row["events"]] == ["created", "opened", "opened"]
    assert all(set(event) == {"kind", "actor", "created_at"} for event in row["events"])
    assert "ciphertext" in row["note"], "the file says why the document is not there"

    # Nothing cryptographic leaves the database, anywhere in the archive.
    archive_text = "\n".join(
        value if isinstance(value, str) else json.dumps(value) for value in files.values()
    )
    for forbidden in (
        "Export sentinel: the document itself",
        plaintext.decode("utf-8"),
        token,
        "c" * 64,
        "payload_ciphertext",
        "passcode_hash",
        "passcode_salt",
        "secret",
    ):
        assert forbidden not in archive_text, forbidden
    # The manifest counts the rows it exported, so an owner can see the number.
    assert files["manifest.json"]["counts"]["clinician_shares"] == 1
    assert files["manifest.json"]["counts"]["share_access_events"] == 3
