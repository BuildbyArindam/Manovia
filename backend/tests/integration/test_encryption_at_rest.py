"""Encrypted at rest: what the database file actually holds.

The point of these tests is not "the repository calls encrypt" — it is that a
stolen ``manovia.db`` (or a backup, or a copy attached to a ticket) does not
contain a person's words. So each test writes through the normal path, then
reads the file back with the plain ``sqlite3`` module, with no ORM and no key.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import DecryptError, FernetCipher, FieldCipher
from app.db.repos import ChatRepository, JournalRepository, MoodRepository
from app.models import MessageRole, RiskLevel, User

SENTINEL = "I heard the car horn and then I could not breathe at all."
NEEDLE = "could not breathe"

# table -> the column that is supposed to hold ciphertext
ENCRYPTED_LOOKUPS: dict[str, str] = {
    "messages": "content_encrypted",
    "journal_entries": "title_encrypted",
    "mood_entries": "note_encrypted",
}


def _path(settings: Settings) -> Path:
    return Path(sa.make_url(settings.database_url).database or "")


def _raw_column(database: Path, table: str, column: str) -> list[object]:
    """Read one column straight off the file, bypassing the app completely."""
    connection = sqlite3.connect(database)
    try:
        return [row[0] for row in connection.execute(f"SELECT {column} FROM {table}")]
    finally:
        connection.close()


async def _write_all_three(session: AsyncSession, user: User) -> None:
    """One encrypted row in each of the three free-text tables."""
    chat = await ChatRepository(session).create(user_id=user.id)
    await ChatRepository(session).add_message(
        session_id=chat.id,
        role=MessageRole.USER,
        content=SENTINEL,
        risk_level=RiskLevel.ELEVATED,
        emotion="panicked",
    )
    await JournalRepository(session).create(
        user_id=user.id, title=SENTINEL, body=SENTINEL, sentiment=-0.4
    )
    await MoodRepository(session).record(
        user_id=user.id,
        valence=1,
        energy=2,
        emotions=["panicked"],
        factors={"sleep": 1},
        note=SENTINEL,
    )
    await session.commit()


async def test_the_database_file_contains_no_plaintext(
    session: AsyncSession, demo_user: User, settings: Settings
) -> None:
    await _write_all_three(session, demo_user)
    database = _path(settings)
    # The whole file, not only the columns we wrote to: no plaintext anywhere,
    # including in journal rows and in any page SQLite left behind.
    raw = database.read_bytes()
    assert NEEDLE.encode() not in raw
    assert SENTINEL.encode() not in raw
    for table, column in ENCRYPTED_LOOKUPS.items():
        blobs = _raw_column(database, table, column)
        assert blobs, f"{table}.{column} was not written"
        for blob in blobs:
            assert isinstance(blob, bytes)
            assert NEEDLE.encode() not in blob
            assert blob != SENTINEL.encode()
            assert len(blob) > len(SENTINEL), "ciphertext should not look like the input"


async def test_the_text_reads_back_through_the_repositories(
    session: AsyncSession, demo_user: User, cipher: FieldCipher
) -> None:
    await _write_all_three(session, demo_user)
    chats = ChatRepository(session)
    chat = (await chats.list_for_user(demo_user.id, limit=1))[0]
    message = (await chats.list_messages(chat.id))[0]
    assert chats.message_text(message) == SENTINEL

    journals = JournalRepository(session)
    entry = (await journals.list_for_user(demo_user.id))[0]
    assert (journals.title(entry), journals.body(entry)) == (SENTINEL, SENTINEL)

    moods = MoodRepository(session)
    mood = (await moods.list_for_user(demo_user.id))[0]
    assert moods.note(mood) == SENTINEL


async def test_a_different_key_cannot_read_the_blob(
    session: AsyncSession, demo_user: User, cipher: FieldCipher
) -> None:
    """Failure is loud: a wrong key raises, it does not return an empty string."""
    await _write_all_three(session, demo_user)
    chats = ChatRepository(session)
    chat = (await chats.list_for_user(demo_user.id, limit=1))[0]
    message = (await chats.list_messages(chat.id))[0]
    stranger = FernetCipher(FernetCipher.generate_key())
    with pytest.raises(DecryptError):
        stranger.decrypt(message.content_encrypted)


async def test_only_the_words_are_encrypted(
    session: AsyncSession, demo_user: User, settings: Settings
) -> None:
    """Risk triage stays queryable in SQL; that split is deliberate.

    ``risk_level``, ``role`` and ``emotion`` have to be filterable and
    aggregatable without a key, so they are stored in the clear. A message's
    words never are.
    """
    await _write_all_three(session, demo_user)
    connection = sqlite3.connect(_path(settings))
    try:
        row = connection.execute(
            "SELECT risk_level, role, emotion, typeof(content_encrypted) FROM messages"
        ).fetchone()
    finally:
        connection.close()
    assert row == (int(RiskLevel.ELEVATED), "user", "panicked", "blob")


async def test_an_absent_note_stays_null_not_empty(
    session: AsyncSession, demo_user: User, settings: Settings, cipher: FieldCipher
) -> None:
    await MoodRepository(session).record(user_id=demo_user.id, valence=4, energy=4)
    await session.commit()
    assert _raw_column(_path(settings), "mood_entries", "note_encrypted") == [None]
    mood = (await MoodRepository(session).list_for_user(demo_user.id))[0]
    assert MoodRepository.note(mood) is None


def test_an_empty_string_round_trips(cipher: FieldCipher) -> None:
    """An empty note is a real value, distinct from "no note"."""
    assert cipher.decrypt(cipher.encrypt("")) == ""
