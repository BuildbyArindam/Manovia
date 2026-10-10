"""The Day 4 auth flows over real HTTP: guest, register, login, upgrade, logout.

Every request goes through the ASGI app with its real middleware, handlers and
database — no mocking of the auth machinery.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.core.tokens import hash_token
from app.db.repos import ChatRepository, RefreshTokenRepository, UserRepository
from app.db.session import Database
from app.models.base import as_utc, utcnow

PASSWORD = "a-fine-passphrase"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(
    client: httpx.AsyncClient, email: str = "person@example.test", password: str = PASSWORD
) -> httpx.Response:
    return await client.post("/api/v1/auth/register", json={"email": email, "password": password})


async def _guest(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post("/api/v1/auth/guest")


# --------------------------------------------------------------------------- #
# Guest flow                                                                    #
# --------------------------------------------------------------------------- #


async def test_guest_flow_mints_tokens_and_guards_protected_endpoints(
    client: httpx.AsyncClient,
) -> None:
    created = await _guest(client)
    assert created.status_code == 201
    body = created.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900  # 15 minutes
    assert body["user"]["is_anonymous"] is True
    assert body["user"]["email"] is None

    # With the token: 200. Without: 401. With garbage: 401.
    me = await client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]

    assert (await client.get("/api/v1/auth/me")).status_code == 401
    missing = await client.get("/api/v1/auth/me")
    assert missing.json()["error"]["code"] == "token_missing"

    garbage = await client.get("/api/v1/auth/me", headers=_auth("not.a.token"))
    assert garbage.status_code == 401
    assert garbage.json()["error"]["code"] == "token_invalid"


async def test_guest_sessions_are_unique_accounts(client: httpx.AsyncClient) -> None:
    first = (await _guest(client)).json()
    second = (await _guest(client)).json()
    assert first["user"]["id"] != second["user"]["id"]
    assert first["access_token"] != second["access_token"]


async def test_bearer_scheme_is_required(client: httpx.AsyncClient) -> None:
    body = (await _guest(client)).json()
    wrong_scheme = await client.get(
        "/api/v1/auth/me", headers={"Authorization": body["access_token"]}
    )
    assert wrong_scheme.status_code == 401
    assert wrong_scheme.json()["error"]["code"] == "token_missing"


# --------------------------------------------------------------------------- #
# Register / login                                                              #
# --------------------------------------------------------------------------- #


async def test_register_then_login_round_trip(client: httpx.AsyncClient) -> None:
    registered = await _register(client, email="Person@Example.Test")
    assert registered.status_code == 201
    body = registered.json()
    assert body["user"]["email"] == "person@example.test", "emails are stored normalised"
    assert body["user"]["is_anonymous"] is False

    login = await client.post(
        "/api/v1/auth/login", json={"email": "person@example.test", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["user"]["id"] == body["user"]["id"]


async def test_password_is_hashed_never_stored_raw(
    client: httpx.AsyncClient, database: Database
) -> None:
    body = (await _register(client)).json()
    async with database.session_factory() as session:
        user = await UserRepository(session).get(uuid.UUID(body["user"]["id"]))
    assert user is not None and user.password_hash is not None
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


async def test_wrong_password_and_unknown_email_fail_identically(
    client: httpx.AsyncClient,
) -> None:
    await _register(client)

    wrong_password = await client.post(
        "/api/v1/auth/login",
        json={"email": "person@example.test", "password": "wrong-password-entirely"},
    )
    unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.test", "password": "wrong-password-entirely"},
    )

    assert wrong_password.status_code == unknown.status_code == 401
    left, right = wrong_password.json()["error"], unknown.json()["error"]
    assert left["code"] == right["code"] == "invalid_credentials"
    assert left["message"] == right["message"], "one constant failure message"


async def test_soft_deleted_accounts_cannot_authenticate(
    client: httpx.AsyncClient, database: Database
) -> None:
    body = (await _register(client)).json()
    user_id = uuid.UUID(body["user"]["id"])
    async with database.session_factory() as session:
        await UserRepository(session).soft_delete(user_id)
        await session.commit()

    login = await client.post(
        "/api/v1/auth/login", json={"email": "person@example.test", "password": PASSWORD}
    )
    assert login.status_code == 401
    assert login.json()["error"]["code"] == "invalid_credentials"

    stale = await client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "account_deleted"


async def test_register_rejects_short_password_with_a_curated_code(
    client: httpx.AsyncClient,
) -> None:
    response = await _register(client, password="short")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "password_invalid"
    assert error["message"] == "Password must be at least 10 characters."


async def test_register_needs_no_password_composition(client: httpx.AsyncClient) -> None:
    """No uppercase/digit/symbol requirement — length only (NIST SP 800-63B)."""
    response = await _register(client, password="alllowercasewithspaces ok")
    assert response.status_code == 201


async def test_register_rejects_implausible_emails(client: httpx.AsyncClient) -> None:
    response = await _register(client, email="not-an-email")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_email"


async def test_register_rejects_a_taken_email(client: httpx.AsyncClient) -> None:
    assert (await _register(client)).status_code == 201
    again = await _register(client)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "email_taken"


async def test_expired_access_token_is_rejected(client: httpx.AsyncClient, app: FastAPI) -> None:
    body = (await _register(client)).json()
    service = app.state.token_service
    expired, _ = service.issue_access(
        uuid.UUID(body["user"]["id"]), now=datetime.now(UTC) - timedelta(hours=1)
    )
    response = await client.get("/api/v1/auth/me", headers=_auth(expired))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_refresh_token_is_not_an_access_token(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    body = (await _register(client)).json()
    service = app.state.token_service
    refresh, _ = service.issue_refresh(uuid.UUID(body["user"]["id"]), family_id=uuid.uuid4())
    response = await client.get("/api/v1/auth/me", headers=_auth(refresh))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_invalid"


# --------------------------------------------------------------------------- #
# Upgrade (anonymous -> registered)                                             #
# --------------------------------------------------------------------------- #


async def test_upgrade_preserves_everything_the_guest_created(
    client: httpx.AsyncClient, database: Database
) -> None:
    guest = (await _guest(client)).json()
    headers = _auth(guest["access_token"])
    user_id = uuid.UUID(guest["user"]["id"])

    # Guest data: consents, then a *saved* chat session through the gated
    # endpoint (Day 11: a session is ephemeral unless the user opts in to
    # history, which is what leaves a row to be orphaned by a bad upgrade).
    for kind in ("ai_disclosure", "terms", "store_chat"):
        await client.post(
            "/api/v1/consent",
            headers=headers,
            json={"grants": [{"kind": kind, "version": "2026-10-01", "granted": True}]},
        )
    opened = await client.post(
        "/api/v1/chat/sessions", headers=headers, json={"save_history": True}
    )
    assert opened.status_code == 201
    session_id = uuid.UUID(opened.json()["id"])

    upgraded = await client.post(
        "/api/v1/auth/upgrade",
        headers=headers,
        json={"email": "grown.up@example.test", "password": PASSWORD},
    )
    assert upgraded.status_code == 200
    body = upgraded.json()
    assert body["user"]["id"] == guest["user"]["id"], "the account is converted, not replaced"
    assert body["user"]["email"] == "grown.up@example.test"
    assert body["user"]["is_anonymous"] is False

    # Same user id owns the pre-upgrade chat session; nothing was orphaned.
    async with database.session_factory() as check:
        sessions = await ChatRepository(check).list_for_user(user_id)
        assert [s.id for s in sessions] == [session_id]
        user = await UserRepository(check).get(user_id)
        assert user is not None and not user.is_anonymous

    # The new tokens work and the account can now sign in with a password.
    me = await client.get("/api/v1/auth/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200
    login = await client.post(
        "/api/v1/auth/login", json={"email": "grown.up@example.test", "password": PASSWORD}
    )
    assert login.status_code == 200


async def test_upgrade_refuses_an_already_registered_account(
    client: httpx.AsyncClient,
) -> None:
    body = (await _register(client)).json()
    response = await client.post(
        "/api/v1/auth/upgrade",
        headers=_auth(body["access_token"]),
        json={"email": "second@example.test", "password": PASSWORD},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "already_registered"


async def test_upgrade_refuses_a_taken_email(client: httpx.AsyncClient) -> None:
    await _register(client, email="taken@example.test")
    guest = (await _guest(client)).json()
    response = await client.post(
        "/api/v1/auth/upgrade",
        headers=_auth(guest["access_token"]),
        json={"email": "taken@example.test", "password": PASSWORD},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


async def test_upgrade_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/upgrade", json={"email": "x@example.test", "password": PASSWORD}
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Logout                                                                        #
# --------------------------------------------------------------------------- #


async def test_logout_revokes_the_refresh_token(client: httpx.AsyncClient) -> None:
    body = (await _register(client)).json()
    out = await client.post("/api/v1/auth/logout", json={"refresh_token": body["refresh_token"]})
    assert out.status_code == 200

    rejected = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "refresh_token_reused"

    # Idempotent: unknown tokens get the same answer (no oracle).
    again = await client.post("/api/v1/auth/logout", json={"refresh_token": body["refresh_token"]})
    assert again.status_code == 200
    unknown = await client.post("/api/v1/auth/logout", json={"refresh_token": "made-up-token"})
    assert unknown.status_code == 200


# --------------------------------------------------------------------------- #
# Lockout                                                                       #
# --------------------------------------------------------------------------- #


async def test_repeated_failed_logins_lock_the_account(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    client = client_factory(
        login_max_failures=3, login_lockout_seconds=60, rate_limit_enabled=False
    )
    await _register(client)

    for _ in range(3):
        failed = await client.post(
            "/api/v1/auth/login",
            json={"email": "person@example.test", "password": "nope-nope-nope"},
        )
        assert failed.status_code == 401
        assert failed.json()["error"]["code"] == "invalid_credentials"

    locked = await client.post(
        "/api/v1/auth/login", json={"email": "person@example.test", "password": "nope-nope-nope"}
    )
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "account_locked"
    assert int(locked.headers["Retry-After"]) >= 1

    # Even the right password is refused while locked: no oracle, no bypass.
    with_password = await client.post(
        "/api/v1/auth/login", json={"email": "person@example.test", "password": PASSWORD}
    )
    assert with_password.status_code == 429
    assert with_password.json()["error"]["code"] == "account_locked"


async def test_lockout_is_per_account_not_per_client(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    client = client_factory(
        login_max_failures=1, login_lockout_seconds=60, rate_limit_enabled=False
    )
    await _register(client, email="one@example.test")
    await _register(client, email="two@example.test")

    await client.post(
        "/api/v1/auth/login", json={"email": "one@example.test", "password": "wrong-entirely"}
    )
    locked = await client.post(
        "/api/v1/auth/login", json={"email": "one@example.test", "password": PASSWORD}
    )
    assert locked.status_code == 429

    other = await client.post(
        "/api/v1/auth/login", json={"email": "two@example.test", "password": PASSWORD}
    )
    assert other.status_code == 200, "a different account is not locked out"


async def test_successful_login_clears_the_failure_counter(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    client = client_factory(
        login_max_failures=3, login_lockout_seconds=60, rate_limit_enabled=False
    )
    await _register(client)
    for _ in range(2):
        await client.post(
            "/api/v1/auth/login",
            json={"email": "person@example.test", "password": "wrong-entirely"},
        )
    ok = await client.post(
        "/api/v1/auth/login", json={"email": "person@example.test", "password": PASSWORD}
    )
    assert ok.status_code == 200
    for _ in range(2):
        await client.post(
            "/api/v1/auth/login",
            json={"email": "person@example.test", "password": "wrong-entirely"},
        )
    still_ok = await client.post(
        "/api/v1/auth/login", json={"email": "person@example.test", "password": PASSWORD}
    )
    assert still_ok.status_code == 200, "the counter reset on the good sign-in"


# --------------------------------------------------------------------------- #
# Refresh tokens at rest                                                        #
# --------------------------------------------------------------------------- #


async def test_refresh_tokens_are_stored_hashed(
    client: httpx.AsyncClient, database: Database
) -> None:
    body = (await _register(client)).json()
    raw = body["refresh_token"]

    async with database.session_factory() as session:
        row = await RefreshTokenRepository(session).get_by_hash(hash_token(raw))
        assert row is not None
        assert row.token_hash == hash_token(raw)
        assert raw not in row.token_hash and len(row.token_hash) == 64
        assert row.is_active
        assert as_utc(row.expires_at) > utcnow()


async def test_expired_refresh_token_is_rejected_with_a_clear_code(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    body = (await _register(client)).json()
    service = app.state.token_service
    expired, _ = service.issue_refresh(
        uuid.UUID(body["user"]["id"]),
        family_id=uuid.uuid4(),
        now=datetime.now(UTC) - timedelta(days=8),
    )
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": expired})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "refresh_token_expired"


async def test_refresh_body_rejects_empty_tokens(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_unknown_refresh_token_is_rejected(client: httpx.AsyncClient, app: FastAPI) -> None:
    """A well-signed JWT with no row is still worthless (tokens are two-part)."""
    service = app.state.token_service
    orphan, _ = service.issue_refresh(uuid.uuid4(), family_id=uuid.uuid4())
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": orphan})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "refresh_token_invalid"


@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/chat/sessions"])
async def test_protected_endpoints_ask_for_bearer_auth(
    client: httpx.AsyncClient, path: str
) -> None:
    response = await client.post(path) if path.endswith("sessions") else await client.get(path)
    assert response.status_code == 401
    assert response.headers.get("WWW-Authenticate") == "Bearer"
