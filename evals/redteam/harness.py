"""Shared plumbing for the Day 29 red-team suite: an in-process app, real users.

Everything runs offline against the real FastAPI application, a throwaway
SQLite database and freshly generated keys — the same shape the backend test
suite uses (``backend/tests/conftest.py``), so a red-team result means the same
thing as a test result. No secret is read from the environment: the keys are
generated here, for this process, and never written anywhere.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402

#: The consent document version every API suite uses.
CONSENT_VERSION = "2026-10-01"
ALL_CONSENTS: tuple[str, ...] = ("terms", "privacy", "ai_disclosure", "store_chat")

#: Environment variables the harness owns for the lifetime of one app.
_OWNED_ENV = (
    "APP_ENV",
    "SECRET_KEY",
    "FIELD_ENCRYPTION_KEY",
    "ALLOWED_ORIGINS",
    "DATABASE_URL",
    "SAFETY_ML_ENABLED",
    "LLM_PROVIDER",
    "RATE_LIMIT_ENABLED",
    "CHAT_RATE_LIMIT_PER_MINUTE",
    "RATE_LIMIT_GLOBAL_PER_MINUTE",
    "RATE_LIMIT_AUTH_PER_MINUTE",
    "PRIVACY_EXPORT_PER_HOUR",
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    "EMOTION_ANALYZER",
)


@contextlib.contextmanager
def _env(values: dict[str, str]) -> Iterator[None]:
    saved = {key: os.environ.get(key) for key in _OWNED_ENV}
    for key in _OWNED_ENV:
        os.environ.pop(key, None)
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@dataclass
class Person:
    """A signed-in caller."""

    id: uuid.UUID
    access_token: str
    refresh_token: str
    label: str = ""

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@dataclass
class App:
    """A running in-process application and an HTTP client bound to it."""

    client: httpx.AsyncClient
    app: Any
    extra: dict[str, Any] = field(default_factory=dict)

    async def guest(self, label: str = "", *, consents: tuple[str, ...] = ALL_CONSENTS) -> Person:
        response = await self.client.post("/api/v1/auth/guest")
        response.raise_for_status()
        body = response.json()
        person = Person(
            id=uuid.UUID(body["user"]["id"]),
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            label=label,
        )
        if consents:
            granted = await self.client.post(
                "/api/v1/consent",
                headers=person.headers,
                json={
                    "grants": [
                        {"kind": kind, "version": CONSENT_VERSION, "granted": True}
                        for kind in consents
                    ]
                },
            )
            granted.raise_for_status()
        return person


@contextlib.asynccontextmanager
async def running_app(
    *,
    rate_limits: bool = False,
    chat_per_minute: int = 1000,
    global_per_minute: int = 100_000,
    export_per_hour: int | None = None,
) -> AsyncIterator[App]:
    """Build the real app over a private SQLite file; tear everything down after."""
    from app.core import crypto

    with tempfile.TemporaryDirectory(prefix="manovia-redteam-") as tmp:
        env = {
            "APP_ENV": "test",
            "SECRET_KEY": secrets.token_urlsafe(48),
            "FIELD_ENCRYPTION_KEY": crypto.FernetCipher.generate_key(),
            "ALLOWED_ORIGINS": "http://testserver",
            "DATABASE_URL": f"sqlite:///{tmp}/redteam.db",
            "SAFETY_ML_ENABLED": "false",
            "LLM_PROVIDER": "fake",
            "RATE_LIMIT_ENABLED": "true" if rate_limits else "false",
            "CHAT_RATE_LIMIT_PER_MINUTE": str(chat_per_minute),
            "RATE_LIMIT_GLOBAL_PER_MINUTE": str(global_per_minute),
            "RATE_LIMIT_AUTH_PER_MINUTE": "100000",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "EMOTION_ANALYZER": "keyword",
        }
        if export_per_hour is not None:
            env["PRIVACY_EXPORT_PER_HOUR"] = str(export_per_hour)
        with _env(env):
            from app.core.config import Settings
            from app.db.session import build_database
            from app.main import create_app
            from app.models import Base

            settings = Settings(_env_file=None)
            previous = crypto._cipher
            crypto.configure_cipher(crypto.FernetCipher.from_settings(settings))
            database = build_database(settings)
            try:
                async with database.engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                app = create_app(settings, database=database)
                async with app.router.lifespan_context(app):
                    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://testserver", timeout=60
                    ) as client:
                        yield App(client=client, app=app)
            finally:
                await database.dispose()
                crypto.configure_cipher(previous)


__all__ = ["ALL_CONSENTS", "BACKEND_DIR", "REPO_ROOT", "App", "Person", "running_app"]
