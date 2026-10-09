"""Async engine and session plumbing.

One small seam keeps the rest of the app honest about which driver it uses:
SQLite (``sqlite+aiosqlite``) for development and tests, PostgreSQL
(``postgresql+asyncpg``) in production. Callers write the plain
``sqlite://``/``postgres://`` URL in ``DATABASE_URL`` and
:func:`async_database_url` upgrades the driver, so a local URL and a deployed
URL look the same in ``.env``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import event, make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.config import Settings

# Backend name -> the asyncio driver this app requires. "postgres" is the
# legacy alias most hosting providers hand out, so it is accepted here too.
ASYNC_DRIVERS: dict[str, str] = {
    "sqlite": "sqlite+aiosqlite",
    "postgresql": "postgresql+asyncpg",
    "postgres": "postgresql+asyncpg",
}

SUPPORTED_BACKENDS: tuple[str, ...] = tuple(ASYNC_DRIVERS)


def async_database_url(database_url: str) -> URL:
    """Parse ``database_url`` and switch to the asyncio driver for its backend."""
    url = make_url(database_url)
    backend = url.get_backend_name()
    expected = ASYNC_DRIVERS.get(backend, "")
    if not expected:
        raise ValueError(
            f"Unsupported DATABASE_URL backend {backend!r}; expected one of {SUPPORTED_BACKENDS}"
        )
    if url.drivername != expected:
        url = url.set(drivername=expected)
    return url


def redact_url(database_url: str) -> str:
    """A printable form of ``database_url`` with any password removed."""
    return make_url(database_url).render_as_string(hide_password=True)


def _enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """Turn on FK enforcement for every SQLite connection.

    SQLite ignores ``ON DELETE CASCADE`` (and FK constraints in general) unless
    ``PRAGMA foreign_keys`` is set per connection, so without this the cascade
    behaviour of the schema would be silently untested in development.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, _record: Any) -> None:
        # The adapter's own execute() opens, runs and closes a cursor for us.
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


def create_db_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an :class:`AsyncEngine` for a SQLite or PostgreSQL URL."""
    url = async_database_url(database_url)
    kwargs: dict[str, Any] = {"echo": echo}
    if url.get_backend_name() == "sqlite":
        # aiosqlite keeps a connection in its own thread; an in-memory database
        # must be shared through one connection or every session gets its own.
        kwargs["connect_args"] = {"check_same_thread": False}
        if not url.database or ":memory:" in url.database:
            kwargs["poolclass"] = StaticPool
        engine = create_async_engine(url, **kwargs)
        _enable_sqlite_foreign_keys(engine)
        return engine
    return create_async_engine(url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory used by the API and the scripts.

    ``expire_on_commit=False`` lets a request keep reading the objects it just
    wrote (ids and server defaults included) without a second round trip.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


@dataclass(slots=True)
class Database:
    """The engine plus its session factory, owned by the application."""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    async def check(self) -> None:
        """Raise if the database cannot answer a trivial query."""
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        """Close every pooled connection (called on application shutdown)."""
        await self.engine.dispose()

    def describe(self) -> str:
        """Printable identity of the target database, never a password."""
        return redact_url(str(self.engine.url))


def build_database(settings: Settings) -> Database:
    """Build the :class:`Database` described by ``settings.database_url``."""
    engine = create_db_engine(settings.database_url, echo=settings.db_echo)
    return Database(engine=engine, session_factory=create_session_factory(engine))


async def check_database(database: Database) -> bool:
    """Readiness helper: ``True`` when the database answers, ``False`` otherwise."""
    try:
        await database.check()
    except Exception as exc:  # readiness must never raise
        structlog.get_logger().warning(
            "database_unreachable", error_type=type(exc).__name__, database=database.describe()
        )
        return False
    return True
