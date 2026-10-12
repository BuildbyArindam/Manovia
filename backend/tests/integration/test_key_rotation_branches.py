"""Key-rotation branches that only exist when the database is not pristine.

``rotate_database_encryption`` is the code somebody runs once, under pressure,
against a live database, with the old key about to be removed. The happy path
(two users, two journal entries, one mood note, one chat) is covered by
``tests/integration/test_key_rotation.py``. What is left here are the states a
real database is actually in when a rotation runs:

* a user row from before Day 17 that has **no** ``data_key_encrypted`` yet, so
  the rotator has to mint one instead of unwrapping one;
* a journal entry still stored as a Day-3 **Fernet** blob (the 0004 migration
  upgraded rows, but rows written by an older process can still be there);
* a journal entry that is re-read after rotation, to prove the *new* wrapped DEK
  is what the row is bound to (a rotation that writes rows nobody can read is
  worse than no rotation);

Each test commits real rows into the real schema and then asserts on what the
database holds — not on the report object alone.
"""

from __future__ import annotations

import uuid

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import configure_cipher
from app.db.repos import ChatRepository, JournalRepository, MoodRepository
from app.models import MessageRole, RiskLevel, User
from app.services.journal.crypto import (
    EnvelopeCipher,
    generate_master_key,
    inspect_envelope_key_id,
    is_envelope_ciphertext,
    rotate_database_encryption,
)


async def test_rotation_mints_a_dek_for_a_user_that_has_none(session: AsyncSession) -> None:
    """A pre-Day-17 user row has ``data_key_encrypted IS NULL``; rotation fills it.

    This is the branch that decides whether the migration works on the *oldest*
    accounts — the ones whose journal entries matter most. If it raised instead,
    the only fix would be to hand-write rows for people who have been here
    longest.
    """
    key_v1 = generate_master_key()
    key_v2 = generate_master_key()

    legacy_user = User(
        id=uuid.uuid4(),
        email="pre-day17@example.test",
        is_anonymous=False,
        language="en",
        region="US",
    )
    session.add(legacy_user)
    await session.commit()
    assert legacy_user.data_key_encrypted is None
    assert legacy_user.data_key_id is None

    cipher = EnvelopeCipher(f"v2:{key_v2},v1:{key_v1}")
    configure_cipher(cipher)
    report = await rotate_database_encryption(session, cipher)
    await session.commit()

    refreshed = (await session.execute(select(User).where(User.id == legacy_user.id))).scalar_one()
    assert report.users_rotated == 1
    assert refreshed.data_key_encrypted is not None
    assert refreshed.data_key_id == "v2"
    assert inspect_envelope_key_id(refreshed.data_key_encrypted) == "v2"
    # The minted DEK really is the user's key: it unwraps for this user id.
    dek, _, kid = cipher.get_or_create_user_key(
        refreshed.id, existing_wrapped_dek=refreshed.data_key_encrypted
    )
    assert kid == "v2" and len(dek) == 32


