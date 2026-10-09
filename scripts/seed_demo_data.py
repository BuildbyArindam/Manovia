#!/usr/bin/env python3
"""Seed one demo user with a realistic slice of data in every table.

    python scripts/seed_demo_data.py                       # uses DATABASE_URL / .env
    python scripts/seed_demo_data.py --database-url sqlite:///./manovia.db
    python scripts/seed_demo_data.py --fresh                # delete + recreate the demo user

Field encryption is required: the script refuses to run without
``FIELD_ENCRYPTION_KEY`` rather than writing rows nobody can read back. Create a
throwaway local key with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Re-running is safe: an existing demo user is reused and no duplicate rows are
added. ``--fresh`` hard-deletes the demo user first, which also demonstrates
the ``ON DELETE CASCADE`` behaviour of the schema.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"

DEMO_EMAIL = "demo@manovia.local"
DEMO_PASSWORD = "manovia-demo"


def _bootstrap_imports() -> None:
    """Make ``app`` importable: this script lives outside the backend package."""
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))


def demo_password_hash(password: str) -> str:
    """Hash the demo password with the API's real argon2 hasher.

    Since Day 4 the seeded account can actually sign in through
    ``POST /api/v1/auth/login`` — the hash is produced by the same code that
    verifies it (``app.core.passwords``).
    """
    _bootstrap_imports()
    from app.core.passwords import get_password_hasher

    return get_password_hasher().hash(password)


async def seed(database_url: str | None, fresh: bool) -> dict[str, Any]:
    """Insert the demo rows and return a summary for printing."""
    _bootstrap_imports()
    import sqlalchemy as sa
    from sqlalchemy import func, select

    from app.core.config import Settings
    from app.core.crypto import FernetCipher, configure_cipher
    from app.content import load_consent_documents
    from app.db.repos import (
        AssessmentRepository,
        ChatRepository,
        ConsentRepository,
        JournalRepository,
        MoodRepository,
        SafetyEventRepository,
        UserRepository,
    )
    from app.db.session import build_database
    from app.models import (
        AssessmentBand,
        AssessmentInstrument,
        Base,
        ConsentKind,
        MessageRole,
        RiskLevel,
        SafetyEventSource,
    )

    settings = (
        Settings(_env_file=None, database_url=database_url) if database_url else Settings()
    )
    if not settings.field_encryption_key:
        raise SystemExit(
            "FIELD_ENCRYPTION_KEY is not set. The demo data contains personal text, so it is "
            "never written in the clear: export a key (see the module docstring) and re-run."
        )
    configure_cipher(FernetCipher(settings.field_encryption_key))

    database = build_database(settings)
    summary: dict[str, Any] = {"database": database.describe(), "created": False}
    try:
        async with database.engine.connect() as connection:
            tables = await connection.run_sync(
                lambda sync_conn: sa.inspect(sync_conn).get_table_names()
            )
        if "users" not in tables:
            raise SystemExit(
                "The schema is not migrated yet (no 'users' table). "
                "Run: cd backend && alembic upgrade head"
            )

        async with database.session_factory() as session:
            users = UserRepository(session)
            user = await users.get_by_email(DEMO_EMAIL)
            if user is not None and fresh:
                # One statement; ON DELETE CASCADE takes the history with it.
                await users.hard_delete(user.id)
                user = None
                summary["cascade_deleted"] = True
            if user is None:
                user = await users.create(
                    email=DEMO_EMAIL,
                    password_hash=demo_password_hash(DEMO_PASSWORD),
                    is_anonymous=False,
                    language="en",
                    region="US",
                )
                summary["created"] = True
            await session.commit()
            user_id = user.id
            summary["user_id"] = str(user_id)

            chats = ChatRepository(session)
            if await chats.list_for_user(user_id, limit=1):
                summary["note"] = "demo user already has data; nothing added (use --fresh to reset)"
                return summary

            consents = ConsentRepository(session)
            moods = MoodRepository(session)
            journals = JournalRepository(session)
            assessments = AssessmentRepository(session)
            safety = SafetyEventRepository(session)

            # Consent rows carry the *current* document versions (the same ones
            # GET /api/v1/consent/requirements publishes), so the demo account
            # passes the require_consent gate.
            documents = load_consent_documents()
            for kind in ConsentKind:
                await consents.record(
                    user_id=user_id, kind=kind, version=documents.version_for(kind)
                )

            chat = await chats.create(user_id=user_id)
            await chats.add_message(
                session_id=chat.id,
                role=MessageRole.USER,
                content="I have not slept properly in a week and work is piling up.",
                risk_level=RiskLevel.CAUTION,
                emotion="overwhelmed",
            )
            await chats.add_message(
                session_id=chat.id,
                role=MessageRole.ASSISTANT,
                content=(
                    "A week of thin sleep is exhausting on its own. What would the smallest "
                    "step for tonight look like?"
                ),
            )
            await chats.add_message(
                session_id=chat.id,
                role=MessageRole.USER,
                content="Honestly I keep thinking everyone would be better off without me.",
                risk_level=RiskLevel.CRISIS,
                emotion="despairing",
            )
            await chats.add_message(
                session_id=chat.id,
                role=MessageRole.SYSTEM,
                content="Crisis response served: helplines shown, no LLM call made.",
                risk_level=RiskLevel.CRISIS,
            )
            await chats.end(chat.id)

            await safety.record(
                user_id=user_id,
                session_id=chat.id,
                risk_level=RiskLevel.CRISIS,
                source=SafetyEventSource.RULES,
            )
            await safety.record(
                user_id=user_id,
                session_id=chat.id,
                risk_level=RiskLevel.CAUTION,
                source=SafetyEventSource.ML,
            )

            await moods.record(
                user_id=user_id,
                valence=2,
                energy=1,
                emotions=["anxious", "tired"],
                factors={"sleep": 2, "work": 4, "relationships": 3},
                note="Lay down at 2am, still wired.",
            )
            await journals.create(
                user_id=user_id,
                title="The Tuesday I stopped multitasking",
                body="Closed the laptop at six and walked. Nothing burned down.",
                sentiment=0.25,
            )
            await assessments.record(
                user_id=user_id,
                instrument=AssessmentInstrument.PHQ9,
                answers={
                    str(question): answer
                    for question, answer in enumerate([2, 3, 2, 1, 1, 2, 2, 1, 0], start=1)
                },
                total=14,
                band=AssessmentBand.MODERATE,
            )

            summary["row_counts"] = {
                table.name: int(
                    (await session.execute(select(func.count()).select_from(table))).scalar_one()
                )
                for table in Base.metadata.sorted_tables
            }
            await session.commit()
            return summary
    finally:
        await database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed one demo user with example data.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL for this run only (e.g. sqlite:///./manovia.db).",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Hard-delete an existing demo user first, then re-seed.",
    )
    args = parser.parse_args()
    summary = asyncio.run(seed(args.database_url, args.fresh))
    print(f"database: {summary['database']}")
    print(f"demo user: {summary.get('user_id')} ({DEMO_EMAIL} / {DEMO_PASSWORD})")
    if summary.get("cascade_deleted"):
        print("previous demo user and all of their rows were deleted by ON DELETE CASCADE")
    for table, count in summary.get("row_counts", {}).items():
        print(f"  {table}: {count}")
    if "note" in summary:
        print(f"note: {summary['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
