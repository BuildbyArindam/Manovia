"""End-to-end integration tests for ``/api/v1/journal``.

Verifies:
- Auth and consent gating (TERMS + PRIVACY required)
- Localised, rotating writing prompts and gratitude/worry_dump templates
- Journal CRUD (POST, GET list with pagination, GET single, PUT update, DELETE)
- Ownership isolation across users
- In-memory sentiment/emotion scoring and user opt-out (``store_emotions=False``)
- Mandatory safety screening on write (HIGH risk returns crisis resources in
  ``metadata.resources`` and records a metadata-only ``SafetyEvent``; safety
  engine failure fails closed with 503)
- Raw SQLite inspection proving ``title_encrypted`` and ``body_encrypted`` are
  ``MENV\\x01`` ciphertexts and no plaintext ever touches the DB or logs
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging import configure_logging
from app.models.enums import RiskLevel, SafetyEventSource
from app.models.journal import JournalEntry
from app.models.safety_event import SafetyEvent
from app.services.journal.crypto import is_envelope_ciphertext

JOURNAL = "/api/v1/journal"
PROMPTS = "/api/v1/journal/prompts"
VERSION = "2026-10-01"

HIGH_RISK_TEXT = "I want to kill myself tonight and cannot go on."


@dataclass
class Person:
    """A signed-in guest account with granted consents."""

    id: uuid.UUID
    headers: dict[str, str]


async def make_person(
    client: httpx.AsyncClient,
    *,
    kinds: tuple[str, ...] = ("terms", "privacy"),
) -> Person:
    resp = await client.post("/api/v1/auth/guest")
    assert resp.status_code == 201
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    if kinds:
        c_resp = await client.post(
            "/api/v1/consent",
            headers=headers,
            json={"grants": [{"kind": k, "version": VERSION, "granted": True} for k in kinds]},
        )
        assert c_resp.status_code == 201
    return Person(id=uuid.UUID(data["user"]["id"]), headers=headers)


async def test_journal_routes_require_auth_and_consent(client: httpx.AsyncClient) -> None:
    unauth = await client.get(JOURNAL)
    assert unauth.status_code == 401

    unconsented = await make_person(client, kinds=())
    no_consent = await client.get(JOURNAL, headers=unconsented.headers)
    assert no_consent.status_code == 403

    only_terms = await make_person(client, kinds=("terms",))
    partial = await client.post(
        JOURNAL,
        headers=only_terms.headers,
        json={"title": "Test", "body": "Body"},
    )
    assert partial.status_code == 403


async def test_journal_prompts_rotate_and_localise(client: httpx.AsyncClient) -> None:
    alice = await make_person(client)

    day1 = await client.get(
        PROMPTS,
        headers=alice.headers,
        params={"lang": "en", "for_date": "2026-10-11", "count": 4},
    )
    assert day1.status_code == 200
    body1 = day1.json()
    assert body1["locale"] == "en"
    assert len(body1["prompts"]) == 4
    assert {t["id"] for t in body1["templates"]} == {"free", "gratitude", "worry_dump"}

    day2 = await client.get(
        PROMPTS,
        headers=alice.headers,
        params={"lang": "en", "for_date": "2026-10-12", "count": 4},
    )
    assert day2.status_code == 200
    body2 = day2.json()
    assert body2["featured"]["id"] != body1["featured"]["id"]

    # Hindi and Bengali localisations return non-ASCII native script strings.
    hi_resp = await client.get(
        PROMPTS,
        headers=alice.headers,
        params={"lang": "hi", "for_date": str(date(2026, 10, 11))},
    )
    assert hi_resp.status_code == 200
    hi_body = hi_resp.json()
    assert hi_body["locale"] == "hi"
    assert any(ord(ch) > 127 for ch in hi_body["featured"]["text"])

    bn_resp = await client.get(
        PROMPTS,
        headers=alice.headers,
        params={"lang": "bn", "template": "gratitude"},
    )
    assert bn_resp.status_code == 200
    bn_body = bn_resp.json()
    assert bn_body["locale"] == "bn"
    assert all(p["template"] == "gratitude" for p in bn_body["prompts"])


async def test_journal_crud_and_pagination_and_isolation(
    client: httpx.AsyncClient,
) -> None:
    alice = await make_person(client)
    bob = await make_person(client)

    # Create 3 entries for Alice.
    created_ids: list[str] = []
    for idx in range(3):
        resp = await client.post(
            JOURNAL,
            headers=alice.headers,
            json={
                "title": f"Entry #{idx + 1}",
                "body": f"Today I noticed calm detail number {idx + 1} while walking.",
                "template": "gratitude" if idx % 2 == 0 else "worry_dump",
                "prompt_id": "prompt-small-anchor",
                "store_emotions": True,
            },
        )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["title"] == f"Entry #{idx + 1}"
        assert payload["word_count"] >= 9
        assert payload["store_emotions"] is True
        assert payload["sentiment"] is not None
        assert isinstance(payload["emotion_scores"], dict)
        assert payload["metadata"]["risk_level"] == "none"
        created_ids.append(payload["id"])

    # Paginated list: page 1 (limit=2, offset=0) and page 2 (limit=2, offset=2).
    page1 = await client.get(JOURNAL, headers=alice.headers, params={"limit": 2, "offset": 0})
    assert page1.status_code == 200
    p1 = page1.json()
    assert p1["count"] == 2
    assert p1["total"] == 3
    assert p1["has_more"] is True

    page2 = await client.get(JOURNAL, headers=alice.headers, params={"limit": 2, "offset": 2})
    assert page2.status_code == 200
    p2 = page2.json()
    assert p2["count"] == 1
    assert p2["total"] == 3
    assert p2["has_more"] is False

    # Filter by template.
    gratitude_only = await client.get(
        JOURNAL, headers=alice.headers, params={"template": "gratitude"}
    )
    assert gratitude_only.status_code == 200
    assert gratitude_only.json()["total"] == 2

    # Update an entry (simulating debounced autosave).
    target_id = created_ids[0]
    upd = await client.put(
        f"{JOURNAL}/{target_id}",
        headers=alice.headers,
        json={
            "title": "Updated Entry #1",
            "body": "Autosaved second draft with extra thoughts.",
            "template": "free",
            "store_emotions": True,
        },
    )
    assert upd.status_code == 200
    assert upd.json()["title"] == "Updated Entry #1"
    assert upd.json()["body"] == "Autosaved second draft with extra thoughts."

    # Single GET returns the updated content.
    fetched = await client.get(f"{JOURNAL}/{target_id}", headers=alice.headers)
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "Updated Entry #1"

    # Cross-user isolation: Bob cannot read, update, or delete Alice's entry.
    assert (await client.get(f"{JOURNAL}/{target_id}", headers=bob.headers)).status_code == 404
    assert (
        await client.put(
            f"{JOURNAL}/{target_id}",
            headers=bob.headers,
            json={"title": "Hijack", "body": "Nope"},
        )
    ).status_code == 404
    assert (await client.delete(f"{JOURNAL}/{target_id}", headers=bob.headers)).status_code == 404

    # Alice deletes her entry; subsequent GET returns 404.
    deleted = await client.delete(f"{JOURNAL}/{target_id}", headers=alice.headers)
    assert deleted.status_code == 204
    assert (await client.get(f"{JOURNAL}/{target_id}", headers=alice.headers)).status_code == 404


async def test_user_can_disable_emotion_scoring_on_write_and_update(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    alice = await make_person(client)

    resp = await client.post(
        JOURNAL,
        headers=alice.headers,
        json={
            "title": "No scoring please",
            "body": "I feel very happy and grateful today, but I turned off scoring.",
            "store_emotions": False,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["store_emotions"] is False
    assert data["sentiment"] is None
    assert data["emotion_scores"] is None
    assert data["metadata"]["primary_emotion"] is None

    row = await session.get(JournalEntry, uuid.UUID(data["id"]))
    assert row is not None
    assert row.sentiment is None
    assert row.emotion_scores is None


async def test_high_risk_entry_returns_crisis_resources_in_metadata_and_records_event(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    alice = await make_person(client)

    resp = await client.post(
        JOURNAL,
        headers=alice.headers,
        json={
            "title": "Late night",
            "body": HIGH_RISK_TEXT,
            "template": "worry_dump",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["metadata"]["risk_level"] == "high"
    assert len(body["metadata"]["resources"]) >= 1
    assert body["metadata"]["crisis"] is not None
    assert body["crisis"] is not None
    assert len(body["crisis"]["resources"]) >= 1

    # Verify SafetyEvent was recorded with source=JOURNAL and no raw text.
    events = (
        (await session.execute(select(SafetyEvent).where(SafetyEvent.user_id == alice.id)))
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].risk_level == RiskLevel.CRISIS
    assert events[0].source == SafetyEventSource.RULES


async def test_safety_failure_fails_closed_with_503_and_writes_nothing(
    client: httpx.AsyncClient,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice = await make_person(client)

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("safety classifier exploded")

    monkeypatch.setattr("app.api.v1.journal.run_ensemble", _boom)

    resp = await client.post(
        JOURNAL,
        headers=alice.headers,
        json={"title": "Should not save", "body": "If safety is down, fail closed."},
    )
    assert resp.status_code == 503
    rows = (
        (await session.execute(select(JournalEntry).where(JournalEntry.user_id == alice.id)))
        .scalars()
        .all()
    )
    assert rows == []


async def test_plaintext_never_appears_in_database_or_logs(
    client: httpx.AsyncClient,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")

    alice = await make_person(client)
    secret_title = "MyUltraUniqueSecretTitle_99481"
    secret_body = "MyUltraUniqueSecretBody_77219_NeverInPlaintextAnywhere"

    resp = await client.post(
        JOURNAL,
        headers=alice.headers,
        json={"title": secret_title, "body": secret_body},
    )
    assert resp.status_code == 201

    # 1. Check raw SQLite database file and raw row bytes.
    db_path = Path(sa.make_url(settings.database_url).database or "")
    raw_db_bytes = db_path.read_bytes()
    assert secret_title.encode("utf-8") not in raw_db_bytes
    assert secret_body.encode("utf-8") not in raw_db_bytes

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT title_encrypted, body_encrypted FROM journal_entries WHERE id = ?",
            (uuid.UUID(resp.json()["id"]).hex,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert is_envelope_ciphertext(row[0])
    assert is_envelope_ciphertext(row[1])

    # 2. Check structured log stream.
    captured = capsys.readouterr()
    logs_output = captured.out + captured.err
    assert "journal.created" in logs_output
    assert secret_title not in logs_output
    assert secret_body not in logs_output
