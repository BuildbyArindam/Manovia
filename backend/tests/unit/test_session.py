"""Engine/session plumbing: URL normalisation, pooling, and the readiness probe."""

from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.db.session import (
    Database,
    async_database_url,
    build_database,
    check_database,
    create_db_engine,
    redact_url,
)


def test_sqlite_urls_gain_the_aiosqlite_driver() -> None:
    assert async_database_url("sqlite:///./manovia.db").drivername == "sqlite+aiosqlite"
    assert async_database_url("sqlite+aiosqlite:///./manovia.db").drivername == "sqlite+aiosqlite"
    assert async_database_url("sqlite://").database is None


def test_postgres_urls_gain_the_asyncpg_driver_and_keep_their_parts() -> None:
    url = async_database_url("postgres://manovia:secret@db.internal:5432/manovia")
    assert url.drivername == "postgresql+asyncpg"
    assert url.host == "db.internal"
    assert url.port == 5432
    assert url.database == "manovia"
    # The password survives (a migration must be able to connect) ...
    assert url.render_as_string(hide_password=False).count("secret") == 1
    # ... and is hidden from anything printable.
    assert "secret" not in url.render_as_string(hide_password=True)


def test_unsupported_backends_are_refused() -> None:
    with pytest.raises(ValueError, match="Unsupported DATABASE_URL backend"):
        async_database_url("mysql+pymysql://localhost/app")


def test_redact_url_hides_credentials() -> None:
    assert redact_url("postgresql+asyncpg://u:p@h/db") == "postgresql+asyncpg://u:***@h/db"


def test_in_memory_sqlite_shares_one_connection(tmp_path: Path) -> None:
    engine = create_db_engine("sqlite:///:memory:")
    assert engine.pool.__class__.__name__ == "StaticPool"
    file_engine = create_db_engine(f"sqlite:///{tmp_path / 'file.db'}")
    assert file_engine.pool.__class__.__name__ != "StaticPool"


async def test_sqlite_foreign_keys_are_enforced_on_every_connection(tmp_path: Path) -> None:
    """Without the PRAGMA, ON DELETE CASCADE would silently do nothing in dev."""
    engine = create_db_engine(f"sqlite:///{tmp_path / 'fk.db'}")
    try:
        async with engine.connect() as connection:
            enabled = (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()
        assert int(enabled) == 1
    finally:
        await engine.dispose()


async def test_check_database_reports_a_working_database(tmp_path: Path) -> None:
    database = build_database(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path}/x.db"))
    try:
        assert await check_database(database) is True
    finally:
        await database.dispose()


async def test_check_database_reports_an_unreachable_database(tmp_path: Path) -> None:
    # A file inside a directory that does not exist cannot be opened.
    missing = tmp_path / "nope" / "manovia.db"
    database = build_database(Settings(_env_file=None, database_url=f"sqlite:///{missing}"))
    try:
        assert await check_database(database) is False
    finally:
        await database.dispose()


async def test_database_check_raises_for_the_caller_that_wants_the_error(
    database: Database,
) -> None:
    await database.check()
    async with database.engine.connect() as connection:
        assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1


def test_describe_never_shows_a_password() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://manovia:supersecret@localhost:5432/manovia",
    )
    assert "supersecret" not in build_database(settings).describe()
