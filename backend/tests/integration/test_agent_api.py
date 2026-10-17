"""The navigator's HTTP surface.

Proves the wire-level contract: consent and auth,
the safety gate in front of the agent (a HIGH-risk
phrase mid-conversation never invokes it), the
confirmation round-trip, and ownership rules.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from app.services.agent.llm import FakeAgentLLM
from app.services.agent.service import build_agent_navigator

AGENT_URL = "/api/v1/agent"


@pytest.fixture
async def person(client: AsyncClient) -> dict[str, str]:
    """A signed-in guest who consented to AI + terms.

    The navigator needs exactly the consents chat needs.
    """
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    consent = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": "2026-10-01", "granted": True}
                for kind in ("terms", "ai_disclosure")
            ]
        },
    )
    assert consent.status_code == 201
    return headers


def _scripted_navigator(settings: Any, script: FakeAgentLLM) -> Any:
    """A navigator whose model is a scripted Fake."""
    return build_agent_navigator(settings, llm=script)


def _reminder_script() -> FakeAgentLLM:
    fake = FakeAgentLLM()
    fake.script_tool_call(
        "set_reminder",
        {
            "text": "Call the counsellor back",
            "when": "2026-10-17T09:00:00+05:30",
        },
    )
    fake.script_text("I've noted that for you.")
    return fake


@pytest.mark.asyncio
async def test_the_agent_requires_auth_and_consent(
    client: AsyncClient,
) -> None:
    result = await client.post(AGENT_URL + "/sessions")
    assert result.status_code == 401


@pytest.mark.asyncio
async def test_a_turn_served_by_a_scripted_fake(
    client: AsyncClient,
    person: Any,
    app: Any,
    settings: Any,
) -> None:
    fake = FakeAgentLLM()
    fake.script_tool_call("explain_options", {"topic": "therapy_types"})
    fake.script_text("Here are the main kinds of therapy.")
    app.state.agent_navigator = _scripted_navigator(settings, fake)

    session = await client.post(AGENT_URL + "/sessions", headers=person)
    assert session.status_code == 201, session.text
    session_id = session.json()["session_id"]

    result = await client.post(
        f"{AGENT_URL}/sessions/{session_id}/chat",
        headers=person,
        json={"message": "What kinds of therapy are there?"},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["response_type"] == "agent"
    assert body["tools_executed"] == ["explain_options"]
    # Transparency: the whole turn is in the action log.
    events = [entry["event"] for entry in body["action_log"]]
    assert "tool_proposed" in events
    assert "tool_executed" in events


@pytest.mark.asyncio
async def test_a_high_risk_phrase_mid_conversation_skips_the_agent(
    client: AsyncClient,
    person: Any,
    app: Any,
    settings: Any,
) -> None:
    fake = FakeAgentLLM()
    fake.script_text("Here are the main kinds of therapy.")
    app.state.agent_navigator = _scripted_navigator(settings, fake)

    session = await client.post(AGENT_URL + "/sessions", headers=person)
    session_id = session.json()["session_id"]

    # A harmless first turn.
    first = await client.post(
        f"{AGENT_URL}/sessions/{session_id}/chat",
        headers=person,
        json={"message": "Tell me about therapy types"},
    )
    assert first.status_code == 200
    assert first.json()["response_type"] == "agent"
    calls_before = len(fake.calls)

    # Then the crisis phrase. The agent must not run:
    # the deterministic crisis flow answers.
    second = await client.post(
        f"{AGENT_URL}/sessions/{session_id}/chat",
        headers=person,
        json={"message": "I want to kill myself tonight"},
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["response_type"] == "crisis"
    assert body["risk_level"] in {"high", "imminent"}
    assert body["crisis_resources"]
    assert body["tools_executed"] == []
    assert body["steps_used"] == 0
    events = [entry["event"] for entry in body["action_log"]]
    assert "safety_gate_blocked" in events
    # The model was not called for the crisis turn.
    assert len(fake.calls) == calls_before


@pytest.mark.asyncio
async def test_a_reminder_needs_the_confirmation_round_trip(
    client: AsyncClient,
    person: Any,
    app: Any,
    settings: Any,
) -> None:
    app.state.agent_navigator = _scripted_navigator(settings, _reminder_script())
    session = await client.post(AGENT_URL + "/sessions", headers=person)
    session_id = session.json()["session_id"]

    result = await client.post(
        f"{AGENT_URL}/sessions/{session_id}/chat",
        headers=person,
        json={"message": "Remind me to call the counsellor back tomorrow at 9am"},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["response_type"] == "confirmation_required"
    confirmation = body["confirmation"]
    assert confirmation["tool"] == "set_reminder"
    assert confirmation["pending_id"]

    # Deny: nothing executes, and the denial is logged.
    denied = await client.post(
        f"{AGENT_URL}/sessions/{session_id}/confirmations/{confirmation['pending_id']}",
        headers=person,
        json={"decision": "deny"},
    )
    assert denied.status_code == 200, denied.text
    denied_body = denied.json()
    assert denied_body["tools_executed"] == []
    events = [entry["event"] for entry in denied_body["action_log"]]
    assert "confirmation_denied" in events
    assert "tool_executed" not in events


@pytest.mark.asyncio
async def test_an_allowed_reminder_executes(
    client: AsyncClient,
    person: Any,
    app: Any,
    settings: Any,
) -> None:
    app.state.agent_navigator = _scripted_navigator(settings, _reminder_script())
    session = await client.post(AGENT_URL + "/sessions", headers=person)
    session_id = session.json()["session_id"]

    result = await client.post(
        f"{AGENT_URL}/sessions/{session_id}/chat",
        headers=person,
        json={"message": "Remind me to call the counsellor back tomorrow at 9am"},
    )
    confirmation = result.json()["confirmation"]
    assert confirmation is not None

    allowed = await client.post(
        f"{AGENT_URL}/sessions/{session_id}/confirmations/{confirmation['pending_id']}",
        headers=person,
        json={"decision": "allow"},
    )
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["tools_executed"] == ["set_reminder"]
    events = [entry["event"] for entry in body["action_log"]]
    assert events.index("confirmation_allowed") < events.index("tool_executed")


@pytest.mark.asyncio
async def test_sessions_are_owned(
    client: AsyncClient,
    person: Any,
    app: Any,
    settings: Any,
) -> None:
    fake = FakeAgentLLM()
    fake.script_text("Here are the main kinds of therapy.")
    app.state.agent_navigator = _scripted_navigator(settings, fake)
    session = await client.post(AGENT_URL + "/sessions", headers=person)
    session_id = session.json()["session_id"]

    # A different guest gets the same 404 a stranger would.
    guest = await client.post("/api/v1/auth/guest")
    other = {"Authorization": f"Bearer {guest.json()['access_token']}"}
    consent = await client.post(
        "/api/v1/consent",
        headers=other,
        json={
            "grants": [
                {"kind": kind, "version": "2026-10-01", "granted": True}
                for kind in ("terms", "ai_disclosure")
            ]
        },
    )
    assert consent.status_code == 201
    response = await client.post(
        f"{AGENT_URL}/sessions/{session_id}/chat",
        headers=other,
        json={"message": "Hello?"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_the_tool_allow_list_is_listed(
    client: AsyncClient,
    person: Any,
) -> None:
    result = await client.get(AGENT_URL + "/tools", headers=person)
    assert result.status_code == 200, result.text
    tools = result.json()["tools"]
    assert set(tools) == {
        "search_directory",
        "draft_message",
        "make_appointment_prep",
        "set_reminder",
        "explain_options",
    }
