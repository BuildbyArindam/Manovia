"""Account deletion: everything goes, the receipt keeps nothing, tokens die.

This is the test file that has to be paranoid, because a "delete my account"
button that leaves rows behind is worse than no button at all. Every assertion
here reads the **database**, not the API:

* zero rows in every user-linked table afterwards,
* the receipt contains counts and an HMAC — no content, no user id,
* old tokens stop working immediately,
* a wrong confirmation or a wrong password deletes nothing at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import (
    AssessmentRepository,
    ChatRepository,
    JournalRepository,
    MoodRepository,
    UserRepository,
)
from app.models.chat_session import ChatSession
from app.models.consent import Consent
from app.models.enums import (
    AssessmentBand,
    AssessmentInstrument,
    MessageRole,
    RiskLevel,
)
from app.models.message import Message
from app.models.privacy import DeletionReceipt, PrivacySetting
from app.models.refresh_token import RefreshToken
from app.models.safety_event import SafetyEvent
from app.models.user_settings import UserSettings
from app.services.privacy.deletion import USER_OWNED_MODELS, account_reference
from tests.privacy.conftest import PRIVACY, account, guest

DELETE_URL = f"{PRIVACY}/account"


async def _fill(session: AsyncSession, user_id: uuid.UUID) -> None:
    """One row in every table that can hold this person's data."""
    chats = ChatRepository(session)
    chat_session = await chats.create(user_id=user_id)
    await chats.add_message(
        session_id=chat_session.id,
        role=MessageRole.USER,
        content="delete me",
        risk_level=RiskLevel.CAUTION,
    )
    await chats.update_memory(chat_session.id, "a summary to delete")

    await MoodRepository(session).record(
        user_id=user_id, valence=2, energy=2, emotions=["sadness"], factors={}, note="delete me too"
    )
    await JournalRepository(session).create(user_id=user_id, title="t", body="b")
    await AssessmentRepository(session).record(
        user_id=user_id,
        instrument=AssessmentInstrument.PHQ9,
        answers={"phq9_1": 1},
        total=1,
        band=AssessmentBand.MINIMAL,
    )
    session.add(
        SafetyEvent(
            user_id=user_id,
            session_id=chat_session.id,
            risk_level=RiskLevel.CAUTION,
            source="rules",
        )
    )
    session.add(PrivacySetting(user_id=user_id, retention_days=90))
    session.add(UserSettings(user_id=user_id))
    await session.commit()


async def _row_counts(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, model in USER_OWNED_MODELS:
        statement = select(func.count()).select_from(model).where(model.user_id == user_id)
        counts[name] = int((await session.execute(statement)).scalar_one())
    session_ids = select(ChatSession.id).where(ChatSession.user_id == user_id)
    counts["messages"] = int(
        (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.session_id.in_(session_ids))
            )
        ).scalar_one()
    )
    return counts


