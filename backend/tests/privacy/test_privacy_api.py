"""Privacy settings, consent history, and the two controls that actually bite.

The point of these tests is that a settings screen which *looks* like it turns
something off must actually turn it off. Two of the three columns in
``privacy_settings`` change behaviour elsewhere in the product, and both are
asserted here at the API level:

* ``store_chat=false`` → ``POST /chat/sessions {"save_history": true}`` is
  refused, even when the ``store_chat`` agreement is on file.
* ``store_sentiment=false`` → a journal write that *asks* for scoring stores
  none, and the response says so.
"""

from __future__ import annotations

import uuid

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal import JournalEntry
from app.models.privacy import PrivacySetting
from tests.privacy.conftest import PRIVACY, account, guest

SETTINGS = f"{PRIVACY}/settings"
CONSENTS = f"{PRIVACY}/consents"


async def test_settings_defaults_are_the_product_defaults(client: httpx.AsyncClient) -> None:
    """No saved row means defaults, and the response says they were not saved."""
    person = await guest(client)
    response = await client.get(SETTINGS, headers=person.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["store_chat"] is True
    assert body["store_sentiment"] is True
    assert body["retention_days"] is None
    assert body["been_saved"] is False
    assert body["retention_choices"] == [30, 90, 180, 365]
    assert body["retention_min_days"] == 7
    assert body["retention_max_days"] == 3650


async def test_patch_is_partial_and_creates_the_row_once(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await guest(client)
    first = await client.patch(SETTINGS, headers=person.headers, json={"retention_days": 90})
    assert first.status_code == 200
    assert first.json()["retention_days"] == 90
    # store_chat was not mentioned, so it keeps its default.
    assert first.json()["store_chat"] is True

    second = await client.patch(SETTINGS, headers=person.headers, json={"store_chat": False})
    assert second.status_code == 200
    assert second.json()["store_chat"] is False
    assert second.json()["retention_days"] == 90
    assert second.json()["been_saved"] is True

    rows = (
        (await session.execute(select(PrivacySetting).where(PrivacySetting.user_id == person.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].retention_days == 90


async def test_retention_can_be_cleared_back_to_keep_forever(client: httpx.AsyncClient) -> None:
    person = await guest(client)
    await client.patch(SETTINGS, headers=person.headers, json={"retention_days": 30})
    cleared = await client.patch(SETTINGS, headers=person.headers, json={"retention_days": None})
    assert cleared.status_code == 200
    assert cleared.json()["retention_days"] is None


async def test_out_of_range_retention_is_rejected_not_clamped(
    client: httpx.AsyncClient,
) -> None:
    person = await guest(client)
    for bad in (0, 6, 3651):
        response = await client.patch(
            SETTINGS, headers=person.headers, json={"retention_days": bad}
        )
        assert response.status_code == 422, bad
    # Nothing was stored by the rejected writes.
    after = await client.get(SETTINGS, headers=person.headers)
    assert after.json()["retention_days"] is None


async def test_an_empty_patch_is_an_error_not_a_silent_noop(client: httpx.AsyncClient) -> None:
    person = await guest(client)
    response = await client.patch(SETTINGS, headers=person.headers, json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "empty_update"


async def test_privacy_routes_need_authentication(client: httpx.AsyncClient) -> None:
    for method, path in (("get", SETTINGS), ("get", CONSENTS), ("get", f"{PRIVACY}/export")):
        response = await getattr(client, method)(path)
        assert response.status_code == 401, path


async def test_privacy_routes_work_without_any_consent(client: httpx.AsyncClient) -> None:
    """A right is not a feature: no consent gate on export, delete or settings.

    Someone who withdrew consent must still be able to read their own data and
    delete it, so these four routes are authenticated but not consent-gated.
    """
    person = await guest(client, kinds=())
    assert (await client.get(SETTINGS, headers=person.headers)).status_code == 200
    assert (await client.get(CONSENTS, headers=person.headers)).status_code == 200
    export = await client.get(f"{PRIVACY}/export", headers=person.headers)
    assert export.status_code == 200


async def test_consent_history_is_newest_first_and_marks_withdrawals(
    client: httpx.AsyncClient,
) -> None:
    person = await guest(client, kinds=("terms", "privacy"))
    withdrawn = await client.post(
        "/api/v1/consent",
        headers=person.headers,
        json={"grants": [{"kind": "privacy", "version": "2026-10-01", "granted": False}]},
    )
    assert withdrawn.status_code == 201

    response = await client.get(CONSENTS, headers=person.headers)
    assert response.status_code == 200
    body = response.json()
    kinds = [(item["kind"], item["granted"]) for item in body["items"]]
    assert ("privacy", False) in kinds and ("privacy", True) in kinds
    assert ("terms", True) in kinds
    # Newest first: the withdrawal is the first row.
    assert kinds[0] == ("privacy", False)
    assert body["current_versions"]["terms"] == "2026-10-01"
    assert all(item["current_version"] for item in body["items"])


async def test_store_chat_off_vetoes_saving_even_with_the_consent(
    client: httpx.AsyncClient,
) -> None:
    person = await guest(client, kinds=("terms", "privacy", "ai_disclosure", "store_chat"))
    saved = await client.post(
        "/api/v1/chat/sessions", headers=person.headers, json={"save_history": True}
    )
    assert saved.status_code == 201 and saved.json()["persistent"] is True

    await client.patch(SETTINGS, headers=person.headers, json={"store_chat": False})
    blocked = await client.post(
        "/api/v1/chat/sessions", headers=person.headers, json={"save_history": True}
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "chat_storage_disabled"

    # An ephemeral session is still allowed — the veto stops *saving*, not chat.
    ephemeral = await client.post("/api/v1/chat/sessions", headers=person.headers)
    assert ephemeral.status_code == 201 and ephemeral.json()["persistent"] is False


async def test_turning_saving_off_stops_new_turns_in_an_existing_saved_chat(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """The veto applies to the *next* message, not only to new sessions."""
    from app.services.chat import build_orchestrator
    from app.services.llm.fake_provider import FakeLLMProvider

    app.state.chat_orchestrator = build_orchestrator(app.state.settings, llm=FakeLLMProvider())
    person = await guest(client, kinds=("terms", "privacy", "ai_disclosure", "store_chat"))
    created = await client.post(
        "/api/v1/chat/sessions", headers=person.headers, json={"save_history": True}
    )
    session_id = created.json()["id"]

    await client.patch(SETTINGS, headers=person.headers, json={"store_chat": False})
    sent = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=person.headers,
        json={"message": "I finished the assignment and slept well."},
    )
    assert sent.status_code == 200

    history = await client.get(
        f"/api/v1/chat/sessions/{session_id}/messages", headers=person.headers
    )
    assert history.status_code == 200
    # Nothing new was written; the session still exists but gained no turns.
    assert history.json()["messages"] == []


async def test_store_sentiment_off_forces_no_scores_on_a_new_entry(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    person = await guest(client)
    await client.patch(SETTINGS, headers=person.headers, json={"store_sentiment": False})

    response = await client.post(
        "/api/v1/journal",
        headers=person.headers,
        json={
            "title": "Scores off",
            "body": "I felt calm and steady all afternoon, and the work went well.",
            "store_emotions": True,  # the client asks; the account-level floor says no
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["store_emotions"] is False
    assert body["sentiment"] is None
    assert body["emotion_scores"] is None

    row = (
        (
            await session.execute(
                select(JournalEntry).where(JournalEntry.user_id == uuid.UUID(str(person.id)))
            )
        )
        .scalars()
        .one()
    )
    assert row.sentiment is None and row.emotion_scores is None


async def test_store_sentiment_on_is_still_the_default_and_stores_scores(
    client: httpx.AsyncClient,
) -> None:
    person = await guest(client)
    response = await client.post(
        "/api/v1/journal",
        headers=person.headers,
        json={
            "title": "Scores on",
            "body": "I felt calm and steady all afternoon, and the work went well.",
            "store_emotions": True,
        },
    )
    assert response.status_code == 201
    assert response.json()["store_emotions"] is True
    assert response.json()["sentiment"] is not None


async def test_a_password_account_can_read_and_change_its_own_settings(
    client: httpx.AsyncClient,
) -> None:
    person = await account(client)
    patched = await client.patch(SETTINGS, headers=person.headers, json={"retention_days": 30})
    assert patched.status_code == 200
    assert patched.json()["retention_days"] == 30
