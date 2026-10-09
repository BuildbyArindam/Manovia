"""Alembic environment: async engine, SQLite (dev/tests) and PostgreSQL (prod).

The target URL always comes from ``DATABASE_URL`` via
:class:`app.core.config.Settings`, so a migration can never be applied to a
database the application is not talking to. ``Settings`` is constructed here
rather than through the cached ``get_settings()`` so that a CLI invocation sees
the environment of the shell that called it.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import context
from app.core.config import Settings
from app.db.session import async_database_url, create_db_engine
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """The asyncio URL for this run, password included (never printed)."""
    return async_database_url(Settings().database_url).render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # compare_type catches column type drift; compare_server_default is
        # left off because SQLite and PostgreSQL render defaults differently
        # and the noise would hide real drift.
        compare_type=True,
        # SQLite cannot ALTER most things in place; batch mode rebuilds tables.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations through the same engine factory the app uses.

    That also gives SQLite its ``PRAGMA foreign_keys`` on every connection, so
    a migration that relies on FK behaviour behaves here exactly as it does in
    the running application.
    """
    engine = create_db_engine(Settings().database_url)
    # Alembic's env.py is synchronous; asyncio.run() is the documented way to
    # drive an async engine from it. Never call this from a running loop.
    asyncio.run(run_async_migrations(engine))


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