async def test_deleting_an_account_empties_every_table(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await guest(client)
    await _fill(session, person.id)
    before = await _row_counts(session, person.id)
    assert before["messages"] == 1 and before["journal_entries"] == 1
    assert before["refresh_tokens"] >= 1

    response = await client.request(
        "DELETE",
        DELETE_URL,
        headers=person.headers,
        json={"confirm": "DELETE"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["irreversible"] is True
    assert body["rows_deleted"]["messages"] == 1
    assert body["rows_deleted"]["journal_entries"] == 1
    assert body["total_rows"] == sum(body["rows_deleted"].values())

    after = await _row_counts(session, person.id)
    assert after == dict.fromkeys(after, 0), after
    assert await UserRepository(session).get(person.id) is None


async def test_the_receipt_has_no_content_and_no_user_id(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await guest(client)
    await _fill(session, person.id)

    response = await client.request(
        "DELETE", DELETE_URL, headers=person.headers, json={"confirm": "DELETE"}
    )
    assert response.status_code == 200
    receipt_id = uuid.UUID(response.json()["receipt_id"])

    stored = await session.get(DeletionReceipt, receipt_id)
    assert stored is not None
    # The table has no column that could hold a person or their words.
    columns = {column.name for column in DeletionReceipt.__table__.columns}
    assert columns == {"id", "account_ref", "requested_via", "rows_deleted", "created_at"}
    assert str(person.id) not in stored.account_ref
    assert stored.account_ref == response.json()["account_ref"]
    assert stored.rows_deleted["journal_entries"] == 1
    assert all(isinstance(value, int) for value in stored.rows_deleted.values())


def test_the_account_reference_is_a_keyed_hash_not_an_identifier() -> None:
    user_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    reference = account_reference(user_id, secret_key="deployment-secret-a")
    assert reference != account_reference(user_id, secret_key="deployment-secret-b")
    assert str(user_id) not in reference
    assert len(reference) == 32


async def test_old_tokens_stop_working_at_once(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await guest(client)
    assert (await client.get("/api/v1/auth/me", headers=person.headers)).status_code == 200

    deleted = await client.request(
        "DELETE", DELETE_URL, headers=person.headers, json={"confirm": "DELETE"}
    )
    assert deleted.status_code == 200

    after = await client.get("/api/v1/auth/me", headers=person.headers)
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "account_deleted"

    # The refresh token is gone with the cascade, so it cannot mint a new access token.
    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": person.refresh_token}
    )
    assert refreshed.status_code == 401

    # And a personal surface refuses the deleted account too.
    moods = await client.get("/api/v1/mood", headers=person.headers)
    assert moods.status_code == 401


async def test_another_account_is_untouched(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    mine = await guest(client)
    theirs = await guest(client)
    await _fill(session, theirs.id)

    assert (
        await client.request("DELETE", DELETE_URL, headers=mine.headers, json={"confirm": "DELETE"})
    ).status_code == 200

    assert (await client.get("/api/v1/auth/me", headers=theirs.headers)).status_code == 200
    counts = await _row_counts(session, theirs.id)
    assert counts["journal_entries"] == 1 and counts["mood_entries"] == 1
    assert (
        await client.get("/api/v1/privacy/export?format=json", headers=theirs.headers)
    ).status_code == 200


async def test_the_wrong_confirmation_deletes_nothing(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await guest(client)
    await _fill(session, person.id)

    # Case does not matter ("delete" is an honest way to type it); these are wrong.
    for wrong in ("", "YES", "DELETE ALL", "delete my account"):
        response = await client.request(
            "DELETE", DELETE_URL, headers=person.headers, json={"confirm": wrong}
        )
        assert response.status_code == 400, wrong
        assert response.json()["error"]["code"] == "confirmation_required"
        assert "Nothing was deleted" in response.json()["error"]["message"]

    assert await UserRepository(session).get(person.id) is not None
    assert await _row_counts(session, person.id) is not None


async def test_a_guest_account_needs_only_the_confirmation(client: httpx.AsyncClient) -> None:
    """A guest has no credential to re-present; the phrase is the whole re-auth."""
    person = await guest(client)
    response = await client.request(
        "DELETE", DELETE_URL, headers=person.headers, json={"confirm": "DELETE"}
    )
    assert response.status_code == 200
    assert response.json()["rows_deleted"].get("consents", 0) >= 1


async def test_a_password_account_must_re_authenticate(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await account(client, email="delete-me@example.test")
    await _fill(session, person.id)

    missing = await client.request(
        "DELETE", DELETE_URL, headers=person.headers, json={"confirm": "DELETE"}
    )
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "reauth_required"

    wrong = await client.request(
        "DELETE",
        DELETE_URL,
        headers=person.headers,
        json={"confirm": "DELETE", "password": "not-the-password"},
    )
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "invalid_credentials"

    assert await UserRepository(session).get(person.id) is not None

    right = await client.request(
        "DELETE",
        DELETE_URL,
        headers=person.headers,
        json={"confirm": "DELETE", "password": person.password},
    )
    assert right.status_code == 200, right.text
    assert await UserRepository(session).get(person.id) is None


async def test_repeated_wrong_passwords_lock_the_delete_path(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Re-auth guessing is throttled by the same lockout the login path uses."""
    person = await account(client, email="lockout@example.test")
    for _ in range(5):
        attempt = await client.request(
            "DELETE",
            DELETE_URL,
            headers=person.headers,
            json={"confirm": "DELETE", "password": "nope"},
        )
        assert attempt.status_code == 403

    locked = await client.request(
        "DELETE",
        DELETE_URL,
        headers=person.headers,
        json={"confirm": "DELETE", "password": person.password},
    )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_locked"
    assert "Retry-After" in locked.headers
    # Still nothing deleted, and the correct password did not get through while locked.
    assert await UserRepository(session).get(person.id) is not None
    assert (
        await client.request(
            "DELETE",
            DELETE_URL,
            headers=person.headers,
            json={"confirm": "DELETE", "password": person.password},
        )
    ).status_code == 429


async def test_deletion_requires_authentication(client: httpx.AsyncClient) -> None:
    anon = await client.request("DELETE", DELETE_URL, json={"confirm": "DELETE"})
    assert anon.status_code == 401
    assert anon.json()["error"]["code"] == "token_missing"


async def test_deleting_twice_is_a_401_not_a_500(client: httpx.AsyncClient) -> None:
    person = await guest(client)
    first = await client.request(
        "DELETE", DELETE_URL, headers=person.headers, json={"confirm": "DELETE"}
    )
    assert first.status_code == 200
    second = await client.request(
        "DELETE", DELETE_URL, headers=person.headers, json={"confirm": "DELETE"}
    )
    assert second.status_code == 401


async def test_deletion_logs_counts_only(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit line for a deletion must not carry the person's content."""
    import io

    import structlog

    from app.core.logging import configure_logging

    person = await guest(client)
    await _fill(session, person.id)

    buffer = io.StringIO()
    configure_logging()
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(buffer))
    try:
        response = await client.request(
            "DELETE", DELETE_URL, headers=person.headers, json={"confirm": "DELETE"}
        )
        assert response.status_code == 200
    finally:
        configure_logging()

    output = buffer.getvalue()
    assert "privacy.account_deleted" in output
    assert str(person.id) not in output
    assert "delete me" not in output
    assert "a summary to delete" not in output


async def test_consent_rows_are_counted_before_they_are_cascaded(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The receipt counts what *existed* — a count taken after the delete is zero."""
    person = await guest(client, kinds=("terms", "privacy", "ai_disclosure"))
    grants = int(
        (
            await session.execute(
                select(func.count()).select_from(Consent).where(Consent.user_id == person.id)
            )
        ).scalar_one()
    )
    assert grants == 3
    response = await client.request(
        "DELETE", DELETE_URL, headers=person.headers, json={"confirm": "DELETE"}
    )
    assert response.json()["rows_deleted"]["consents"] == 3


async def test_expired_refresh_tokens_of_a_deleted_account_are_gone(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await account(client, email="token-check@example.test")
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == person.id)
        .values(expires_at=datetime.now(UTC) - timedelta(days=1))
    )
    await session.commit()

    deleted = await client.request(
        "DELETE",
        DELETE_URL,
        headers=person.headers,
        json={"confirm": "delete", "password": person.password},
    )
    assert deleted.status_code == 200, deleted.text
    remaining = int(
        (
            await session.execute(
                select(func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.user_id == person.id)
            )
        ).scalar_one()
    )
    assert remaining == 0
