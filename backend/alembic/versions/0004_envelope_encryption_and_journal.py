"""envelope encryption per-user data keys and journal metadata

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11

Day 17:
1. Adds ``users.data_key_encrypted`` and ``users.data_key_id`` to store each
   user's random 256-bit AES-GCM data key wrapped by ``FIELD_ENCRYPTION_KEY``.
2. Adds ``journal_entries.key_id``, ``emotion_scores``, ``template``,
   ``prompt_id``, and ``updated_at``.
3. Data migration: provisions per-user wrapped DEKs for existing users and
   migrates any Day-3 Fernet ciphertexts in ``journal_entries`` (as well as
   ``mood_entries``, ``chat_sessions``, and ``messages``) into the Day-17
   AES-256-GCM envelope format with ``(user_id, field_name)`` Associated Data.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op
from app.core.config import Settings
from app.core.crypto import get_cipher, is_cipher_configured
from app.services.journal.crypto import (
    EnvelopeCipher,
    is_envelope_ciphertext,
    unwrap_data_key,
)

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql")


def _resolve_migration_cipher() -> EnvelopeCipher | None:
    if is_cipher_configured():
        configured = get_cipher()
        if isinstance(configured, EnvelopeCipher):
            return configured
    settings = Settings()
    return EnvelopeCipher.from_settings(settings)


def _as_uuid(raw: object) -> uuid.UUID:
    if isinstance(raw, uuid.UUID):
        return raw
    if isinstance(raw, bytes):
        return uuid.UUID(bytes=raw)
    return uuid.UUID(str(raw))


def _migrate_fernet_data(bind: sa.engine.Connection, cipher: EnvelopeCipher) -> None:
    """Provision user DEKs and migrate Day-3 Fernet ciphertexts to MENV envelopes."""
    users_table = sa.table(
        "users",
        sa.column("id", sa.Uuid()),
        sa.column("data_key_encrypted", sa.LargeBinary()),
        sa.column("data_key_id", sa.String(64)),
    )
    journals_table = sa.table(
        "journal_entries",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("title_encrypted", sa.LargeBinary()),
        sa.column("body_encrypted", sa.LargeBinary()),
        sa.column("key_id", sa.String(64)),
    )
    moods_table = sa.table(
        "mood_entries",
        sa.column("id", sa.Uuid()),
        sa.column("note_encrypted", sa.LargeBinary()),
    )
    chats_table = sa.table(
        "chat_sessions",
        sa.column("id", sa.Uuid()),
        sa.column("memory_encrypted", sa.LargeBinary()),
    )
    messages_table = sa.table(
        "messages",
        sa.column("id", sa.Uuid()),
        sa.column("content_encrypted", sa.LargeBinary()),
    )

    wrapped_by_user: dict[uuid.UUID, bytes] = {}
    dek_by_user: dict[uuid.UUID, bytes] = {}

    user_rows = bind.execute(
        sa.select(users_table.c.id, users_table.c.data_key_encrypted, users_table.c.data_key_id)
    ).fetchall()
    for row in user_rows:
        uid = _as_uuid(row[0])
        existing_wdek = row[1]
        if existing_wdek is not None:
            dek, kid = unwrap_data_key(bytes(existing_wdek), user_id=uid, keyring=cipher.keyring)
            wrapped = bytes(existing_wdek)
        else:
            dek, wrapped, kid = cipher.get_or_create_user_key(uid)
            bind.execute(
                sa.update(users_table)
                .where(users_table.c.id == uid)
                .values(data_key_encrypted=wrapped, data_key_id=kid)
            )
        wrapped_by_user[uid] = wrapped
        dek_by_user[uid] = dek

    journal_rows = bind.execute(
        sa.select(
            journals_table.c.id,
            journals_table.c.user_id,
            journals_table.c.title_encrypted,
            journals_table.c.body_encrypted,
        )
    ).fetchall()
    for row in journal_rows:
        jid = _as_uuid(row[0])
        uid = _as_uuid(row[1])
        raw_title = bytes(row[2])
        raw_body = bytes(row[3])
        if is_envelope_ciphertext(raw_title) and is_envelope_ciphertext(raw_body):
            continue

        wdek = wrapped_by_user.get(uid)
        dek = dek_by_user.get(uid)
        if wdek is None or dek is None:
            dek, wdek, _ = cipher.get_or_create_user_key(uid)
            wrapped_by_user[uid] = wdek
            dek_by_user[uid] = dek

        title_plain = (
            cipher.decrypt_field(raw_title, user_id=uid, field_name="title", wrapped_dek=wdek)
            if is_envelope_ciphertext(raw_title)
            else cipher.decrypt_legacy_fernet(raw_title)
        )
        body_plain = (
            cipher.decrypt_field(raw_body, user_id=uid, field_name="body", wrapped_dek=wdek)
            if is_envelope_ciphertext(raw_body)
            else cipher.decrypt_legacy_fernet(raw_body)
        )

        new_title = cipher.encrypt_field(
            title_plain, user_id=uid, field_name="title", wrapped_dek=wdek, data_key=dek
        )
        new_body = cipher.encrypt_field(
            body_plain, user_id=uid, field_name="body", wrapped_dek=wdek, data_key=dek
        )
        bind.execute(
            sa.update(journals_table)
            .where(journals_table.c.id == jid)
            .values(
                title_encrypted=new_title,
                body_encrypted=new_body,
                key_id=cipher.active_key_id,
            )
        )

    mood_rows = bind.execute(
        sa.select(moods_table.c.id, moods_table.c.note_encrypted).where(
            moods_table.c.note_encrypted.is_not(None)
        )
    ).fetchall()
    for row in mood_rows:
        mid = _as_uuid(row[0])
        raw_note = bytes(row[1])
        if not is_envelope_ciphertext(raw_note):
            plain = cipher.decrypt_legacy_fernet(raw_note)
            bind.execute(
                sa.update(moods_table)
                .where(moods_table.c.id == mid)
                .values(note_encrypted=cipher.encrypt(plain))
            )

    chat_rows = bind.execute(
        sa.select(chats_table.c.id, chats_table.c.memory_encrypted).where(
            chats_table.c.memory_encrypted.is_not(None)
        )
    ).fetchall()
    for row in chat_rows:
        cid = _as_uuid(row[0])
        raw_mem = bytes(row[1])
        if not is_envelope_ciphertext(raw_mem):
            plain = cipher.decrypt_legacy_fernet(raw_mem)
            bind.execute(
                sa.update(chats_table)
                .where(chats_table.c.id == cid)
                .values(memory_encrypted=cipher.encrypt(plain))
            )

    message_rows = bind.execute(
        sa.select(messages_table.c.id, messages_table.c.content_encrypted)
    ).fetchall()
    for row in message_rows:
        msgid = _as_uuid(row[0])
        raw_msg = bytes(row[1])
        if not is_envelope_ciphertext(raw_msg):
            plain = cipher.decrypt_legacy_fernet(raw_msg)
            bind.execute(
                sa.update(messages_table)
                .where(messages_table.c.id == msgid)
                .values(content_encrypted=cipher.encrypt(plain))
            )


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("data_key_encrypted", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("data_key_id", sa.String(length=64), nullable=True))

    with op.batch_alter_table("journal_entries") as batch:
        batch.add_column(
            sa.Column("key_id", sa.String(length=64), nullable=False, server_default="v1")
        )
        batch.add_column(sa.Column("emotion_scores", JSON_TYPE, nullable=True))
        batch.add_column(sa.Column("template", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("prompt_id", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column(
                "updated_at",
                TIMESTAMP,
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            )
        )

    if not context.is_offline_mode():
        cipher = _resolve_migration_cipher()
        if cipher is not None:
            _migrate_fernet_data(op.get_bind(), cipher)


def downgrade() -> None:
    with op.batch_alter_table("journal_entries") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("prompt_id")
        batch.drop_column("template")
        batch.drop_column("emotion_scores")
        batch.drop_column("key_id")

    with op.batch_alter_table("users") as batch:
        batch.drop_column("data_key_id")
        batch.drop_column("data_key_encrypted")
