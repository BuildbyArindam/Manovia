"""Application lifecycle: what startup configures and what shutdown releases."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.crypto import CipherNotConfiguredError, get_cipher
from app.db.session import Database, check_database
from app.main import create_app


async def test_startup_configures_the_cipher_for_the_whole_process(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    create_app(settings)
    assert get_cipher().encrypt("hello") != b"hello"
    # create_app is the only place the cipher is installed for the API process.
    assert "field_encryption_key_missing" not in capsys.readouterr().out


async def test_startup_warns_and_refuses_when_no_key_is_configured(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """No key means no writes, and the log line says why (without a value)."""
    keyless = settings.model_copy(update={"field_encryption_key": None})
    create_app(keyless)
    with pytest.raises(CipherNotConfiguredError):
        get_cipher()
    logged = capsys.readouterr().out
    assert "field_encryption_key_missing" in logged
    assert "test-secret-key" not in logged


async def test_shutdown_disposes_the_engine(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    disposed: list[AsyncEngine] = []
    original = Database.dispose

    async def spy(self: Database) -> None:
        disposed.append(self.engine)
        await original(self)

    monkeypatch.setattr(Database, "dispose", spy)
    app = create_app(settings)
    database: Database = app.state.db
    async with app.router.lifespan_context(app):
        # While the app is up, the readiness probe's query works.
        assert await check_database(database) is True
    assert disposed == [database.engine]
    # A disposed pool is replaced, so the same engine can serve requests again.
    assert await check_database(database) is True