async def test_rotation_upgrades_legacy_fernet_journal_rows(
    session: AsyncSession, demo_user: User, settings: Settings
) -> None:
    """Journal rows written by the Day-3 cipher are re-encrypted, not skipped.

    ``decrypt_legacy_fernet`` tries every key in the ring. A rotator that only
    understood ``MENV`` blobs would leave these rows on the old key forever —
    and the reason to rotate is usually that the old key is being retired.
    """
    key_v2 = generate_master_key()
    v1_key = settings.field_encryption_key
    assert v1_key is not None

    # Write the row the Day-3 way: Fernet with the not-yet-upgraded key.
    legacy_fernet = Fernet(v1_key.encode("ascii"))
    entry = await JournalRepository(session).create(
        user_id=demo_user.id,
        title="Legacy title",
        body="Legacy body written before envelope encryption.",
        sentiment=0.1,
    )
    entry.title_encrypted = legacy_fernet.encrypt(b"Legacy title")
    entry.body_encrypted = legacy_fernet.encrypt(b"Legacy body written before envelope encryption.")
    entry.key_id = "legacy-fernet"
    await session.commit()
    assert not is_envelope_ciphertext(entry.title_encrypted)

    cipher = current_keyring(settings, extra_key=key_v2)
    configure_cipher(cipher)
    report = await rotate_database_encryption(session, cipher)
    await session.commit()

    rotated = await JournalRepository(session).get(entry.id)
    assert rotated is not None
    assert report.journal_entries_rotated == 1
    assert is_envelope_ciphertext(rotated.title_encrypted)
    assert is_envelope_ciphertext(rotated.body_encrypted)
    assert inspect_envelope_key_id(rotated.title_encrypted) == "v2"
    assert rotated.key_id == "v2"

    # Read them back through the *rotated* user DEK and a v2-only keyring: the
    # legacy key is not in this ring, so a successful read is the proof that the
    # row no longer needs it.
    v2_only = EnvelopeCipher(f"v2:{key_v2}")
    refreshed_user = (
        await session.execute(select(User).where(User.id == demo_user.id))
    ).scalar_one()
    assert (
        v2_only.decrypt_field(
            rotated.title_encrypted,
            user_id=demo_user.id,
            field_name="title",
            wrapped_dek=refreshed_user.data_key_encrypted,
        )
        == "Legacy title"
    )
    assert (
        v2_only.decrypt_field(
            rotated.body_encrypted,
            user_id=demo_user.id,
            field_name="body",
            wrapped_dek=refreshed_user.data_key_encrypted,
        )
        == "Legacy body written before envelope encryption."
    )


async def test_rotation_re_encrypts_every_ciphertext_column(
    session: AsyncSession, demo_user: User, settings: Settings
) -> None:
    """The report counts are the row counts, and every counted row now reads under v2.

    A rotation report that says "5 messages rotated" while one column still
    needs the old key is the failure mode this test exists to catch: it reads
    every column back with a keyring that does **not** contain the old key.
    """
    key_v2 = generate_master_key()
    configure_cipher(current_keyring(settings))

    journals = JournalRepository(session)
    entry = await journals.create(user_id=demo_user.id, title="T", body="B", sentiment=0.0)
    moods = MoodRepository(session)
    await moods.record(user_id=demo_user.id, valence=3, energy=3, note="a note")
    chats = ChatRepository(session)
    chat = await chats.create(user_id=demo_user.id)
    await chats.add_message(
        session_id=chat.id,
        role=MessageRole.USER,
        content="hello there",
        risk_level=RiskLevel.NONE,
    )
    await chats.update_memory(chat.id, "User said hello.")
    await session.commit()

    cipher = current_keyring(settings, extra_key=key_v2)
    configure_cipher(cipher)
    report = await rotate_database_encryption(session, cipher, rekey_user_deks=False)
    await session.commit()

    assert (report.users_rotated, report.journal_entries_rotated) == (1, 1)
    assert (report.mood_entries_rotated, report.chat_sessions_rotated) == (1, 1)
    assert report.messages_rotated == 1

    # The old master key is gone from the environment now: everything must read.
    v2_only = EnvelopeCipher(f"v2:{key_v2}")
    configure_cipher(v2_only)
    refreshed_user = (
        await session.execute(select(User).where(User.id == demo_user.id))
    ).scalar_one()
    wrapped = refreshed_user.data_key_encrypted
    assert (
        v2_only.decrypt_field(
            (await journals.get(entry.id)).body_encrypted,  # type: ignore[union-attr]
            user_id=demo_user.id,
            field_name="body",
            wrapped_dek=wrapped,
        )
        == "B"
    )
    stored_mood = (await moods.list_for_user(demo_user.id))[0]
    assert v2_only.decrypt(stored_mood.note_encrypted) == "a note"  # type: ignore[arg-type]
    stored_session = await chats.get(chat.id)
    assert stored_session is not None
    assert v2_only.decrypt(stored_session.memory_encrypted) == "User said hello."  # type: ignore[arg-type]
    messages = await chats.list_messages(chat.id)
    assert v2_only.decrypt(messages[0].content_encrypted) == "hello there"


