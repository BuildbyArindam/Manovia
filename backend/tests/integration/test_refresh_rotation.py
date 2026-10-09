"""Refresh-token rotation and stolen-token (reuse) detection over real HTTP."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import httpx

from app.core.tokens import hash_token
from app.db.repos import RefreshTokenRepository, UserRepository
from app.db.session import Database
from app.models.base import as_utc, utcnow


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "rotating@example.test", "password": "a-fine-passphrase"},
    )
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


async def test_refresh_rotates_the_token(client: httpx.AsyncClient) -> None:
    body = await _register(client)

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rotated.status_code == 200
    new = rotated.json()
    assert new["refresh_token"] != body["refresh_token"], "rotation issues a new token"
    assert new["access_token"] != body["access_token"]
    assert new["user"]["id"] == body["user"]["id"]

    # The fresh access token works.
    me = await client.get("/api/v1/auth/me", headers=_auth(new["access_token"]))
    assert me.status_code == 200


async def test_reusing_a_rotated_token_revokes_the_whole_family(
    client: httpx.AsyncClient, database: Database
) -> None:
    """The classic stolen-refresh-token detection: one replay burns the family.

    Sequence: register (rt1) -> refresh rt1 (issues rt2) -> replay rt1. The
    replay proves possession of a rotated token, so neither rt1 nor rt2 nor any
    of their descendants may be used again.
    """
    body = await _register(client)
    rt1 = body["refresh_token"]

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt1})
    assert rotated.status_code == 200
    rt2 = rotated.json()["refresh_token"]

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt1})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_token_reused"

    # The legitimate successor is dead too: the whole family is revoked.
    successor = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt2})
    assert successor.status_code == 401
    assert successor.json()["error"]["code"] == "refresh_token_reused"

    # And the database agrees: no active rows for that user.
    async with database.session_factory() as session:
        repo = RefreshTokenRepository(session)
        for token in (rt1, rt2):
            row = await repo.get_by_hash(hash_token(token))
            assert row is not None and row.revoked_at is not None


async def test_a_second_rotation_then_replay_still_burns_everything(
    client: httpx.AsyncClient,
) -> None:
    body = await _register(client)
    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    rt2 = first.json()["refresh_token"]
    second = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt2})
    rt3 = second.json()["refresh_token"]

    # Replay the *first* rotation's input; even the newest token must die.
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_token_reused"

    newest = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt3})
    assert newest.status_code == 401


async def test_login_refresh_logout_then_old_token_is_dead(client: httpx.AsyncClient) -> None:
    """Register -> login -> refresh -> logout: the old refresh token is refused."""
    await _register(client)
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "rotating@example.test", "password": "a-fine-passphrase"},
    )
    assert login.status_code == 200
    session = login.json()

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]}
    )
    assert rotated.status_code == 200

    await client.post(
        "/api/v1/auth/logout", json={"refresh_token": rotated.json()["refresh_token"]}
    )

    # Every token of that family — including the one presented to logout — is dead.
    for token in (session["refresh_token"], rotated.json()["refresh_token"]):
        rejected = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert rejected.status_code == 401
        assert rejected.json()["error"]["code"] in {"refresh_token_reused", "refresh_token_invalid"}


async def test_families_do_not_bleed_into_each_other(client: httpx.AsyncClient) -> None:
    """Reusing one session's token must not kill the user's other session."""
    body = await _register(client)
    other = await client.post(
        "/api/v1/auth/login",
        json={"email": "rotating@example.test", "password": "a-fine-passphrase"},
    )
    other_session = other.json()

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rotated.status_code == 200

    # Burn the first family.
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert replay.status_code == 401

    # The second family (from login) is untouched.
    still = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": other_session["refresh_token"]}
    )
    assert still.status_code == 200


async def test_revoked_timestamps_are_set_on_rotation(
    client: httpx.AsyncClient, database: Database
) -> None:
    body = await _register(client)
    before = utcnow()
    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rotated.status_code == 200

    async with database.session_factory() as session:
        repo = RefreshTokenRepository(session)
        old = await repo.get_by_hash(hash_token(body["refresh_token"]))
        new = await repo.get_by_hash(hash_token(rotated.json()["refresh_token"]))
    assert old is not None and old.revoked_at is not None and as_utc(old.revoked_at) >= before
    assert new is not None and new.is_active
    assert old.family_id == new.family_id, "rotation stays inside the family"


async def test_garbage_refresh_tokens_are_rejected(client: httpx.AsyncClient) -> None:
    """Not even a JWT: one clear code, no stack traces."""
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": "made-up-token"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "refresh_token_invalid"


async def test_a_row_that_expired_in_the_database_is_rejected(
    client: httpx.AsyncClient, database: Database
) -> None:
    """The row's expires_at is authoritative even when the JWT looks fresh."""
    body = await _register(client)
    async with database.session_factory() as session:
        row = await RefreshTokenRepository(session).get_by_hash(hash_token(body["refresh_token"]))
        assert row is not None
        row.expires_at = utcnow() - timedelta(minutes=1)
        await session.commit()

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "refresh_token_expired"


async def test_a_soft_deleted_user_cannot_rotate(
    client: httpx.AsyncClient, database: Database
) -> None:
    body = await _register(client)
    async with database.session_factory() as session:
        await UserRepository(session).soft_delete(uuid.UUID(body["user"]["id"]))
        await session.commit()

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "account_deleted"
