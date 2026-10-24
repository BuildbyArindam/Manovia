"""Day 26: races that a sequential test can never see.

Both of these passed the existing sequential tests while being wrong under
concurrency, because each handler read a row and then wrote it as two steps:

* **Refresh-token rotation.** Two simultaneous refreshes of one token must not
  both succeed. Exactly one may rotate; the loser holds a spent token, which is
  the reuse case, so the family is revoked.
* **Assessment double-submit.** A double-tap or a client retry sends the same
  answers twice within milliseconds. That is one questionnaire, not two rows
  that double the history and bend the trend chart.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentResult
from app.models.refresh_token import RefreshToken

ASSESSMENTS = "/api/v1/assessments"
VERSION = "2026-10-01"
PHQ9 = {f"phq9_{n}": 1 for n in range(1, 10)}


async def _guest(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


async def test_concurrent_refresh_of_one_token_rotates_exactly_once(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    body = await _guest(client)
    token = body["refresh_token"]
    user_id = uuid.UUID(body["user"]["id"])

    responses = await asyncio.gather(
        *(client.post("/api/v1/auth/refresh", json={"refresh_token": token}) for _ in range(8))
    )

    winners = [r for r in responses if r.status_code == 200]
    losers = [r for r in responses if r.status_code == 401]
    assert len(winners) == 1, [r.status_code for r in responses]
    assert len(losers) == 7
    assert {r.json()["error"]["code"] for r in losers} == {"refresh_token_reused"}

    # The loser burned the family, so even the winner's successor is dead: a
    # replayed token is evidence of theft, and nothing in the family is trusted.
    successor = winners[0].json()["refresh_token"]
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": successor})
    assert after.status_code == 401

    active = (
        await session.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        )
    ).scalar_one()
    assert active == 0


async def test_a_single_refresh_is_unaffected_by_the_atomic_claim(
    client: httpx.AsyncClient,
) -> None:
    body = await _guest(client)
    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert first.status_code == 200
    second = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}
    )
    assert second.status_code == 200, "sequential rotation keeps working"


async def test_double_submitted_assessment_is_stored_once(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    body = await _guest(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    granted = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": VERSION, "granted": True} for kind in ("terms", "privacy")
            ]
        },
    )
    assert granted.status_code == 201

    responses = await asyncio.gather(
        *(
            client.post(f"{ASSESSMENTS}/phq9/submit", headers=headers, json={"answers": PHQ9})
            for _ in range(6)
        )
    )

    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["result"]["id"] for r in responses}) == 1, "one stored result"
    stored = (
        await session.execute(
            select(func.count())
            .select_from(AssessmentResult)
            .where(AssessmentResult.user_id == uuid.UUID(body["user"]["id"]))
        )
    ).scalar_one()
    assert stored == 1


async def test_a_deliberate_second_submission_with_different_answers_is_kept(
    client: httpx.AsyncClient,
) -> None:
    body = await _guest(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": VERSION, "granted": True} for kind in ("terms", "privacy")
            ]
        },
    )
    first = await client.post(f"{ASSESSMENTS}/phq9/submit", headers=headers, json={"answers": PHQ9})
    changed = {**PHQ9, "phq9_1": 3}
    second = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=headers, json={"answers": changed}
    )
    assert first.json()["result"]["id"] != second.json()["result"]["id"]


async def test_duplicate_item_9_response_still_returns_the_crisis_view(
    client: httpx.AsyncClient,
) -> None:
    """De-duplication must never swallow the item-9 support options."""
    body = await _guest(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": VERSION, "granted": True} for kind in ("terms", "privacy")
            ]
        },
    )
    answers = {**dict.fromkeys(PHQ9, 0), "phq9_9": 1}
    first = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=headers, json={"answers": answers}
    )
    again = await client.post(
        f"{ASSESSMENTS}/phq9/submit", headers=headers, json={"answers": answers}
    )
    assert first.json()["crisis"] is not None
    assert again.json()["crisis"] is not None
    assert again.json()["result"]["id"] == first.json()["result"]["id"]
