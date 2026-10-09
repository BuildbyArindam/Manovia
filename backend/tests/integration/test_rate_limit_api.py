"""Rate limiting over real HTTP: strict on auth, moderate globally (Day 4)."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from app.core import crypto
from app.core.config import Settings
from app.db.session import Database
from app.main import create_app


async def test_auth_endpoints_are_rate_limited(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    client = client_factory(rate_limit_auth_per_minute=3, rate_limit_global_per_minute=100)
    for _ in range(3):
        response = await client.post("/api/v1/auth/guest")
        assert response.status_code == 201

    limited = await client.post("/api/v1/auth/guest")
    assert limited.status_code == 429
    error = limited.json()["error"]
    assert error["code"] == "rate_limited"
    assert error["request_id"]
    retry = int(limited.headers["Retry-After"])
    assert 1 <= retry <= 60
    # Even the 429 wears the full uniform: request id + security headers.
    assert limited.headers["X-Request-ID"] == error["request_id"]
    assert limited.headers["X-Content-Type-Options"] == "nosniff"


async def test_the_global_limit_is_moderate(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    client = client_factory(rate_limit_global_per_minute=2, rate_limit_auth_per_minute=50)
    assert (await client.get("/api/v1/health")).status_code == 200
    assert (await client.get("/api/v1/health")).status_code == 200

    limited = await client.get("/api/v1/health")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"

    # A different bucket: the auth endpoints still have their own budget.
    assert (await client.post("/api/v1/auth/guest")).status_code == 201

    # Paths outside /api/ (docs, schema) are not counted at all.
    assert (await client.get("/openapi.json")).status_code == 200


async def test_limits_are_per_client_ip(
    database: Database, settings: Settings, cipher: crypto.FieldCipher
) -> None:
    """One budget per client address: a second IP is unaffected by the first."""
    app = create_app(
        settings.model_copy(update={"rate_limit_auth_per_minute": 1}), database=database
    )
    first = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("1.1.1.1", 1111)),
        base_url="http://testserver",
    )
    second = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("2.2.2.2", 2222)),
        base_url="http://testserver",
    )
    async with first, second:
        assert (await first.post("/api/v1/auth/guest")).status_code == 201
        assert (await first.post("/api/v1/auth/guest")).status_code == 429
        assert (await second.post("/api/v1/auth/guest")).status_code == 201


async def test_rate_limiting_can_be_disabled(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    client = client_factory(rate_limit_enabled=False, rate_limit_auth_per_minute=1)
    for _ in range(5):
        assert (await client.post("/api/v1/auth/guest")).status_code == 201


async def test_other_auth_endpoints_share_the_strict_budget(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    client = client_factory(rate_limit_auth_per_minute=2)
    assert (await client.post("/api/v1/auth/guest")).status_code == 201
    login = await client.post(
        "/api/v1/auth/login", json={"email": "a@example.test", "password": "wrong-entirely"}
    )
    assert login.status_code == 401  # last slot in the budget
    limited = await client.post("/api/v1/auth/guest")
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
