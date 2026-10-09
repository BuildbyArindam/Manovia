"""Integration tests for the health and readiness endpoints."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.health import router
from app.core.config import Settings
from app.main import create_app


async def test_health_is_a_liveness_probe(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_reports_config_and_database_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"config": "ok", "database": "ok"}
    assert body["app_env"] == "test"


async def test_ready_never_names_the_database_in_its_response(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    """The probe is unauthenticated: a DSN (or a file path) must not leak."""
    response = await client.get("/api/v1/ready")
    assert "manovia-test.db" not in response.text
    assert "sqlite" not in response.text
    assert "secret" not in response.text


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


async def test_ready_fails_closed_when_the_database_is_unreachable(
    settings: Settings, tmp_path: Path
) -> None:
    """A process that cannot persist data must not receive traffic."""
    broken = settings.model_copy(
        update={"database_url": f"sqlite:///{tmp_path / 'missing-dir' / 'manovia.db'}"}
    )
    app = create_app(broken)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as ac:
            response = await ac.get("/api/v1/ready")
        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "not_ready"
        assert body["error"]["message"] == "Database is not reachable"
        assert body["error"]["request_id"] == response.headers["x-request-id"]
    finally:
        await app.state.db.dispose()


async def test_ready_reports_connectivity_and_not_migration_state(
    settings: Settings, tmp_path: Path
) -> None:
    """Readiness means "the database answers", so an unmigrated file is ready.

    Running migrations is a deploy step, not something a replica should race to
    finish before it accepts traffic; ``scripts/seed_demo_data.py`` is the place
    that insists on a schema.
    """
    virgin = settings.model_copy(update={"database_url": f"sqlite:///{tmp_path / 'fresh.db'}"})
    app = create_app(virgin)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as ac:
            response = await ac.get("/api/v1/ready")
        assert response.status_code == 200
        assert response.json()["checks"]["database"] == "ok"
        # A SQLite file is created on first connect, so nothing was pre-created.
        assert (tmp_path / "fresh.db").exists()
    finally:
        await app.state.db.dispose()


async def test_ready_fails_closed_when_the_application_has_no_database(
    settings: Settings,
) -> None:
    """A bare app (no data layer wired) must not claim readiness."""
    bare = FastAPI()
    bare.include_router(router, prefix="/api/v1")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=bare), base_url="http://testserver"
    ) as ac:
        response = await ac.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Database is not reachable"
