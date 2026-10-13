"""Day 27: service metrics through the real chat pipeline.

One end-to-end pass proves the counters the runbook depends on actually move
when traffic flows: request rate against route templates, the LLM counter, the
output-guard verdict counter, and safety events by level — and that not one
label carries the words that produced them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.core.config import Settings
from app.core.metrics import reset_metrics
from app.services.chat import build_orchestrator
from app.services.llm.fake_provider import FakeLLMProvider
from app.services.nlp.keyword import KeywordFallbackAnalyzer
from tests.chat.conftest import HIGH_MESSAGE, NEUTRAL_MESSAGE

SESSIONS = "/api/v1/chat/sessions"


@pytest.fixture
def llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest_asyncio.fixture(autouse=True)
async def installed_fake(app: FastAPI, settings: Settings, llm: FakeLLMProvider) -> None:
    app.state.chat_orchestrator = build_orchestrator(
        settings, llm=llm, analyzer=KeywordFallbackAnalyzer()
    )


@pytest.fixture
async def fresh_metrics() -> AsyncIterator[None]:
    """A clean metrics registry around each test (counters are process-wide)."""
    reset_metrics()
    yield
    reset_metrics()


async def _guest(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    granted = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": "ai_disclosure", "version": "2026-10-01", "granted": True},
                {"kind": "terms", "version": "2026-10-01", "granted": True},
            ]
        },
    )
    assert granted.status_code == 201
    return headers


async def test_chat_traffic_moves_every_required_metric(
    client: httpx.AsyncClient, fresh_metrics: None
) -> None:
    headers = await _guest(client)
    created = await client.post(SESSIONS, headers=headers)
    assert created.status_code == 201
    session_id = created.json()["id"]

    sent = await client.post(
        f"{SESSIONS}/{session_id}/messages", headers=headers, json={"message": NEUTRAL_MESSAGE}
    )
    assert sent.status_code == 200

    # A HIGH-risk message goes through the deterministic crisis path and must
    # leave exactly the metadata-only safety event the escalation policy says.
    high = await client.post(
        f"{SESSIONS}/{session_id}/messages", headers=headers, json={"message": HIGH_MESSAGE}
    )
    assert high.status_code == 200

    body = (await client.get("/metrics")).text

    # Request counters carry route templates — never the raw session id.
    assert 'path="/api/v1/chat/sessions",status="201"' in body
    assert 'path="/api/v1/chat/sessions/{session_id}/messages"' in body
    assert session_id not in body
    # The LLM (Fake provider, leading position) served the neutral turn.
    assert 'manovia_llm_requests_total{position="0",provider="fake"} 1.0' in body
    # The neutral reply cleared the output guard on the first check; the HIGH
    # turn is the deterministic crisis path (no model, no guard by design).
    assert 'manovia_output_guard_total{outcome="passed"} 1.0' in body
    # The HIGH message recorded a crisis-level rules event — count, no content.
    assert 'manovia_safety_events_total{level="crisis",source="rules"} 1.0' in body
    # Nothing a person typed appears anywhere in the exposition.
    for fragment in (NEUTRAL_MESSAGE, HIGH_MESSAGE, "kill"):
        assert fragment not in body
