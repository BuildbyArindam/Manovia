"""Shared pytest fixtures: isolated settings and an in-process HTTP client."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app

_ENV_KEYS = (
    "APP_ENV",
    "SECRET_KEY",
    "DATABASE_URL",
    "LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "OLLAMA_BASE_URL",
    "FIELD_ENCRYPTION_KEY",
    "ALLOWED_ORIGINS",
    "LOG_LEVEL",
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Valid test settings, isolated from the environment and any .env file."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return Settings(
        _env_file=None,
        app_env="test",
        secret_key="test-secret-key",
        allowed_origins="http://testserver",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """A fresh application instance configured with the test settings."""
    return create_app(settings)


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client wired straight to the app (no network involved)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
