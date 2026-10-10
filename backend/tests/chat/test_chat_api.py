"""The chat endpoints end to end: auth, consent, ownership, storage, SSE, outages.

Everything runs against the real FastAPI app, a real (SQLite) database and the
real field cipher, with the scripted Fake provider installed as the model. Where
a test says "no rows" it counts rows in the actual tables.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select, text

from app.core.config import Settings
from app.db.repos import ChatRepository
from app.db.session import Database
from app.main import create_app
from app.models import ChatSession, Message, SafetyEvent
from app.models.enums import RiskLevel as StoredRiskLevel
from app.services.chat import build_orchestrator
from app.services.llm.base import ProviderDown
from app.services.llm.canned import CANNED_REPLY
from app.services.llm.fake_provider import DEFAULT_REPLY, FakeLLMProvider
from app.services.nlp.keyword import KeywordFallbackAnalyzer
from tests.chat.conftest import (
    HIGH_MESSAGE,
    IMMINENT_MESSAGE,
    MEDIUM_MESSAGE,
    NEUTRAL_MESSAGE,
    PII_MESSAGE,
    SAD_MESSAGE,
)

VERSION = "2026-10-01"
SESSIONS = "/api/v1/chat/sessions"


@dataclass
class Person:
    """A signed-in guest."""

    id: str
    headers: dict[str, str]


@pytest.fixture
def llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest_asyncio.fixture(autouse=True)
async def installed_fake(app: FastAPI, settings: Settings, llm: FakeLLMProvider) -> None:
    """Put the Fake model (and a deterministic analyzer) behind the app."""
    app.state.chat_orchestrator = build_orchestrator(
        settings, llm=llm, analyzer=KeywordFallbackAnalyzer()
    )


async def person(
    client: httpx.AsyncClient,
    *,
    kinds: tuple[str, ...] = ("ai_disclosure", "terms"),
) -> Person:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    if kinds:
        granted = await client.post(
            "/api/v1/consent",
            headers=headers,
            json={"grants": [{"kind": k, "version": VERSION, "granted": True} for k in kinds]},
        )
        assert granted.status_code == 201
    return Person(id=body["user"]["id"], headers=headers)


async def open_session(
    client: httpx.AsyncClient, who: Person, *, save: bool | None = None
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"headers": who.headers}
    if save is not None:
        kwargs["json"] = {"save_history": save}
    response = await client.post(SESSIONS, **kwargs)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def say(
    client: httpx.AsyncClient, who: Person, session_id: str, message: str, **extra: Any
) -> httpx.Response:
    return await client.post(
        f"{SESSIONS}/{session_id}/messages",
        headers=who.headers,
        json={"message": message, **extra},
    )


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        assert lines[0].startswith("event: "), block
        assert lines[1].startswith("data: "), block
        events.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    return events


async def count(database: Database, model: type) -> int:
    async with database.session_factory() as db:
        return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


# --------------------------------------------------------------------------- #
# Sessions: ephemeral by default, saved only with consent                       #
# --------------------------------------------------------------------------- #


async def test_a_session_without_a_body_is_ephemeral_and_leaves_no_row(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client)
    session = await open_session(client, who)

    assert session["persistent"] is False
    expires = datetime.fromisoformat(session["expires_at"])
    created = datetime.fromisoformat(session["created_at"])
    assert timedelta(minutes=29) < expires - created <= timedelta(minutes=30, seconds=1)
    assert await count(database, ChatSession) == 0


async def test_saving_history_needs_the_store_chat_consent(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client)
    refused = await client.post(SESSIONS, headers=who.headers, json={"save_history": True})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "consent_required"
    assert "store_chat" in refused.json()["error"]["message"]
    assert await count(database, ChatSession) == 0

    saver = await person(client, kinds=("ai_disclosure", "terms", "store_chat"))
    session = await open_session(client, saver, save=True)
    assert session["persistent"] is True and session["expires_at"] is None
    assert await count(database, ChatSession) == 1


async def test_an_ephemeral_session_needs_no_store_chat_consent(
    client: httpx.AsyncClient,
) -> None:
    who = await person(client)  # no store_chat
    assert (await open_session(client, who, save=False))["persistent"] is False


async def test_chat_endpoints_need_a_signed_in_user_with_the_core_consents(
    client: httpx.AsyncClient,
) -> None:
    anonymous = await client.post(f"{SESSIONS}/{uuid.uuid4()}/messages", json={"message": "hi"})
    assert anonymous.status_code == 401

    unconsented = await person(client, kinds=())
    for path in ("messages", "stream"):
        response = await client.post(
            f"{SESSIONS}/{uuid.uuid4()}/{path}", headers=unconsented.headers, json={"message": "hi"}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "consent_required"


# --------------------------------------------------------------------------- #
# The pipeline over HTTP                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("message", "risk_level", "response_type", "llm_calls"),
    [
        (NEUTRAL_MESSAGE, "none", "normal", 1),
        (SAD_MESSAGE, "none", "normal", 1),
        (MEDIUM_MESSAGE, "medium", "check_in", 1),
        (HIGH_MESSAGE, "high", "crisis", 0),
        (IMMINENT_MESSAGE, "imminent", "crisis", 0),
    ],
)
async def test_response_type_and_risk_level_by_message(
    client: httpx.AsyncClient,
    llm: FakeLLMProvider,
    message: str,
    risk_level: str,
    response_type: str,
    llm_calls: int,
) -> None:
    who = await person(client)
    session = await open_session(client, who)

    response = await say(client, who, session["id"], message)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metadata"]["risk_level"] == risk_level
    assert body["metadata"]["response_type"] == response_type
    assert llm.call_count == llm_calls, "HIGH/IMMINENT must never reach the model"
    assert body["session_id"] == session["id"]


async def test_the_reply_shape_is_what_the_ui_needs(client: httpx.AsyncClient) -> None:
    who = await person(client)
    session = await open_session(client, who)
    body = (await say(client, who, session["id"], SAD_MESSAGE, region="IN")).json()

    assert set(body) == {"session_id", "message_id", "reply", "metadata"}
    assert body["reply"] == DEFAULT_REPLY
    meta = body["metadata"]
    assert {"risk_level", "emotion", "response_type", "resources"} <= set(meta)
    assert meta["emotion"] == "sadness"
    assert meta["region"] == "IN"


async def test_a_crisis_reply_carries_the_crisiscard_payload(
    client: httpx.AsyncClient,
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    body = (await say(client, who, session["id"], HIGH_MESSAGE, region="IN")).json()
    meta = body["metadata"]

    assert meta["response_type"] == "crisis"
    assert meta["crisis"]["template_id"] in {"crisis.high", "crisis.imminent"}
    assert meta["crisis"]["title"] and meta["crisis"]["body"]
    assert meta["emergency"]["kind"] == "emergency"
    assert len(meta["resources"]) >= 2
    assert {"id", "name", "number", "type", "hours"} <= set(meta["resources"][0])
    assert meta["emotion"] is None


async def test_the_profile_region_is_used_when_the_request_names_none(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    default = (await say(client, who, session["id"], HIGH_MESSAGE)).json()["metadata"]
    explicit = (await say(client, who, session["id"], HIGH_MESSAGE, region="US")).json()["metadata"]
    assert explicit["region"] == "US"
    assert default["region"] != "US"


async def test_a_medium_message_is_answered_and_the_reply_contains_a_check_in(
    client: httpx.AsyncClient,
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    body = (await say(client, who, session["id"], MEDIUM_MESSAGE, region="IN")).json()

    assert body["reply"].startswith(DEFAULT_REPLY)
    assert "helplines" in body["reply"]
    assert body["metadata"]["check_in"]["template_id"] == "check_in.medium"
    assert body["metadata"]["resources"]


async def test_pii_is_redacted_before_it_reaches_the_model(
    client: httpx.AsyncClient, llm: FakeLLMProvider
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    assert (await say(client, who, session["id"], PII_MESSAGE)).status_code == 200

    call = llm.last_call
    assert call is not None
    assert "jo.sharma@example.com" not in call.all_text
    assert "9876543210" not in call.all_text
    assert "[EMAIL]" in call.all_text and "[PHONE]" in call.all_text


async def test_the_conversation_window_grows_across_turns(
    client: httpx.AsyncClient, llm: FakeLLMProvider
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    await say(client, who, session["id"], "first thing I wanted to say")
    await say(client, who, session["id"], "and a second thing")

    call = llm.last_call
    assert call is not None
    assert [m.content for m in call.messages] == [
        "first thing I wanted to say",
        DEFAULT_REPLY,
        "and a second thing",
    ]


async def test_a_crisis_exchange_is_not_fed_back_to_the_model(
    client: httpx.AsyncClient, llm: FakeLLMProvider
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    await say(client, who, session["id"], HIGH_MESSAGE)
    await say(client, who, session["id"], "ok, thanks for that")

    call = llm.last_call
    assert call is not None
    assert HIGH_MESSAGE not in call.all_text
    assert call.system is not None and "crisis support" in call.system


# --------------------------------------------------------------------------- #
# Storage: encrypted when saved, nothing when ephemeral                         #
# --------------------------------------------------------------------------- #


async def test_saved_messages_are_encrypted_at_rest(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client, kinds=("ai_disclosure", "terms", "store_chat"))
    session = await open_session(client, who, save=True)
    await say(client, who, session["id"], SAD_MESSAGE)

    async with database.session_factory() as db:
        raw = (await db.execute(text("SELECT content_encrypted FROM messages"))).scalars().all()
        rows = (await db.execute(select(Message).order_by(Message.created_at))).scalars().all()
        decrypted = [ChatRepository(db).message_text(m) for m in rows]

    assert len(raw) == 2
    for blob in raw:
        assert isinstance(blob, bytes)
        assert b"sad" not in blob and b"tired" not in blob
        assert DEFAULT_REPLY.encode() not in blob
    assert decrypted == [SAD_MESSAGE, DEFAULT_REPLY]
    assert [m.role.value for m in rows] == ["user", "assistant"]
    assert rows[0].emotion == "sadness"


async def test_a_saved_crisis_turn_stores_the_message_and_a_linked_safety_event(
    client: httpx.AsyncClient, database: Database, llm: FakeLLMProvider
) -> None:
    who = await person(client, kinds=("ai_disclosure", "terms", "store_chat"))
    session = await open_session(client, who, save=True)
    body = (await say(client, who, session["id"], HIGH_MESSAGE)).json()

    assert llm.call_count == 0
    assert body["metadata"]["persisted"] is True
    async with database.session_factory() as db:
        messages = (await db.execute(select(Message).order_by(Message.created_at))).scalars().all()
        events = (await db.execute(select(SafetyEvent))).scalars().all()
        stored = [ChatRepository(db).message_text(m) for m in messages]

    assert stored[0] == HIGH_MESSAGE
    assert [int(m.risk_level) for m in messages] == [StoredRiskLevel.CRISIS] * 2
    (event,) = events
    assert str(event.user_id) == who.id
    assert str(event.session_id) == session["id"]
    assert int(event.risk_level) == StoredRiskLevel.CRISIS
    assert event.source.value == "rules"


async def test_a_none_risk_turn_writes_no_safety_event_but_medium_does(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client, kinds=("ai_disclosure", "terms", "store_chat"))
    session = await open_session(client, who, save=True)
    await say(client, who, session["id"], NEUTRAL_MESSAGE)
    assert await count(database, SafetyEvent) == 0
    await say(client, who, session["id"], MEDIUM_MESSAGE)
    assert await count(database, SafetyEvent) == 1


async def test_ephemeral_sessions_leave_no_session_or_message_rows(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client)  # no store_chat
    session = await open_session(client, who)
    for message in (NEUTRAL_MESSAGE, SAD_MESSAGE, MEDIUM_MESSAGE, HIGH_MESSAGE):
        assert (await say(client, who, session["id"], message)).status_code == 200

    assert await count(database, ChatSession) == 0
    assert await count(database, Message) == 0
    async with database.session_factory() as db:
        for table in ("chat_sessions", "messages"):
            assert (await db.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one() == 0


async def test_an_ephemeral_safety_event_is_anonymous(
    client: httpx.AsyncClient, database: Database
) -> None:
    """Audit counts survive; nothing links them to someone who chose not to save."""
    who = await person(client)
    session = await open_session(client, who)
    await say(client, who, session["id"], MEDIUM_MESSAGE)
    await say(client, who, session["id"], HIGH_MESSAGE)

    async with database.session_factory() as db:
        events = (await db.execute(select(SafetyEvent))).scalars().all()
    assert sorted(int(e.risk_level) for e in events) == [
        StoredRiskLevel.ELEVATED,
        StoredRiskLevel.CRISIS,
    ]
    assert all(e.user_id is None and e.session_id is None for e in events)


async def test_withdrawing_store_chat_stops_storing_from_the_next_message(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client, kinds=("ai_disclosure", "terms", "store_chat"))
    session = await open_session(client, who, save=True)
    first = (await say(client, who, session["id"], SAD_MESSAGE)).json()
    assert first["metadata"]["persisted"] is True
    assert await count(database, Message) == 2

    withdrawn = await client.post(
        "/api/v1/consent",
        headers=who.headers,
        json={"grants": [{"kind": "store_chat", "version": VERSION, "granted": False}]},
    )
    assert withdrawn.status_code == 201

    second = (await say(client, who, session["id"], MEDIUM_MESSAGE)).json()
    assert second["metadata"]["persisted"] is False
    assert second["reply"]
    assert await count(database, Message) == 2, "nothing new was written"
    async with database.session_factory() as db:
        events = (await db.execute(select(SafetyEvent))).scalars().all()
    assert [e.user_id for e in events] == [None], "the audit row is anonymous after withdrawal"


async def test_an_ended_session_refuses_new_messages(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client, kinds=("ai_disclosure", "terms", "store_chat"))
    session = await open_session(client, who, save=True)
    async with database.session_factory() as db:
        await ChatRepository(db).end(uuid.UUID(session["id"]))
        await db.commit()

    response = await say(client, who, session["id"], SAD_MESSAGE)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_ended"


# --------------------------------------------------------------------------- #
# Ownership                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("save", [False, True], ids=["ephemeral", "saved"])
async def test_user_a_cannot_read_user_bs_session(
    client: httpx.AsyncClient, llm: FakeLLMProvider, save: bool
) -> None:
    kinds = ("ai_disclosure", "terms", "store_chat")
    alice = await person(client, kinds=kinds)
    mallory = await person(client, kinds=kinds)
    session = await open_session(client, alice, save=save)
    await say(client, alice, session["id"], "something private about my week")
    calls_before = llm.call_count
    base = f"{SESSIONS}/{session['id']}"

    attempts = [
        await client.get(base, headers=mallory.headers),
        await client.get(f"{base}/messages", headers=mallory.headers),
        await say(client, mallory, session["id"], "let me in"),
        await client.post(f"{base}/stream", headers=mallory.headers, json={"message": "hi"}),
        await client.get(f"{base}/stream", headers=mallory.headers, params={"message": "hi"}),
    ]

    for response in attempts:
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "session_not_found"
        assert "private" not in response.text
    assert llm.call_count == calls_before, "a refused request must not reach the model"
    # ...and the rightful owner is unaffected.
    mine = await client.get(f"{base}/messages", headers=alice.headers)
    assert mine.status_code == 200 and len(mine.json()["messages"]) == 2


async def test_someone_elses_session_is_indistinguishable_from_one_that_never_existed(
    client: httpx.AsyncClient,
) -> None:
    alice = await person(client)
    mallory = await person(client)
    session = await open_session(client, alice)

    theirs = await client.get(f"{SESSIONS}/{session['id']}", headers=mallory.headers)
    nothing = await client.get(f"{SESSIONS}/{uuid.uuid4()}", headers=mallory.headers)

    assert theirs.status_code == nothing.status_code == 404
    assert theirs.json()["error"]["code"] == nothing.json()["error"]["code"]
    assert theirs.json()["error"]["message"] == nothing.json()["error"]["message"]


async def test_an_ephemeral_sessions_history_is_readable_by_its_owner(
    client: httpx.AsyncClient,
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    await say(client, who, session["id"], SAD_MESSAGE)

    info = await client.get(f"{SESSIONS}/{session['id']}", headers=who.headers)
    assert info.status_code == 200 and info.json()["persistent"] is False
    history = (await client.get(f"{SESSIONS}/{session['id']}/messages", headers=who.headers)).json()
    assert history["persistent"] is False
    assert [(m["role"], m["text"]) for m in history["messages"]] == [
        ("user", SAD_MESSAGE),
        ("assistant", DEFAULT_REPLY),
    ]


async def test_a_saved_sessions_history_is_decrypted_for_its_owner(
    client: httpx.AsyncClient,
) -> None:
    who = await person(client, kinds=("ai_disclosure", "terms", "store_chat"))
    session = await open_session(client, who, save=True)
    await say(client, who, session["id"], SAD_MESSAGE)
    await say(client, who, session["id"], HIGH_MESSAGE)

    history = (await client.get(f"{SESSIONS}/{session['id']}/messages", headers=who.headers)).json()
    assert [m["text"] for m in history["messages"]][:2] == [SAD_MESSAGE, DEFAULT_REPLY]
    assert [m["risk_level"] for m in history["messages"]] == [0, 0, 3, 3]
    limited = (
        await client.get(
            f"{SESSIONS}/{session['id']}/messages", headers=who.headers, params={"limit": 1}
        )
    ).json()
    assert len(limited["messages"]) == 1


async def test_an_expired_ephemeral_session_is_a_404(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    store = app.state.chat_sessions.ephemeral
    store._sessions[uuid.UUID(session["id"])].expires_at = 0.0  # lapsed

    response = await say(client, who, session["id"], SAD_MESSAGE)
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Validation and rate limiting                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("  \n  ", "message_empty"),
        ("x" * 4001, "message_too_long"),
        ("hello\x00there", "message_encoding"),
        # A lone surrogate never reaches the orchestrator over HTTP: the JSON
        # parser refuses it first (the orchestrator's own check is unit-tested).
        ("lone \ud800 surrogate", "validation_error"),
    ],
)
async def test_bad_messages_get_a_curated_422(
    client: httpx.AsyncClient, llm: FakeLLMProvider, message: str, code: str
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    # json.dumps escapes the lone surrogate ("\\ud800"), as a real client would.
    response = await client.post(
        f"{SESSIONS}/{session['id']}/messages",
        headers={**who.headers, "Content-Type": "application/json"},
        content=json.dumps({"message": message}),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert message.strip() == "" or message not in response.text
    assert llm.call_count == 0


async def test_an_empty_string_is_a_validation_error(client: httpx.AsyncClient) -> None:
    who = await person(client)
    session = await open_session(client, who)
    response = await say(client, who, session["id"], "")
    assert response.status_code == 422


async def test_the_rate_limit_is_per_user_with_retry_after(
    client: httpx.AsyncClient, app: FastAPI, settings: Settings, llm: FakeLLMProvider
) -> None:
    app.state.chat_orchestrator = build_orchestrator(
        settings.model_copy(update={"chat_rate_limit_per_minute": 2}),
        llm=llm,
        analyzer=KeywordFallbackAnalyzer(),
    )
    busy = await person(client)
    calm = await person(client)
    busy_session = await open_session(client, busy)
    calm_session = await open_session(client, calm)

    assert (await say(client, busy, busy_session["id"], "one")).status_code == 200
    assert (await say(client, busy, busy_session["id"], "two")).status_code == 200
    limited = await say(client, busy, busy_session["id"], "three")

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    assert llm.call_count == 2
    assert (await say(client, calm, calm_session["id"], "hello")).status_code == 200


# --------------------------------------------------------------------------- #
# LLM outage                                                                    #
# --------------------------------------------------------------------------- #


async def test_an_llm_outage_is_a_fallback_reply_not_a_500(
    client: httpx.AsyncClient, app: FastAPI, settings: Settings
) -> None:
    app.state.chat_orchestrator = build_orchestrator(
        settings,
        llm=FakeLLMProvider(error=ProviderDown("provider is down")),
        analyzer=KeywordFallbackAnalyzer(),
    )
    who = await person(client)
    session = await open_session(client, who)
    response = await say(client, who, session["id"], SAD_MESSAGE)

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == CANNED_REPLY
    assert body["metadata"]["response_type"] == "fallback"
    assert body["metadata"]["degraded"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        # Anthropic with no key: not configured -> next link -> canned.
        {"llm_provider": "anthropic"},
        # Ollama pointed at a port nothing listens on: connection refused.
        {
            "llm_provider": "ollama",
            "ollama_base_url": "http://127.0.0.1:9",
            "llm_max_attempts": 1,
            "llm_timeout_seconds": 2.0,
        },
    ],
    ids=["no-api-key", "provider-down"],
)
async def test_the_real_provider_chain_degrades_to_the_fallback_template(
    client: httpx.AsyncClient, app: FastAPI, settings: Settings, overrides: dict[str, Any]
) -> None:
    app.state.chat_orchestrator = build_orchestrator(
        settings.model_copy(update=overrides), analyzer=KeywordFallbackAnalyzer()
    )
    who = await person(client)
    session = await open_session(client, who)

    response = await say(client, who, session["id"], SAD_MESSAGE)
    assert response.status_code == 200
    assert response.json()["reply"] == CANNED_REPLY
    assert response.json()["metadata"]["response_type"] == "fallback"

    streamed = await client.post(
        f"{SESSIONS}/{session['id']}/stream", headers=who.headers, json={"message": SAD_MESSAGE}
    )
    assert streamed.status_code == 200
    events = parse_sse(streamed.text)
    assert events[-1][0] == "final"
    assert events[-1][1]["metadata"]["response_type"] == "fallback"


async def test_a_broken_safety_stage_is_a_503_and_the_model_is_not_called(
    client: httpx.AsyncClient, app: FastAPI, llm: FakeLLMProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    who = await person(client)
    session = await open_session(client, who)

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("rules engine unavailable")

    monkeypatch.setattr(app.state.chat_orchestrator._engine, "assess", explode)

    for path in ("messages", "stream"):
        response = await client.post(
            f"{SESSIONS}/{session['id']}/{path}",
            headers=who.headers,
            json={"message": SAD_MESSAGE},
        )
        assert response.status_code == 503, "a real status, even for the stream"
        assert response.json()["error"]["code"] == "safety_unavailable"
    assert llm.call_count == 0 and not llm.stream_calls


# --------------------------------------------------------------------------- #
# Server-Sent Events                                                            #
# --------------------------------------------------------------------------- #


async def test_a_post_stream_is_token_events_then_a_final_event_with_metadata(
    client: httpx.AsyncClient, app: FastAPI, settings: Settings
) -> None:
    llm = FakeLLMProvider(chunks=["I hear ", "that. ", "It sounds ", "heavy."])
    app.state.chat_orchestrator = build_orchestrator(
        settings, llm=llm, analyzer=KeywordFallbackAnalyzer()
    )
    who = await person(client)
    session = await open_session(client, who)

    response = await client.post(
        f"{SESSIONS}/{session['id']}/stream", headers=who.headers, json={"message": SAD_MESSAGE}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = parse_sse(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds == ["token"] * 4 + ["final"], "tokens in order, then exactly one final"
    assert [data["text"] for kind, data in events if kind == "token"] == [
        "I hear ",
        "that. ",
        "It sounds ",
        "heavy.",
    ]
    final = events[-1][1]
    assert final["reply"] == "I hear that. It sounds heavy."
    assert final["replaced"] is False
    assert final["session_id"] == session["id"]
    assert final["metadata"]["risk_level"] == "none"
    assert final["metadata"]["emotion"] == "sadness"
    assert final["metadata"]["response_type"] == "normal"
    assert final["metadata"]["resources"] == []


async def test_a_get_stream_is_the_same_stream(client: httpx.AsyncClient) -> None:
    who = await person(client)
    session = await open_session(client, who)

    response = await client.get(
        f"{SESSIONS}/{session['id']}/stream",
        headers=who.headers,
        params={"message": SAD_MESSAGE, "region": "IN"},
    )

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1][0] == "final"
    assert "".join(d["text"] for k, d in events if k == "token") == events[-1][1]["reply"]
    assert events[-1][1]["metadata"]["region"] == "IN"


async def test_a_crisis_stream_has_a_token_a_final_and_no_model_call(
    client: httpx.AsyncClient, llm: FakeLLMProvider
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    response = await client.post(
        f"{SESSIONS}/{session['id']}/stream", headers=who.headers, json={"message": HIGH_MESSAGE}
    )

    events = parse_sse(response.text)
    assert [kind for kind, _ in events] == ["token", "final"]
    meta = events[-1][1]["metadata"]
    assert meta["response_type"] == "crisis" and meta["risk_level"] == "high"
    assert meta["crisis"] is not None and meta["resources"]
    assert llm.call_count == 0 and not llm.stream_calls


async def test_a_streamed_medium_reply_includes_the_check_in_and_resources(
    client: httpx.AsyncClient,
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    response = await client.post(
        f"{SESSIONS}/{session['id']}/stream", headers=who.headers, json={"message": MEDIUM_MESSAGE}
    )
    final = parse_sse(response.text)[-1][1]
    assert final["metadata"]["response_type"] == "check_in"
    assert "helplines" in final["reply"]
    assert final["metadata"]["resources"]


async def test_stream_problems_known_up_front_are_http_errors_not_events(
    client: httpx.AsyncClient,
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    missing = await client.post(
        f"{SESSIONS}/{uuid.uuid4()}/stream", headers=who.headers, json={"message": "hi"}
    )
    blank = await client.post(
        f"{SESSIONS}/{session['id']}/stream", headers=who.headers, json={"message": "   "}
    )
    assert missing.status_code == 404
    assert blank.status_code == 422
    assert not missing.headers["content-type"].startswith("text/event-stream")


async def test_a_streamed_turn_is_stored_before_the_final_event(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client, kinds=("ai_disclosure", "terms", "store_chat"))
    session = await open_session(client, who, save=True)
    response = await client.post(
        f"{SESSIONS}/{session['id']}/stream", headers=who.headers, json={"message": SAD_MESSAGE}
    )
    final = parse_sse(response.text)[-1][1]

    assert final["metadata"]["persisted"] is True and final["message_id"]
    assert await count(database, Message) == 2


# --------------------------------------------------------------------------- #
# Wiring and privacy                                                            #
# --------------------------------------------------------------------------- #


async def test_the_orchestrator_is_built_lazily_from_settings(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    fresh = client_factory(emotion_analyzer="keyword", llm_provider="fake")
    app: FastAPI = fresh._transport.app  # type: ignore[attr-defined]
    assert app.state.chat_orchestrator is None, "nothing is loaded at start-up"

    who = await person(fresh)
    session = await open_session(fresh, who)
    response = await say(fresh, who, session["id"], SAD_MESSAGE)

    assert response.status_code == 200
    assert response.json()["reply"] == DEFAULT_REPLY
    assert app.state.chat_orchestrator is not None


async def test_the_request_log_never_contains_what_was_said(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    who = await person(client, kinds=("ai_disclosure", "terms", "store_chat"))
    session = await open_session(client, who, save=True)
    capsys.readouterr()
    await say(client, who, session["id"], PII_MESSAGE)
    await say(client, who, session["id"], HIGH_MESSAGE)
    await client.post(
        f"{SESSIONS}/{session['id']}/stream", headers=who.headers, json={"message": SAD_MESSAGE}
    )

    logged = capsys.readouterr().out
    assert "chat_turn" in logged
    for secret in (
        PII_MESSAGE,
        "jo.sharma",
        "9876543210",
        HIGH_MESSAGE,
        SAD_MESSAGE,
        DEFAULT_REPLY,
    ):
        assert secret not in logged


async def test_a_deleted_user_cannot_use_their_token(
    client: httpx.AsyncClient, database: Database
) -> None:
    who = await person(client)
    session = await open_session(client, who)
    async with database.session_factory() as db:
        await db.execute(
            text("UPDATE users SET deleted_at = :now WHERE id = :id").bindparams(
                now=datetime.now(UTC), id=uuid.UUID(who.id).hex
            )
        )
        await db.commit()
    response = await say(client, who, session["id"], SAD_MESSAGE)
    assert response.status_code == 401


async def test_the_app_factory_gives_each_app_its_own_ephemeral_store(
    settings: Settings, database: Database
) -> None:
    first = create_app(settings, database=database)
    second = create_app(settings, database=database)
    assert first.state.chat_sessions.ephemeral is not second.state.chat_sessions.ephemeral
