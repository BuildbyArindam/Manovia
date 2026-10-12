"""Day 23: ``GET /plan/today`` writes the plan in the person's language.

The plan is generated once per local day, so these tests also pin the honest
behaviour around that: the first request's language sticks for the day, an
unknown locale falls back to English, and the account's saved language is the
default when the request does not name one.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos.users import UserRepository

ROOT = "/api/v1/plan"

FIRST_CHECK_IN_EN = "Take a first check-in"
FIRST_CHECK_IN_HI = "पहला मूड चेक-इन कीजिए"
FIRST_CHECK_IN_BN = "প্রথম মুড চেক-ইন করুন"


async def guest(client: httpx.AsyncClient) -> tuple[uuid.UUID, dict[str, str]]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    requirements = (await client.get("/api/v1/consent/requirements")).json()["documents"]
    grants = [
        {"kind": item["kind"], "version": item["version"], "granted": True}
        for item in requirements
        if item["kind"] in ("terms", "privacy")
    ]
    assert (
        await client.post("/api/v1/consent", headers=headers, json={"grants": grants})
    ).status_code == 201
    return uuid.UUID(body["user"]["id"]), headers


async def test_locale_query_writes_the_plan_in_hindi(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    response = await client.get(f"{ROOT}/today", headers=headers, params={"locale": "hi"})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    item = body["items"][0]
    assert item["title"] == FIRST_CHECK_IN_HI
    assert "चेक-इन" in item["why"]


async def test_locale_query_writes_the_plan_in_bengali(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    response = await client.get(f"{ROOT}/today", headers=headers, params={"locale": "bn"})
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["title"] == FIRST_CHECK_IN_BN


async def test_unknown_locale_falls_back_to_english(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    response = await client.get(f"{ROOT}/today", headers=headers, params={"locale": "fr"})
    assert response.status_code == 200
    assert response.json()["items"][0]["title"] == FIRST_CHECK_IN_EN


async def test_account_language_is_the_default(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user_id, headers = await guest(client)
    user = await UserRepository(session).get(user_id)
    assert user is not None
    user.language = "bn"
    await session.commit()

    response = await client.get(f"{ROOT}/today", headers=headers)
    assert response.status_code == 200
    assert response.json()["items"][0]["title"] == FIRST_CHECK_IN_BN


async def test_the_first_language_of_the_day_sticks(client: httpx.AsyncClient) -> None:
    """A plan is written once per local day: it keeps the language it was
    generated in, even if a later request asks for another one."""
    _, headers = await guest(client)
    first = await client.get(f"{ROOT}/today", headers=headers, params={"locale": "hi"})
    assert first.json()["items"][0]["title"] == FIRST_CHECK_IN_HI

    second = await client.get(f"{ROOT}/today", headers=headers, params={"locale": "en"})
    assert second.status_code == 200
    assert second.json()["items"][0]["title"] == FIRST_CHECK_IN_HI
