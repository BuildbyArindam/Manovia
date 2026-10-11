"""Rotate the master encryption key and re-encrypt all stored personal fields.

Usage::

    # Using FIELD_ENCRYPTION_KEY keyring ("<new_id>:<new_b64>,<old_id>:<old_b64>"):
    FIELD_ENCRYPTION_KEY="v2:<new_b64>,v1:<old_b64>" \\
        python scripts/rotate_encryption_keys.py

    # Or passing explicit --new-key and --old-key arguments:
    python scripts/rotate_encryption_keys.py \\
        --new-key "v2:<new_b64>" \\
        --old-key "v1:<old_b64>" \\
        --database-url sqlite:///./manovia.db

Once this script finishes and exits 0, every user's wrapped DEK and every
encrypted column (``journal_entries``, ``mood_entries``, ``chat_sessions``,
``messages``) is encrypted under the new master key ID, and the old master key
ID can be removed from ``FIELD_ENCRYPTION_KEY``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"


def _bootstrap_imports() -> None:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))


def _normalize_key_entry(value: str, *, fallback_id: str) -> str:
    cleaned = value.strip()
    if ":" in cleaned:
        return cleaned
    return f"{fallback_id}:{cleaned}"


async def rotate(
    *,
    database_url: str | None = None,
    new_key: str | None = None,
    old_key: str | None = None,
    new_key_id: str = "v2",
    old_key_id: str = "v1",
    rekey_user_deks: bool = True,
) -> dict[str, Any]:
    """Run key rotation against ``database_url`` and return a metadata summary."""
    _bootstrap_imports()
    from app.core.config import Settings
    from app.core.crypto import configure_cipher
    from app.db.session import build_database
    from app.services.journal.crypto import EnvelopeCipher, rotate_database_encryption

    settings = (
        Settings(_env_file=None, database_url=database_url)
        if database_url
        else Settings()
    )

    if new_key and old_key:
        combined_spec = (
            f"{_normalize_key_entry(new_key, fallback_id=new_key_id)},"
            f"{_normalize_key_entry(old_key, fallback_id=old_key_id)}"
        )
        cipher = EnvelopeCipher(combined_spec)
    elif new_key:
        prev = settings.field_encryption_previous_keys or settings.field_encryption_key
        combined_spec = _normalize_key_entry(new_key, fallback_id=new_key_id)
        if prev:
            prev_norm = _normalize_key_entry(prev, fallback_id=old_key_id)
            combined_spec = f"{combined_spec},{prev_norm}"
        cipher = EnvelopeCipher(combined_spec)
    else:
        if not settings.field_encryption_key:
            raise SystemExit(
                "FIELD_ENCRYPTION_KEY is not set (and --new-key was not provided). "
                "Provide '<new_id>:<new_b64>,<old_id>:<old_b64>' to rotate."
            )
        cipher = EnvelopeCipher(
            settings.field_encryption_key,
            default_key_id=settings.field_encryption_key_id,
            previous_keys=settings.field_encryption_previous_keys,
        )

    configure_cipher(cipher)
    database = build_database(settings)
    try:
        async with database.session_factory() as session:
            report = await rotate_database_encryption(
                session, cipher, rekey_user_deks=rekey_user_deks
            )
            await session.commit()
        return {
            "database": database.describe(),
            "active_key_id": report.active_key_id,
            "users_rotated": report.users_rotated,
            "journal_entries_rotated": report.journal_entries_rotated,
            "cbt_records_rotated": report.cbt_records_rotated,
            "mood_entries_rotated": report.mood_entries_rotated,
            "chat_sessions_rotated": report.chat_sessions_rotated,
            "messages_rotated": report.messages_rotated,
        }
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rotate master encryption key and re-encrypt all stored fields."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL for this run only (e.g. sqlite:///./manovia.db).",
    )
    parser.add_argument(
        "--new-key",
        default=None,
        help="New master key ('<key_id>:<base64url>' or bare '<base64url>' with --new-key-id).",
    )
    parser.add_argument(
        "--old-key",
        default=None,
        help="Old master key ('<key_id>:<base64url>' or bare '<base64url>' with --old-key-id).",
    )
    parser.add_argument(
        "--new-key-id",
        default="v2",
        help="Key ID when --new-key has no '<key_id>:' prefix (default: v2).",
    )
    parser.add_argument(
        "--old-key-id",
        default="v1",
        help="Key ID when --old-key has no '<key_id>:' prefix (default: v1).",
    )
    parser.add_argument(
        "--preserve-user-deks",
        action="store_true",
        help="Only re-wrap existing user DEKs under the new master key without minting fresh DEKs.",
    )
    args = parser.parse_args(argv)
    summary = asyncio.run(
        rotate(
            database_url=args.database_url,
            new_key=args.new_key,
            old_key=args.old_key,
            new_key_id=args.new_key_id,
            old_key_id=args.old_key_id,
            rekey_user_deks=not args.preserve_user_deks,
        )
    )
    print(f"database: {summary['database']}")
    print(f"active_key_id: {summary['active_key_id']}")
    print(f"users_rotated: {summary['users_rotated']}")
    print(f"journal_entries_rotated: {summary['journal_entries_rotated']}")
    print(f"cbt_records_rotated: {summary['cbt_records_rotated']}")
    print(f"mood_entries_rotated: {summary['mood_entries_rotated']}")
    print(f"chat_sessions_rotated: {summary['chat_sessions_rotated']}")
    print(f"messages_rotated: {summary['messages_rotated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
