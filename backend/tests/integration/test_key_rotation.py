"""Integration tests for Alembic 0004 Fernet->Envelope migration and key rotation."""

from __future__ import annotations

import importlib.util
import sqlite3
import uuid
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.core.config import Settings
from app.core.crypto import DecryptError, configure_cipher
from app.db.repos import ChatRepository, JournalRepository, MoodRepository, UserRepository
from app.db.session import Database
from app.models import MessageRole, RiskLevel
from app.services.journal.crypto import (
    EnvelopeCipher,
    generate_master_key,
    inspect_envelope_key_id,
    is_envelope_ciphertext,
    rotate_database_encryption,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
ROTATE_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts" / "rotate_encryption_keys.py"


def _load_rotate_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "manovia_rotate_encryption_keys", ROTATE_SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return config


def test_alembic_0004_migrates_legacy_fernet_rows_to_envelope_encryption(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading from 0003 to 0004 converts Day-3 Fernet ciphertexts into MENV envelopes."""
    raw_key = generate_master_key()
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", raw_key)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY_ID", "v1")
    configure_cipher(None)

    config = _alembic_config()
    command.upgrade(config, "0003")

    db_path = Path(sa.make_url(settings.database_url).database or "")
    fernet = Fernet(raw_key.encode("ascii"))
    user_id = uuid.uuid4()
    entry_id = uuid.uuid4()
    legacy_title = fernet.encrypt(b"Legacy Day 3 Title")
    legacy_body = fernet.encrypt(b"Legacy Day 3 Body written before envelope encryption.")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO users (id, email, is_anonymous, language, created_at) "
            "VALUES (?, ?, 0, 'en', '2026-10-11 00:00:00')",
            (user_id.hex, "legacy@example.test"),
        )
        conn.execute(
            "INSERT INTO journal_entries "
            "(id, user_id, title_encrypted, body_encrypted, sentiment, created_at) "
            "VALUES (?, ?, ?, ?, 0.2, '2026-10-11 00:00:00')",
            (entry_id.hex, user_id.hex, legacy_title, legacy_body),
        )
        conn.commit()
    finally:
        conn.close()

    # Now run alembic upgrade head (0003 -> 0004).
    command.upgrade(config, "head")

    conn = sqlite3.connect(db_path)
    try:
        user_row = conn.execute(
            "SELECT data_key_encrypted, data_key_id FROM users WHERE id = ?",
            (user_id.hex,),
        ).fetchone()
        journal_row = conn.execute(
            "SELECT title_encrypted, body_encrypted, key_id FROM journal_entries WHERE id = ?",
            (entry_id.hex,),
        ).fetchone()
    finally:
        conn.close()

    assert user_row is not None
    wrapped_dek, user_kid = user_row
    assert isinstance(wrapped_dek, bytes) and wrapped_dek.startswith(b"MDEK\x01")
    assert user_kid == "v1"

    assert journal_row is not None
    migrated_title, migrated_body, entry_kid = journal_row
    assert is_envelope_ciphertext(migrated_title)
    assert is_envelope_ciphertext(migrated_body)
    assert entry_kid == "v1"

    cipher = EnvelopeCipher(raw_key, default_key_id="v1")
    assert (
        cipher.decrypt_field(
            migrated_title, user_id=user_id, field_name="title", wrapped_dek=wrapped_dek
        )
        == "Legacy Day 3 Title"
    )
    assert (
        cipher.decrypt_field(
            migrated_body, user_id=user_id, field_name="body", wrapped_dek=wrapped_dek
        )
        == "Legacy Day 3 Body written before envelope encryption."
    )


async def test_key_rotation_re_encrypts_everything_and_retires_old_key(
    session: AsyncSession,
) -> None:
    """Rotating from v1 to v2 re-encrypts all records so v1 is no longer needed."""
    key_v1 = generate_master_key()
    key_v2 = generate_master_key()

    cipher_v1 = EnvelopeCipher(f"v1:{key_v1}")
    configure_cipher(cipher_v1)

    users = UserRepository(session)
    user_a = await users.create(email="alice@example.test")
    user_b = await users.create(email="bob@example.test")

    journals = JournalRepository(session)
    entry_a = await journals.create(
        user_id=user_a.id,
        title="Alice's entry",
        body="First paragraph in Alice's journal.",
        sentiment=0.4,
    )
    entry_b = await journals.create(
        user_id=user_b.id,
        title="Bob's entry",
        body="First paragraph in Bob's journal.",
        sentiment=-0.2,
    )
    moods = MoodRepository(session)
    mood_a = await moods.record(user_id=user_a.id, valence=4, energy=3, note="Feeling steadier.")
    chats = ChatRepository(session)
    chat_a = await chats.create(user_id=user_a.id)
    msg_a = await chats.add_message(
        session_id=chat_a.id,
        role=MessageRole.USER,
        content="Hello from chat",
        risk_level=RiskLevel.NONE,
    )
    await chats.update_memory(chat_a.id, "User greeted companion.")
    await session.commit()

    old_title_ct = bytes(entry_a.title_encrypted)
    old_body_ct = bytes(entry_a.body_encrypted)
    assert inspect_envelope_key_id(old_title_ct) == "v1"
    assert entry_a.key_id == "v1"
    assert user_a.data_key_id == "v1"

    # Rotate using keyring with v2 active and v1 historical.
    cipher_rotating = EnvelopeCipher(f"v2:{key_v2},v1:{key_v1}")
    configure_cipher(cipher_rotating)
    report = await rotate_database_encryption(session, cipher_rotating)
    await session.commit()

    assert report.active_key_id == "v2"
    assert report.users_rotated == 2
    assert report.journal_entries_rotated == 2
    assert report.mood_entries_rotated == 1
    assert report.chat_sessions_rotated == 1
    assert report.messages_rotated == 1

    await session.refresh(user_a)
    await session.refresh(entry_a)
    await session.refresh(entry_b)
    await session.refresh(mood_a)
    await session.refresh(msg_a)

    assert user_a.data_key_id == "v2"
    assert entry_a.key_id == "v2"
    assert entry_b.key_id == "v2"
    assert entry_a.title_encrypted != old_title_ct
    assert entry_a.body_encrypted != old_body_ct
    assert inspect_envelope_key_id(entry_a.title_encrypted) == "v2"
    assert inspect_envelope_key_id(entry_a.body_encrypted) == "v2"

    # Remove v1 completely: configure cipher with ONLY v2.
    cipher_v2_only = EnvelopeCipher(f"v2:{key_v2}")
    configure_cipher(cipher_v2_only)

    assert journals.title(entry_a) == "Alice's entry"
    assert journals.body(entry_a) == "First paragraph in Alice's journal."
    assert journals.title(entry_b) == "Bob's entry"
    assert journals.body(entry_b) == "First paragraph in Bob's journal."
    assert moods.note(mood_a) == "Feeling steadier."
    assert chats.message_text(msg_a) == "Hello from chat"
    mem, _ = await chats.memory_summary(chat_a.id)
    assert mem == "User greeted companion."

    # Old v1 cipher alone can no longer read any rotated entry.
    configure_cipher(cipher_v1)
    with pytest.raises(DecryptError):
        journals.title(entry_a)
    with pytest.raises(DecryptError):
        journals.body(entry_a)


async def test_rotate_encryption_keys_cli_script_end_to_end(
    database: Database, settings: Settings, session: AsyncSession
) -> None:
    """The CLI script rotates keys on disk and retires the old key id."""
    key_v1 = generate_master_key()
    key_v2 = generate_master_key()

    configure_cipher(EnvelopeCipher(f"v1:{key_v1}"))
    user = await UserRepository(session).create(email="cli@example.test")
    entry = await JournalRepository(session).create(
        user_id=user.id,
        title="CLI rotation check",
        body="This entry will be rotated by scripts/rotate_encryption_keys.py.",
    )
    await session.commit()
    entry_id = entry.id

    module = _load_rotate_script()
    summary = await module.rotate(
        database_url=settings.database_url,
        new_key=f"v2:{key_v2}",
        old_key=f"v1:{key_v1}",
    )
    assert summary["active_key_id"] == "v2"
    assert summary["users_rotated"] >= 1
    assert summary["journal_entries_rotated"] == 1

    # Now configure ONLY v2 and read back from a fresh session.
    configure_cipher(EnvelopeCipher(f"v2:{key_v2}"))
    async with database.session_factory() as check_session:
        reloaded = await JournalRepository(check_session).get(entry_id)
        assert reloaded is not None
        assert reloaded.key_id == "v2"
        assert JournalRepository.title(reloaded) == "CLI rotation check"
        assert (
            JournalRepository.body(reloaded)
            == "This entry will be rotated by scripts/rotate_encryption_keys.py."
        )
