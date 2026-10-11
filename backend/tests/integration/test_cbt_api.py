"""Day 19: real API, encrypted storage and safety at each step."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cbt import CBTRecord
from app.services.cbt import TRAPS
from app.services.journal.crypto import is_envelope_ciphertext
from app.services.llm.fake_provider import FakeLLMProvider

URL = "/api/v1/cbt"
THOUGHT = "I'll fail the exam and everyone will think I'm stupid"
HIGH = "I want to kill myself tonight and cannot go on."
SAMPLE = {
    "situation": "An exam is tomorrow",
    "automatic_thought": THOUGHT,
    "emotion": "anxiety",
    "intensity_before": 85,
    "evidence_for": "I missed one question",
    "evidence_against": "I studied and passed practice tests",
    "balanced_thought": "I might not know every answer, but I prepared",
    "intensity_after": 45,
    "what_helped": ["looking at evidence"],
}


async def person(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    consent = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": "2026-10-01", "granted": True}
                for kind in ("terms", "privacy")
            ]
        },
    )
    assert consent.status_code == 201
    return headers


@pytest.mark.asyncio
async def test_wizard_api_encryption_and_ownership(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    headers = await person(client)
    assert (await client.get(URL + "/records")).status_code == 401
    for field in (
        "situation",
        "automatic_thought",
        "evidence_for",
        "evidence_against",
        "balanced_thought",
    ):
        result = await client.post(
            URL + "/screen", headers=headers, json={"field": field, "text": SAMPLE[field]}
        )
        assert result.status_code == 200, result.text
        assert result.json()["interrupted"] is False
    saved = await client.post(URL + "/records", headers=headers, json=SAMPLE)
    assert saved.status_code == 200, saved.text
    assert saved.json()["interrupted"] is False
    record = saved.json()["record"]
    assert record["intensity_before"] == 85 and record["intensity_after"] == 45
    assert (await client.get(URL + "/records", headers=headers)).json()[0]["id"] == record["id"]
    assert (await client.get(URL + "/records/" + record["id"], headers=headers)).json()[
        "automatic_thought"
    ] == THOUGHT
    row = (await session.execute(select(CBTRecord))).scalar_one()
    for field in (
        "situation",
        "automatic_thought",
        "evidence_for",
        "evidence_against",
        "balanced_thought",
    ):
        blob = getattr(row, f"{field}_encrypted")
        assert is_envelope_ciphertext(blob)
        assert str(SAMPLE[field]).encode() not in blob
    other = await person(client)
    assert (await client.get(URL + "/records/" + record["id"], headers=other)).status_code == 404
    assert (await client.get(URL + "/records", headers=other)).json() == []
    assert (
        await client.request("DELETE", URL + "/records/" + record["id"], headers=headers)
    ).status_code == 204
    assert (await client.get(URL + "/records/" + record["id"], headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_schema_rejects_invalid(client: httpx.AsyncClient) -> None:
    headers = await person(client)
    for change in (
        {"intensity_before": 101},
        {"intensity_after": -1},
        {"emotion": "diagnosis"},
        {"what_helped": ["custom text"]},
        {"situation": "  "},
    ):
        result = await client.post(URL + "/records", headers=headers, json={**SAMPLE, **change})
        assert result.status_code == 422, result.text
    assert (await client.get(URL + "/records", headers=headers)).json() == []


@pytest.mark.asyncio
async def test_safety_interrupt_mid_wizard_and_bypass(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    headers = await person(client)
    fake = FakeLLMProvider().script('["mind-reading"]')
    app.state.cbt_llm = fake
    result = await client.post(
        URL + "/traps", headers=headers, json={"field": "automatic_thought", "text": HIGH}
    )
    assert result.status_code == 200, result.text
    assert result.json()["interrupted"] is True
    assert result.json()["crisis"]["resources"]
    assert not fake.calls
    bypass = await client.post(
        URL + "/records", headers=headers, json={**SAMPLE, "automatic_thought": HIGH}
    )
    assert bypass.json()["interrupted"] is True
    assert bypass.json()["record"] is None
    assert (await client.get(URL + "/records", headers=headers)).json() == []
    later = await client.post(
        URL + "/screen", headers=headers, json={"field": "evidence_against", "text": HIGH}
    )
    assert later.json()["interrupted"] is True


@pytest.mark.asyncio
async def test_traps_fixed_taxonomy_guard_redaction_and_fallback(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    headers = await person(client)
    fake = FakeLLMProvider().script(
        '["mind-reading", "made-up", "fortune-telling", "catastrophising", "labelling"]'
    )
    app.state.cbt_llm = fake
    result = (
        await client.post(
            URL + "/traps",
            headers=headers,
            json={"field": "automatic_thought", "text": THOUGHT + " email me at user@example.com"},
        )
    ).json()
    assert result["source"] == "llm"
    assert [item["id"] for item in result["candidates"]] == [
        "mind-reading",
        "fortune-telling",
        "catastrophising",
    ]
    assert all("might be" in item["explanation"] for item in result["candidates"])
    assert set(item["id"] for item in result["candidates"]) <= TRAPS.keys()
    assert "user@example.com" not in fake.calls[0].user_text
    # A diagnosis-like sentence cannot be emitted even when appended to an in-list ID.
    fake.script('["mind-reading"] You have a disorder.')
    rejected = (
        await client.post(
            URL + "/traps", headers=headers, json={"field": "automatic_thought", "text": THOUGHT}
        )
    ).json()
    assert rejected["source"] == "fallback"
    assert "disorder" not in str(rejected)
    fake.script_error(Exception("offline"))
    offline = (
        await client.post(
            URL + "/traps", headers=headers, json={"field": "automatic_thought", "text": THOUGHT}
        )
    ).json()
    assert offline["source"] == "fallback"
    assert [candidate["id"] for candidate in offline["candidates"]] == [
        "mind-reading",
        "fortune-telling",
        "labelling",
    ]


@pytest.mark.asyncio
async def test_cbt_key_rotation_retires_old_key(session: AsyncSession) -> None:
    """Regression: adding a ciphertext column without rotating it strands records."""
    from app.core.crypto import configure_cipher, decrypt_field, encrypt_field
    from app.db.repos.users import UserRepository
    from app.services.journal.crypto import (
        EnvelopeCipher,
        generate_master_key,
        rotate_database_encryption,
    )

    first, second = generate_master_key(), generate_master_key()
    cipher = EnvelopeCipher(f"v1:{first}")
    configure_cipher(cipher)
    user = await UserRepository(session).create(email="rotate-cbt@example.test")
    wrapped, _ = await UserRepository(session).ensure_data_key(user.id)
    fields = {
        f"{field}_encrypted": encrypt_field(
            str(SAMPLE[field]), user_id=user.id, field_name=f"cbt.{field}", wrapped_dek=wrapped
        )
        for field in (
            "situation",
            "automatic_thought",
            "evidence_for",
            "evidence_against",
            "balanced_thought",
        )
    }
    record = CBTRecord(
        user_id=user.id,
        key_id="v1",
        emotion="anxiety",
        intensity_before=85,
        intensity_after=45,
        what_helped=[],
        **fields,
    )
    session.add(record)
    await session.commit()
    rotating = EnvelopeCipher(f"v2:{second},v1:{first}")
    configure_cipher(rotating)
    report = await rotate_database_encryption(session, rotating)
    await session.commit()
    assert report.cbt_records_rotated == 1
    await session.refresh(user)
    await session.refresh(record)
    assert record.key_id == "v2"
    configure_cipher(EnvelopeCipher(f"v2:{second}"))
    for field in (
        "situation",
        "automatic_thought",
        "evidence_for",
        "evidence_against",
        "balanced_thought",
    ):
        assert (
            decrypt_field(
                getattr(record, f"{field}_encrypted"),
                user_id=user.id,
                field_name=f"cbt.{field}",
                wrapped_dek=user.data_key_encrypted,
            )
            == SAMPLE[field]
        )
