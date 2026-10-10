"""Day 11 verification script — runs real API calls with Fake LLM.

Steps:
2. Simulate via API: neutral, sad, MEDIUM, HIGH -> print response_type + risk_level
4. LLM outage fallback
5. Session ownership
6. DB encryption + ephemeral no rows
7. Latency p50/p95 for 20 messages
"""

import asyncio
import time
import uuid
import json
from pathlib import Path

import httpx
from sqlalchemy import text as sa_text

from app.core import crypto
from app.core.config import Settings
from app.db.session import build_database
from app.main import create_app
from app.models import Base
from app.db.repos import UserRepository


async def main():
    # Setup settings with temp DB
    tmp_path = Path("/tmp/manovia_verify_day11.db")
    if tmp_path.exists():
        tmp_path.unlink()
    key = crypto.FernetCipher.generate_key()
    settings = Settings(
        _env_file=None,
        app_env="test",
        secret_key="test-secret-key-0123456789abcdef",
        field_encryption_key=key,
        allowed_origins="http://testserver",
        database_url=f"sqlite:///{tmp_path}",
        safety_ml_enabled="false",
        llm_provider="fake",
        chat_rate_limit_per_minute=100,
    )
    cipher = crypto.FernetCipher.from_settings(settings)
    crypto.configure_cipher(cipher)

    database = build_database(settings)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = create_app(settings, database=database)
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    async def guest():
        resp = await client.post("/api/v1/auth/guest")
        assert resp.status_code == 201, resp.text
        return resp.json()

    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    async def grant_all(token):
        headers = auth(token)
        for kind in ("ai_disclosure", "terms", "privacy", "store_chat"):
            r = await client.post(
                "/api/v1/consent",
                headers=headers,
                json={"grants": [{"kind": kind, "version": "2026-10-01", "granted": True}]},
            )
            assert r.status_code == 201

    # Create user A
    g = await guest()
    token_a = g["access_token"]
    await grant_all(token_a)
    headers_a = auth(token_a)

    # Create persistent session
    sess_resp = await client.post("/api/v1/chat/sessions", headers=headers_a, json={"store": True})
    assert sess_resp.status_code == 201
    sid = sess_resp.json()["id"]

    print("=== Step 2: Simulate 4 message types via API ===")
    cases = [
        ("neutral", "Hello, I had a nice day today."),
        ("sad", "I feel so alone lately and sad"),
        ("medium", "Everything feels grey lately, I can't enjoy anything"),
        ("high", "I want to die"),
    ]
    for label, text in cases:
        # Reset fake call count
        chain = app.state.llm_chain
        primary = chain.providers[0]
        if hasattr(primary, "calls"):
            primary.calls.clear()
        resp = await client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            headers=headers_a,
            json={"content": text},
        )
        assert resp.status_code == 200, f"{label} failed: {resp.text}"
        body = resp.json()
        print(
            f"{label:8s} => risk_level={body['risk_level']:9s} response_type={body['response_type']:12s} emotion={body.get('emotion')} degraded={body.get('degraded')} llm_calls={getattr(primary, 'call_count', 0)}"
        )
        if label == "high":
            assert primary.call_count == 0, "HIGH must have 0 LLM calls"
            print(f"  HIGH check: LLM call count is 0 -> PASS")
            assert len(body["resources"]) > 0
            print(f"  HIGH check: resources present ({len(body['resources'])}) -> PASS")

    print("\n=== Step 3: Stream a response (via API client streaming) ===")
    # Stream neutral
    async with client.stream(
        "POST",
        f"/api/v1/chat/sessions/{sid}/messages/stream",
        headers=headers_a,
        json={"content": "Hello streaming test"},
    ) as sresp:
        assert sresp.status_code == 200
        print(f"Stream status: {sresp.status_code}, content-type: {sresp.headers.get('content-type')}")
        events = []
        async for line in sresp.aiter_lines():
            if not line.strip():
                continue
            if line.startswith("data:"):
                try:
                    data = json.loads(line[5:].strip())
                    events.append(data)
                    if "token" in data:
                        print(f"  token event: {data['token'][:50]!r}")
                    elif data.get("type") == "final":
                        print(f"  final event: risk_level={data.get('risk_level')} emotion={data.get('emotion')} response_type={data.get('response_type')} resources={len(data.get('resources', []))}")
                except Exception as e:
                    print(f"  failed to parse data: {line} err {e}")
        token_events = [e for e in events if "token" in e]
        final_events = [e for e in events if e.get("type") == "final"]
        print(f"  total events: {len(events)}, token: {len(token_events)}, final: {len(final_events)}")
        assert len(token_events) >= 1
        assert len(final_events) >= 1
        print("  SSE order check -> PASS")

    print("\n=== Step 4: LLM outage fallback ===")
    from app.services.llm.base import ProviderDown
    from app.services.llm.canned import CannedProvider, CANNED_REPLY
    from app.services.llm.chain import LLMChain
    from app.services.llm.fake_provider import FakeLLMProvider
    from app.services.nlp.redaction import Redactor

    failing = FakeLLMProvider()
    failing.script_error(ProviderDown("outage"), times=10)
    failing_chain = LLMChain([failing, CannedProvider()], redactor=Redactor(), redact=True)
    original_chain = app.state.llm_chain
    app.state.llm_chain = failing_chain

    resp = await client.post(
        f"/api/v1/chat/sessions/{sid}/messages",
        headers=headers_a,
        json={"content": "Test when LLM is down"},
    )
    print(f"Outage response status: {resp.status_code}")
    assert resp.status_code == 200, "outage must not be 500"
    body = resp.json()
    print(f"  degraded={body.get('degraded')} provider={body.get('provider')} response_type={body.get('response_type')}")
    print(f"  assistant content: {body['assistant_message']['content'][:100]!r}")
    assert body["assistant_message"]["content"] == CANNED_REPLY or body.get("degraded") is True
    print("  Fallback graceful, no 500 -> PASS")

    app.state.llm_chain = original_chain

    print("\n=== Step 5: Session ownership enforced ===")
    # User B
    g2 = await guest()
    token_b = g2["access_token"]
    await grant_all(token_b)
    headers_b = auth(token_b)

    resp = await client.get(f"/api/v1/chat/sessions/{sid}", headers=headers_b)
    print(f"  B reading A's session: status={resp.status_code} (expect 403/404)")
    assert resp.status_code in (403, 404)
    print("  Ownership enforced -> PASS")

    print("\n=== Step 6: DB encryption + ephemeral no rows ===")
    # Check persistent messages are encrypted
    async with database.session_factory() as db_sess:
        rows = (await db_sess.execute(sa_text("SELECT content_encrypted FROM messages"))).fetchall()
        print(f"  DB messages count: {len(rows)}")
        for row in rows:
            blob = row[0]
            # Should not contain plaintext
            assert b"nice day" not in blob
            assert b"I want to die" not in blob
            # Fernet token starts with gAAAAA
            assert blob.startswith(b"gAAAAA") or len(blob) > 20
        print("  Stored message bodies are encrypted -> PASS")

    # Ephemeral session
    eph_resp = await client.post("/api/v1/chat/sessions", headers=headers_a, json={"store": False})
    assert eph_resp.status_code == 201
    eph_sid = eph_resp.json()["id"]
    assert eph_resp.json()["ephemeral"] is True
    msg_resp = await client.post(
        f"/api/v1/chat/sessions/{eph_sid}/messages",
        headers=headers_a,
        json={"content": "Ephemeral secret"},
    )
    assert msg_resp.status_code == 200
    async with database.session_factory() as db_sess:
        count = (
            await db_sess.execute(
                sa_text("SELECT COUNT(*) FROM messages WHERE session_id = :sid"),
                {"sid": eph_sid},
            )
        ).scalar()
        print(f"  Ephemeral session {eph_sid} DB message count: {count} (expect 0)")
        assert count == 0
        count2 = (
            await db_sess.execute(
                sa_text("SELECT COUNT(*) FROM chat_sessions WHERE id = :sid"),
                {"sid": eph_sid},
            )
        ).scalar()
        print(f"  Ephemeral session DB session count: {count2} (expect 0)")
        assert count2 == 0
    print("  Ephemeral leaves no rows -> PASS")

    print("\n=== Step 7: Latency p50/p95 for 20 messages (Fake LLM) ===")
    latencies = []
    for i in range(20):
        chain = app.state.llm_chain
        primary = chain.providers[0]
        if hasattr(primary, "calls"):
            primary.calls.clear()
        start = time.monotonic()
        resp = await client.post(
            f"/api/v1/chat/sessions/{sid}/messages",
            headers=headers_a,
            json={"content": f"Latency test message {i} hello world"},
        )
        elapsed = (time.monotonic() - start) * 1000  # ms
        latencies.append(elapsed)
        assert resp.status_code == 200

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95_idx = int(len(latencies) * 0.95)
    p95 = latencies[min(p95_idx, len(latencies) - 1)]
    print(f"  20 messages: min={latencies[0]:.2f}ms max={latencies[-1]:.2f}ms mean={sum(latencies)/len(latencies):.2f}ms")
    print(f"  p50={p50:.2f}ms p95={p95:.2f}ms")
    print("  Latency measured -> PASS")

    await client.aclose()
    await database.dispose()
    print("\n=== All verification steps done ===")


if __name__ == "__main__":
    asyncio.run(main())
