"""The demo seed script: it must run end to end against the migrated schema.

The script lives in ``scripts/`` (outside the package), so it is imported by
path here — the same way a developer runs it.
"""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import FieldCipher
from app.db.repos import ChatRepository
from app.db.session import Database, async_database_url
from app.models import Base, Message, MessageRole, RiskLevel

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "seed_demo_data.py"

SEEDED_COUNTS = {
    "users": 1,
    "consents": 4,
    "chat_sessions": 1,
    "messages": 4,
    "mood_entries": 1,
    "journal_entries": 1,
    "assessment_results": 1,
    "safety_events": 2,
}


def _load() -> ModuleType:
    assert SCRIPT_PATH.exists(), f"missing {SCRIPT_PATH}"
    spec = importlib.util.spec_from_file_location("manovia_seed_demo_data", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _file(settings: Settings) -> Path:
    return Path(async_database_url(settings.database_url).database or "")


def test_the_script_is_documented_and_importable() -> None:
    module = _load()
    assert module.__doc__ is not None and "FIELD_ENCRYPTION_KEY" in module.__doc__
    assert callable(module.seed) and callable(module.main)


def test_demo_password_hash_is_self_describing() -> None:
    module = _load()
    digest = module.demo_password_hash("manovia-demo")
    algorithm, salt, derived = digest.split("$")
    assert algorithm == "scrypt"
    assert len(salt) == 32 and len(derived) == 64
    assert module.demo_password_hash("manovia-demo") != digest, "the salt must be random"
    assert "manovia-demo" not in digest


async def test_seed_creates_one_demo_user_with_a_row_in_every_table(
    database: Database, cipher: FieldCipher, session: AsyncSession
) -> None:
    module = _load()
    summary = await module.seed(None, False)
    assert summary["created"] is True
    assert summary["row_counts"] == SEEDED_COUNTS

    # The seeded conversation is readable through the repositories, so the
    # script and the application agree on the key and on the schema.
    chats = ChatRepository(session)
    chat = (await chats.list_for_user(uuid.UUID(summary["user_id"])))[0]
    messages = await chats.list_messages(chat.id)
    first = chats.message_text(next(iter(messages)))
    assert first.startswith("I have not slept properly")
    assert [message.risk_level for message in messages].count(int(RiskLevel.CRISIS)) == 2
    assert [message.role for message in messages][-1] is MessageRole.SYSTEM


async def test_the_seeded_words_are_not_readable_in_the_file(
    database: Database, cipher: FieldCipher, settings: Settings
) -> None:
    module = _load()
    await module.seed(None, False)
    raw = _file(settings).read_bytes()
    assert b"not slept properly" not in raw
    assert b"better off without me" not in raw
    connection = sqlite3.connect(_file(settings))
    try:
        blob = connection.execute("SELECT content_encrypted FROM messages LIMIT 1").fetchone()[0]
        users = connection.execute(
            "SELECT substr(password_hash, 1, 7) FROM users LIMIT 1"
        ).fetchone()[0]
    finally:
        connection.close()
    assert isinstance(blob, (bytes, bytearray)) and b"slept" not in bytes(blob)
    assert users == "scrypt$"


async def test_seed_is_idempotent_and_fresh_rebuilds_the_demo_user(
    database: Database, cipher: FieldCipher
) -> None:
    module = _load()
    first = await module.seed(None, False)
    second = await module.seed(None, False)
    assert second.get("created") is False and "note" in second
    assert second["user_id"] == first["user_id"]
    async with database.session_factory() as check:
        total = await check.execute(select(func.count()).select_from(Message))
        assert int(total.scalar_one()) == SEEDED_COUNTS["messages"]

    third = await module.seed(None, True)
    assert third["created"] is True and third["cascade_deleted"] is True
    assert third["user_id"] != first["user_id"], "the demo user was rebuilt"
    async with database.session_factory() as check:
        for table in Base.metadata.sorted_tables:
            rows = await check.execute(select(func.count()).select_from(table))
            expected = SEEDED_COUNTS[table.name]
            assert int(rows.scalar_one()) == expected, f"{table.name} was not rebuilt cleanly"


async def test_seed_refuses_to_run_without_an_encryption_key(
    database: Database, cipher: FieldCipher, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FIELD_ENCRYPTION_KEY", raising=False)
    module = _load()
    with pytest.raises(SystemExit, match="FIELD_ENCRYPTION_KEY is not set"):
        await module.seed(None, False)


async def test_seed_refuses_an_unmigrated_database(
    cipher: FieldCipher, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'unmigrated.db'}")
    module = _load()
    with pytest.raises(SystemExit, match="not migrated"):
        await module.seed(None, False)


async def test_an_explicit_database_url_overrides_the_environment(
    database: Database, cipher: FieldCipher, settings: Settings, tmp_path: Path
) -> None:
    """``--database-url`` must never touch the database in DATABASE_URL."""
    module = _load()
    copied = tmp_path / "explicit.db"
    shutil.copy(_file(settings), copied)  # the schema, none of the rows

    summary = await module.seed(f"sqlite:///{copied}", False)
    assert summary["created"] is True and summary["row_counts"] == SEEDED_COUNTS
    assert copied.name in summary["database"]
    async with database.session_factory() as check:
        rows = await check.execute(select(func.count()).select_from(Base.metadata.tables["users"]))
        assert int(rows.scalar_one()) == 0, "the environment database was left alone"
