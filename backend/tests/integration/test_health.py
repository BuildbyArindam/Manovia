"""Integration tests for the health and readiness endpoints."""

import httpx
import pytest

from app.core.config import Settings


async def test_health_is_a_liveness_probe(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_reports_config_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"config": "ok"}


async def test_ready_fails_closed_when_config_is_invalid(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Point env_file at nothing so a developer's local .env cannot mask the
    # broken configuration, then make the environment itself invalid.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    response = await client.get("/api/v1/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "not_ready"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
