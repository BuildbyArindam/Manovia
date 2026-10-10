"""POST /crisis/assess with the Day 9 ML ensemble switched on.

The default test settings run rules-only (the Day 8 contract stays pinned in
``test_crisis_api.py``). These tests flip ``SAFETY_ML_ENABLED`` on and assert
what Day 9 adds:

* the ensemble metadata on the response;
* metadata-only ``safety_events`` rows at MEDIUM and above — level, source,
  no text anywhere;
* the ML path can raise, and the public endpoint never crashes when the
  classifier degrades.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import select

from app.core.config import Settings
from app.db.session import Database
from app.models import SafetyEvent
from app.models.enums import RiskLevel as StoredRiskLevel
from app.models.enums import SafetyEventSource

ASSESS = "/api/v1/crisis/assess"


@pytest_asyncio.fixture
async def ml_client(
    settings: Settings, database: Database, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    """A client whose app runs with the ML ensemble enabled."""
    from app.main import create_app

    monkeypatch.setattr(settings, "safety_ml_enabled", True)
    app = create_app(settings, database=database)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _events(database: Database) -> list[SafetyEvent]:
    async with database.session_factory() as session:
        return list((await session.execute(select(SafetyEvent))).scalars())


async def test_a_high_message_writes_a_metadata_only_safety_event(
    ml_client: httpx.AsyncClient, database: Database
) -> None:
    response = await ml_client.post(ASSESS, json={"text": "I want to die", "region": "IN"})
    assert response.status_code == 200
    body = response.json()
    assert body["level"] == "high"
    assert body["policy"]["record_event"] is True
    assert body["ensemble"]["ml_used"] is True
    assert body["ensemble"]["source"] in ("rules", "ml", "ensemble.uncertain")

    events = await _events(database)
    assert len(events) == 1
    event = events[0]
    assert event.risk_level == int(StoredRiskLevel.CRISIS)
    assert event.source in (SafetyEventSource.RULES, SafetyEventSource.ML)
    assert event.user_id is None  # public endpoint, no account
    assert event.session_id is None
    # No text, structurally: every column is metadata (mirrors the whitelist
    # in tests/unit/test_models.py::test_safety_event_has_no_textual_column).
    allowed = (sa.Uuid, sa.SmallInteger, sa.Enum, sa.DateTime)
    for column in SafetyEvent.__table__.columns:
        assert isinstance(column.type, allowed), f"safety_events.{column.name} holds prose?"


async def test_a_medium_message_writes_an_elevated_row(
    ml_client: httpx.AsyncClient, database: Database
) -> None:
    response = await ml_client.post(
        ASSESS, json={"text": "I wish I could just disappear for a while, maybe forever."}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["level"] in ("medium", "high", "imminent")  # ML may raise
    if body["level"] == "medium":
        assert body["policy"]["template_id"] == "check_in.medium"
        assert body["policy"]["record_event"] is True
        events = await _events(database)
        assert len(events) == 1
        assert events[0].risk_level == int(StoredRiskLevel.ELEVATED)


async def test_a_none_message_writes_no_row(
    ml_client: httpx.AsyncClient, database: Database
) -> None:
    response = await ml_client.post(ASSESS, json={"text": "The new cafe does great coffee."})
    assert response.status_code == 200
    # Whatever the ensemble says, nothing below MEDIUM persists.
    body = response.json()
    if body["level"] in ("none", "low"):
        assert await _events(database) == []


async def test_ensemble_metadata_never_echoes_the_input(
    ml_client: httpx.AsyncClient, database: Database
) -> None:
    text = "zzqx7canary the deadline is killing me"
    response = await ml_client.post(ASSESS, json={"text": text})
    assert response.status_code == 200
    payload = response.text
    assert "zzqx7canary" not in payload
    assert "deadline" not in payload


async def test_rules_only_response_shape_is_unchanged_when_ml_is_off(
    client: httpx.AsyncClient, database: Database
) -> None:
    """With the default (disabled) setting the ensemble reports rules-only."""
    body = (await client.post(ASSESS, json={"text": "I want to die", "region": "IN"})).json()
    assert body["ensemble"]["ml_used"] is False
    assert body["ensemble"]["source"] == "rules"
    assert body["ensemble"]["ml_level"] is None
    # And no row was written: ML off, rules HIGH -> still a row is due,
    # because record_event follows the POLICY, not the ensemble source.
    events = await _events(database)
    assert len(events) == 1
    assert events[0].source is SafetyEventSource.RULES