async def test_rotation_without_rekey_keeps_the_same_dek(
    session: AsyncSession, demo_user: User, settings: Settings
) -> None:
    """``rekey_user_deks=False`` re-wraps the master key, not the data key.

    Used when a hosted KMS rotation changes the wrapping key only. The DEK must
    survive, otherwise every ciphertext written under the old DEK becomes
    unreadable — the exact opposite of what a "wrap-only" rotation promises.
    """
    key_v2 = generate_master_key()
    cipher_v1 = current_keyring(settings)
    configure_cipher(cipher_v1)

    journals = JournalRepository(session)
    entry = await journals.create(user_id=demo_user.id, title="T", body="B", sentiment=0.0)
    await session.commit()
    original_dek, original_wrapped, _ = cipher_v1.get_or_create_user_key(demo_user.id)
    session_user = (await session.execute(select(User).where(User.id == demo_user.id))).scalar_one()
    session_user.data_key_encrypted = original_wrapped
    await session.commit()

    cipher = current_keyring(settings, extra_key=key_v2)
    configure_cipher(cipher)
    await rotate_database_encryption(session, cipher, rekey_user_deks=False)
    await session.commit()

    refreshed = (await session.execute(select(User).where(User.id == demo_user.id))).scalar_one()
    dek_after, _, kid_after = cipher.get_or_create_user_key(
        refreshed.id, existing_wrapped_dek=refreshed.data_key_encrypted
    )
    assert kid_after == "v2"
    assert dek_after == original_dek
    assert refreshed.data_key_id == "v2"
    rotated = await journals.get(entry.id)
    assert rotated is not None
    assert (
        cipher.decrypt_field(
            rotated.body_encrypted,
            user_id=demo_user.id,
            field_name="body",
            wrapped_dek=refreshed.data_key_encrypted,
        )
        == "B"
    )


def current_keyring(settings: Settings, *, extra_key: str | None = None) -> EnvelopeCipher:
    """The cipher the *fixtures* already used, optionally with a new active key.

    ``settings.field_encryption_key`` is the key the ``demo_user`` fixture wrote
    its wrapped DEK under, so a rotation test has to carry that exact key as the
    retired one — otherwise the first unwrap fails for a reason that has nothing
    to do with the branch under test (which is what happened on the first draft
    of this file, and why the helper exists).
    """
    v1 = settings.field_encryption_key
    assert v1 is not None
    spec = f"v1:{v1}" if extra_key is None else f"v2:{extra_key},v1:{v1}"
    return EnvelopeCipher(spec)


async def test_rotation_handles_a_journal_row_whose_owner_is_gone(
    session: AsyncSession, settings: Settings
) -> None:
    """A row left behind by a pre-foreign-key database still gets rotated.

    ``journal_entries.user_id`` is a foreign key with ``ON DELETE CASCADE``
    today, so an ownerless row cannot be created through the ORM — but a
    database restored from a pre-2026 dump, or written while FKs were off, can
    contain one. The rotator's fallback mints a DEK for the unknown owner rather
    than crashing halfway through a rotation that has already rewritten user
    rows (a half-rotated database is the worst possible outcome, because neither
    key alone can read it).
    """
    from sqlalchemy import text

    key_v2 = generate_master_key()
    configure_cipher(current_keyring(settings))

    orphan_id = uuid.uuid4()
    await session.commit()
    # Simulate the legacy state: no FK enforcement for this insert.
    await session.execute(text("PRAGMA foreign_keys=OFF"))
    await session.execute(
        text(
            "INSERT INTO journal_entries"
            " (id, user_id, title_encrypted, body_encrypted, sentiment, key_id,"
            " created_at, updated_at) VALUES"
            " (:id, :user_id, :title, :body, 0.0, 'v1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            # sa.Uuid() stores 32-char hex on SQLite, so the raw insert has to
            # match that form or the ORM's later UPDATE matches zero rows.
            "id": orphan_id.hex,
            "user_id": orphan_id.hex,
            "title": current_keyring(settings).encrypt_field(
                "orphan title", user_id=orphan_id, field_name="title"
            ),
            "body": current_keyring(settings).encrypt_field(
                "orphan body", user_id=orphan_id, field_name="body"
            ),
        },
    )
    await session.commit()
    await session.execute(text("PRAGMA foreign_keys=ON"))

    cipher = current_keyring(settings, extra_key=key_v2)
    configure_cipher(cipher)
    report = await rotate_database_encryption(session, cipher)
    await session.commit()
    assert report.journal_entries_rotated == 1
