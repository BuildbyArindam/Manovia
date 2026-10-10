"""Shared pytest fixtures: isolated settings, an in-process HTTP client, a database.

Everything here is hermetic: settings come from monkeypatched environment
variables (never a developer's ``.env``), the database is a per-test SQLite
file, and the field cipher uses a key generated for that test only. No network,
no PostgreSQL server, no committed key material.
"""

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import Settings
from app.db.repos import UserRepository
from app.db.session import Database, build_database
from app.main import create_app
from app.models import Base, User

_ENV_KEYS = (
    "APP_ENV",
    "SECRET_KEY",
    "DATABASE_URL",
    "DB_ECHO",
    "LLM_PROVIDER",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "OLLAMA_BASE_URL",
    "FIELD_ENCRYPTION_KEY",
    "ALLOWED_ORIGINS",
    "LOG_LEVEL",
    "PASSWORD_MIN_LENGTH",
    "PASSWORD_MAX_LENGTH",
    "JWT_ACCESS_MINUTES",
    "JWT_REFRESH_DAYS",
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_AUTH_PER_MINUTE",
    "RATE_LIMIT_GLOBAL_PER_MINUTE",
    "LOGIN_MAX_FAILURES",
    "LOGIN_LOCKOUT_SECONDS",
    "LOGIN_LOCKOUT_MAX_SECONDS",
    # NLP / emotion analysis (Day 6). EMOTION_MODEL_ID matters most: a test
    # must never reach the network because a developer's .env named a model.
    "EMOTION_ANALYZER",
    "EMOTION_MODEL_ID",
    "EMOTION_DEVICE",
    "EMOTION_MAX_LENGTH",
    "EMOTION_BATCH_SIZE",
    "EMOTION_CACHE_SIZE",
    "EMOTION_FAILURE_THRESHOLD",
    "EMOTION_COOLDOWN_SECONDS",
    "EMOTION_SLOW_MS",
    # Model downloads must never happen inside the test suite.
    "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
    # Safety ML ensemble (Day 9). Tests pin the ML path explicitly; a
    # developer's .env must not flip the rules-only expectations.
    "SAFETY_ML_ENABLED",
    "SAFETY_ML_MIN_CONFIDENCE",
    "SAFETY_ML_CRISIS_MASS_FLOOR",
    "SAFETY_ML_SUSPICION_FLOOR",
    "SAFETY_ML_ARTIFACT_DIR",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer's configuration.

    Two sources have to be neutralised: the process environment (deleted) and
    the ``.env`` files ``Settings`` would otherwise read (disabled). Code that
    builds its own ``Settings()`` — the readiness endpoint, ``alembic/env.py``,
    the seed script — would otherwise pick up a local ``.env`` and quietly change
    what a test proves.
    """
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def isolate_cipher() -> Iterator[None]:
    """Save and restore the process-wide cipher around every test (it is global)."""
    previous = crypto._cipher
    crypto.configure_cipher(None)
    yield
    crypto.configure_cipher(previous)


@pytest.fixture
def settings(clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """Valid test settings pointed at a private SQLite file in ``tmp_path``."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", crypto.FernetCipher.generate_key())
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://testserver")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'manovia-test.db'}")
    # The Day 8 integration tests pin exact levels for pinned texts; the ML
    # raise path is exercised by its own tests, which flip this on.
    monkeypatch.setenv("SAFETY_ML_ENABLED", "false")
    return Settings(_env_file=None)


@pytest.fixture(autouse=True)
def offline_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt and braces: the suite must never talk to the Hugging Face hub.

    ``build_analyzer`` is lazy, so an accidental model load would only happen
    when a test calls the dev endpoint. These two flags make any such call fail
    fast offline instead of hanging on a DNS lookup.
    """
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture
def cipher(settings: Settings) -> crypto.FieldCipher:
    """Install the test cipher, the way application startup does."""
    configured = crypto.FernetCipher.from_settings(settings)
    assert configured is not None  # settings always carry a key
    crypto.configure_cipher(configured)
    return configured


@pytest_asyncio.fixture
async def database(settings: Settings, cipher: crypto.FieldCipher) -> AsyncIterator[Database]:
    """A test database whose schema comes from the models' metadata.

    A real environment runs ``alembic upgrade head``; the tables here are built
    from the same ``Base.metadata`` Alembic compares itself against, and
    ``tests/integration/test_migrations.py`` proves the migration and the models
    do not drift.
    """
    built = build_database(settings)
    async with built.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield built
    await built.dispose()


@pytest_asyncio.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    """One async session. The test owns the transaction (commit/rollback)."""
    async with database.session_factory() as db_session:
        yield db_session


@pytest_asyncio.fixture
async def demo_user(session: AsyncSession) -> User:
    """A user row to hang child records off."""
    user = await UserRepository(session).create(
        email="someone@example.test", is_anonymous=False, language="en", region="US"
    )
    await session.commit()
    return user


@pytest_asyncio.fixture
async def app(settings: Settings, database: Database) -> FastAPI:
    """A fresh application wired to the test database."""
    return create_app(settings, database=database)


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Async HTTP client wired straight to the app (no network involved)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def client_factory(
    database: Database, settings: Settings, cipher: crypto.FieldCipher
) -> AsyncIterator[Callable[..., httpx.AsyncClient]]:
    """Build clients for apps with overridden settings (rate limits, lockout).

    ``factory(rate_limit_auth_per_minute=3)`` creates a fresh application over
    the same test database with those ``Settings`` fields replaced, and hands
    back a client for it. All clients are closed when the test ends.
    """

    clients: list[httpx.AsyncClient] = []

    def factory(**overrides: Any) -> httpx.AsyncClient:
        custom = settings.model_copy(update=overrides) if overrides else settings
        built = create_app(custom, database=database)
        ac = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=built), base_url="http://testserver"
        )
        clients.append(ac)
        return ac

    yield factory
    for ac in clients:
        await ac.aclose()
