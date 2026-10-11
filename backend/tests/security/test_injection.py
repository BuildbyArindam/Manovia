"""SQL injection and XSS-in-transit: the payload must be data, never code.

Two halves:

* **SQL** — classic payloads are pushed through every query parameter and every
  JSON text field, then the schema and the row counts are inspected. An ORM with
  bound parameters makes this boring; boring is the point, and a regression here
  would be catastrophic, so it is tested rather than assumed.
* **XSS** — the API's job is to hand back exactly what it was given, encoded as
  JSON, with no HTML context and no ``text/html`` content type anywhere. Whether
  the *browser* renders it as text is a frontend concern and is proven
  separately in the Playwright suite (``frontend/e2e/xss.spec.ts``).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession
from app.models.message import Message
from app.models.user import User

CONSENT_VERSION = "2026-10-01"

#: Payloads chosen to break each of the three shapes an injection can take:
#: a terminated statement, a boolean that would widen a WHERE clause, and a
#: UNION that would splice another table into a result.
SQL_PAYLOADS = [
    "'; DROP TABLE users;--",
    "' OR '1'='1",
    "1' UNION SELECT password_hash FROM users--",
    "'; UPDATE users SET email='pwned@example.test';--",
    "\\'; DROP TABLE journal_entries; --",
    "%27%20OR%201=1--",
]

XSS_PAYLOADS = [
    "<img src=x onerror=alert(1)>",
    "<script>alert('xss')</script>",
    '"><svg/onload=alert(1)>',
    "javascript:alert(1)",
    "<iframe src=javascript:alert(1)>",
]

ALL_PAYLOADS = [*SQL_PAYLOADS, *XSS_PAYLOADS]


async def _signed_in(
    client: httpx.AsyncClient, kinds: tuple[str, ...] = ("terms", "privacy")
) -> dict[str, str]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    if kinds:
        granted = await client.post(
            "/api/v1/consent",
            headers=headers,
            json={
                "grants": [
                    {"kind": kind, "version": CONSENT_VERSION, "granted": True} for kind in kinds
                ]
            },
        )
        assert granted.status_code == 201
    return headers


async def _table_names(session: AsyncSession) -> set[str]:
    """The live schema, read through the async engine (never guessed)."""

    def read(sync_connection):  # type: ignore[no-untyped-def]
        return inspect(sync_connection).get_table_names()

    connection = await session.connection()
    return set(await connection.run_sync(read))


async def _user_count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(User))).scalar_one())


# --------------------------------------------------------------------------- #
# Query parameters                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("payload", ALL_PAYLOADS)
async def test_payloads_in_query_parameters_are_refused_or_ignored(
    client: httpx.AsyncClient, session: AsyncSession, payload: str
) -> None:
    """No 5xx, no schema change, no leaked row — the request is simply wrong."""
    headers = await _signed_in(client)
    before = await _table_names(session)

    requests = [
        ("GET", "/api/v1/mood", {"from": payload, "to": payload}),
        ("GET", "/api/v1/journal", {"template": payload, "since": payload}),
        ("GET", "/api/v1/exercises/recommendations", {"emotion": payload}),
        ("GET", "/api/v1/chat/sessions", {"limit": payload}),
        ("GET", "/api/v1/insights/mood/trends", {"tz": payload}),
    ]
    for method, path, params in requests:
        response = await client.request(method, path, headers=headers, params=params)
        assert response.status_code < 500, (
            f"{path} {params} → {response.status_code}: {response.text}"
        )
        # A curated failure never quotes what was sent.
        assert payload not in response.text or response.status_code < 400

    assert await _table_names(session) == before
    assert await _user_count(session) == 1


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
async def test_a_payload_in_a_login_email_is_just_a_failed_login(
    client: httpx.AsyncClient, payload: str
) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": payload, "password": "whatever"}
    )
    assert response.status_code in {401, 422}, response.text
    assert "users" not in response.text
    assert "password_hash" not in response.text


@pytest.mark.parametrize("payload", SQL_PAYLOADS)
async def test_a_payload_in_a_bearer_token_is_just_a_rejected_token(
    client: httpx.AsyncClient, payload: str
) -> None:
    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {payload}"})
    assert response.status_code == 401
    assert "Traceback" not in response.text


# --------------------------------------------------------------------------- #
# JSON bodies: stored verbatim, returned verbatim, executed never               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("payload", ALL_PAYLOADS)
async def test_a_payload_in_a_journal_entry_survives_as_text(
    client: httpx.AsyncClient, session: AsyncSession, payload: str
) -> None:
    headers = await _signed_in(client)
    created = await client.post(
        "/api/v1/journal",
        headers=headers,
        json={"title": payload, "body": f"before {payload} after"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["title"] == payload

    listed = await client.get("/api/v1/journal", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["title"] == payload

    # The test database is built from the models, so there is no alembic_version
    # table here — every *product* table must be present, and nothing else.
    assert await _table_names(session) == {
        "assessment_results",
        "cbt_records",
        "chat_sessions",
        "consents",
        "deletion_receipts",
        "exercise_completions",
        "journal_entries",
        "messages",
        "mood_entries",
        "plan_items",
        "privacy_settings",
        "refresh_tokens",
        "safety_events",
        "user_settings",
        "users",
    }


@pytest.mark.parametrize("payload", ALL_PAYLOADS)
async def test_a_payload_in_a_mood_note_survives_as_text(
    client: httpx.AsyncClient, payload: str
) -> None:
    headers = await _signed_in(client)
    created = await client.post(
        "/api/v1/mood",
        headers=headers,
        json={"valence": 3, "energy": 3, "emotions": [], "factors": [], "note": payload},
    )
    assert created.status_code == 201, created.text
    assert created.json()["entry"]["note"] == payload

    listed = await client.get("/api/v1/mood", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["entries"][0]["note"] == payload


@pytest.mark.parametrize("payload", ALL_PAYLOADS)
async def test_a_payload_in_a_chat_message_is_answered_not_executed(
    client: httpx.AsyncClient, app: FastAPI, payload: str
) -> None:
    """The message reaches the pipeline unchanged and comes back as JSON text."""
    from app.services.chat import build_orchestrator
    from app.services.llm.fake_provider import FakeLLMProvider

    app.state.chat_orchestrator = build_orchestrator(
        app.state.settings, llm=FakeLLMProvider().script("Understood.")
    )
    headers = await _signed_in(client, ("terms", "privacy", "ai_disclosure", "store_chat"))

    opened = await client.post(
        "/api/v1/chat/sessions", headers=headers, json={"save_history": True}
    )
    assert opened.status_code == 201, opened.text
    session_id = opened.json()["id"]
    sent = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=headers,
        json={"message": payload},
    )
    assert sent.status_code in {200, 201}, sent.text
    assert sent.headers["content-type"].startswith("application/json")

    # Stored (encrypted) and handed back to its owner exactly as typed: the
    # payload is data all the way in, through the ORM, and all the way out.
    stored = await client.get(f"/api/v1/chat/sessions/{session_id}/messages", headers=headers)
    assert stored.status_code == 200, stored.text
    user_turns = [m for m in stored.json()["messages"] if m["role"] == "user"]
    assert [m["text"] for m in user_turns] == [payload]


@pytest.mark.parametrize("payload", ALL_PAYLOADS)
async def test_a_payload_in_cbt_fields_survives_as_text(
    client: httpx.AsyncClient, payload: str
) -> None:
    headers = await _signed_in(client, ("terms", "privacy", "ai_disclosure"))
    created = await client.post(
        "/api/v1/cbt/records",
        headers=headers,
        json={
            "situation": payload,
            "automatic_thought": payload,
            "evidence_for": payload,
            "evidence_against": payload,
            "balanced_thought": payload,
            "emotion": "anxiety",
            "intensity_before": 60,
            "intensity_after": 30,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["record"]["situation"] == payload

    listed = await client.get("/api/v1/cbt/records", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["situation"] == payload


# --------------------------------------------------------------------------- #
# Content types and encoding                                                    #
# --------------------------------------------------------------------------- #


async def test_nothing_is_ever_served_as_html(client: httpx.AsyncClient) -> None:
    headers = await _signed_in(client)
    for path in (
        "/api/v1/health",
        "/api/v1/auth/me",
        "/api/v1/mood",
        "/api/v1/journal",
        "/api/v1/privacy/settings",
        "/api/v1/does-not-exist",
    ):
        response = await client.get(path, headers=headers)
        assert "text/html" not in response.headers.get("content-type", ""), path


async def test_a_payload_in_a_body_round_trips_byte_for_byte(
    client: httpx.AsyncClient,
) -> None:
    """Unicode, quotes and angle brackets are preserved: no encoding surprises."""
    headers = await _signed_in(client)
    tricky = "<b>émoji 🙂</b> \"quoted\" & 'apostrophe' \\backslash\\ \u200bzero-width"
    created = await client.post("/api/v1/journal", headers=headers, json={"body": tricky})
    assert created.status_code == 201
    assert created.json()["body"] == tricky

    fetched = await client.get(f"/api/v1/journal/{created.json()['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["body"] == tricky


async def test_a_payload_cannot_escape_a_path_parameter(
    client: httpx.AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
    """``../`` and ``;`` in a UUID parameter are a 422, not a traversal."""
    headers = await _signed_in(client)
    for hostile in ("../../etc/passwd", "1;DROP TABLE users", "' OR 1=1--"):
        response = await client.get(f"/api/v1/journal/{hostile}", headers=headers)
        assert response.status_code in {404, 422}, response.text
        assert "root:" not in response.text
    assert await _user_count(session) == 1


async def test_a_payload_in_a_foreign_key_is_refused_not_coerced(
    client: httpx.AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
    """A chat session id from another person stays other: 404, and no row is made."""
    from app.services.chat import build_orchestrator
    from app.services.llm.fake_provider import FakeLLMProvider

    app.state.chat_orchestrator = build_orchestrator(
        app.state.settings, llm=FakeLLMProvider().script("hi")
    )
    kinds = ("terms", "privacy", "ai_disclosure", "store_chat")
    mine = await _signed_in(client, kinds)
    theirs = await _signed_in(client, kinds)

    opened = await client.post("/api/v1/chat/sessions", headers=theirs, json={"save_history": True})
    assert opened.status_code == 201
    session_id = opened.json()["id"]

    stolen = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages", headers=mine, json={"message": "hello"}
    )
    assert stolen.status_code == 404, stolen.text

    count = int((await session.execute(select(func.count()).select_from(Message))).scalar_one())
    assert count == 0
    sessions = int(
        (await session.execute(select(func.count()).select_from(ChatSession))).scalar_one()
    )
    assert sessions == 1
