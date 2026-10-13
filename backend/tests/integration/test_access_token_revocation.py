"""Day 29 red team: an access token dies with its session (RT-P-TK-02, RT-P-TK-03).

Before Day 29 an access token lived out its 15 minutes after logout and after
a refresh-token reuse burn — so a stolen access token kept working even after
the theft was detected.
"""

from __future__ import annotations

import uuid

import httpx
from fastapi import FastAPI

from app.core.tokens import ACCESS_TOKEN_TYPE


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _guest(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    return {"access": body["access_token"], "refresh": body["refresh_token"]}


async def test_rt_p_tk_02_logout_ends_the_access_token_too(client: httpx.AsyncClient) -> None:
    person = await _guest(client)
    assert (await client.get("/api/v1/auth/me", headers=_auth(person["access"]))).status_code == 200
    out = await client.post("/api/v1/auth/logout", json={"refresh_token": person["refresh"]})
    assert out.status_code == 200
    me = await client.get("/api/v1/auth/me", headers=_auth(person["access"]))
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "token_revoked"
    assert me.headers["WWW-Authenticate"] == "Bearer"


async def test_rt_p_tk_03_a_reuse_burn_ends_the_stolen_access_token(
    client: httpx.AsyncClient,
) -> None:
    person = await _guest(client)
    stolen = person["access"]
    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": person["refresh"]})
    assert first.status_code == 200
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": person["refresh"]})
    assert replay.json()["error"]["code"] == "refresh_token_reused"
    for token in (stolen, first.json()["access_token"]):
        me = await client.get("/api/v1/auth/me", headers=_auth(token))
        assert me.status_code == 401
        assert me.json()["error"]["code"] == "token_revoked"


async def test_normal_rotation_keeps_the_session_working(client: httpx.AsyncClient) -> None:
    person = await _guest(client)
    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": person["refresh"]})
    assert rotated.status_code == 200
    for token in (person["access"], rotated.json()["access_token"]):
        assert (await client.get("/api/v1/auth/me", headers=_auth(token))).status_code == 200


async def test_logging_out_one_device_leaves_another_signed_in(
    client: httpx.AsyncClient,
) -> None:
    body = {
        "email": f"two-{uuid.uuid4().hex[:8]}@example.com",
        "password": "a sturdy passphrase 42",
    }
    registered = await client.post("/api/v1/auth/register", json=body)
    assert registered.status_code == 201, registered.text
    second = await client.post("/api/v1/auth/login", json=body)
    assert second.status_code == 200, second.text
    await client.post(
        "/api/v1/auth/logout", json={"refresh_token": registered.json()["refresh_token"]}
    )
    still = await client.get("/api/v1/auth/me", headers=_auth(second.json()["access_token"]))
    assert still.status_code == 200


async def test_access_tokens_carry_their_family(client: httpx.AsyncClient, app: FastAPI) -> None:
    person = await _guest(client)
    claims = app.state.token_service.verify(person["access"], expected_type=ACCESS_TOKEN_TYPE)
    assert claims.family_id is not None


async def test_a_family_less_access_token_still_works_until_it_expires(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    """Tokens minted before Day 29 carry no ``fam``: they are not cut off on deploy."""
    person = await _guest(client)
    me = (await client.get("/api/v1/auth/me", headers=_auth(person["access"]))).json()
    legacy, _ = app.state.token_service.issue_access(uuid.UUID(me["id"]))
    assert (await client.get("/api/v1/auth/me", headers=_auth(legacy))).status_code == 200
