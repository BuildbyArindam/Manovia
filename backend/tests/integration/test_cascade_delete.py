"""ON DELETE CASCADE: one DELETE on ``users`` must remove an entire history."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import (
    AssessmentRepository,
    ChatRepository,
    ConsentRepository,
    JournalRepository,
    MoodRepository,
    SafetyEventRepository,
    UserRepository,
)
from app.models import (
    AssessmentBand,
    AssessmentInstrument,
    Base,
    ChatSession,
    ConsentKind,
    MessageRole,
    RiskLevel,
    SafetyEventSource,
    User,
)

CHILD_TABLES = (
    "consents",
    "chat_sessions",
    "messages",
    "mood_entries",
    "journal_entries",
    "assessment_results",
    "safety_events",
)


async def count(session: AsyncSession, table: str) -> int:
    stmt = select(func.count()).select_from(Base.metadata.tables[table])
    return int((await session.execute(stmt)).scalar_one())


async def seed_everything(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """One row in every child table; returns the chat session id."""
    await ConsentRepository(session).record(
        user_id=user_id, kind=ConsentKind.STORE_CHAT, version="2026-10"
    )
    chat = await ChatRepository(session).create(user_id=user_id)
    for index in range(3):
        await ChatRepository(session).add_message(
            session_id=chat.id,
            role=MessageRole.USER,
            content=f"message number {index}",
            risk_level=RiskLevel.CAUTION,
        )
    await MoodRepository(session).record(
        user_id=user_id, valence=3, energy=3, emotions=["ok"], note="a note"
    )
    await JournalRepository(session).create(user_id=user_id, title="t", body="b")
    await AssessmentRepository(session).record(
        user_id=user_id,
        instrument=AssessmentInstrument.GAD7,
        answers={"1": 1},
        total=1,
        band=AssessmentBand.MINIMAL,
    )
    await SafetyEventRepository(session).record(
        user_id=user_id,
        session_id=chat.id,
        risk_level=RiskLevel.ELEVATED,
        source=SafetyEventSource.RULES,
    )
    await session.commit()
    return chat.id


async def test_hard_delete_of_a_user_removes_every_child_row(
    session: AsyncSession, demo_user: User
) -> None:
    await seed_everything(session, demo_user.id)
    assert await count(session, "messages") == 3
    assert await count(session, "safety_events") == 1

    assert await UserRepository(session).hard_delete(demo_user.id) is True
    await session.commit()

    assert await count(session, "users") == 0
    for table in CHILD_TABLES:
        assert await count(session, table) == 0, f"{table} survived the user delete"


async def test_orm_delete_uses_the_same_cascade(session: AsyncSession, demo_user: User) -> None:
    """``passive_deletes=True`` means the database, not the ORM, removes the rows."""
    await seed_everything(session, demo_user.id)
    user = await session.get(User, demo_user.id)
    assert user is not None
    await session.delete(user)
    await session.commit()
    assert await count(session, "messages") == 0
    assert await count(session, "chat_sessions") == 0


async def test_deleting_a_session_deletes_its_messages_but_not_the_user(
    session: AsyncSession, demo_user: User
) -> None:
    chat_id = await seed_everything(session, demo_user.id)
    await session.execute(delete(ChatSession).where(ChatSession.id == chat_id))
    await session.commit()
    assert await count(session, "messages") == 0
    assert await count(session, "safety_events") == 0, "events tied to the session go with it"
    assert await count(session, "users") == 1
    assert await count(session, "mood_entries") == 1


async def test_safety_events_without_a_user_survive_a_user_delete(
    session: AsyncSession, demo_user: User
) -> None:
    """Anonymous detections (no user id yet) are not anybody's property to erase."""
    await SafetyEventRepository(session).record(
        user_id=None, session_id=None, risk_level=RiskLevel.CRISIS, source=SafetyEventSource.RULES
    )
    await UserRepository(session).hard_delete(demo_user.id)
    await session.commit()
    assert await count(session, "safety_events") == 1


async def test_a_user_with_no_history_deletes_cleanly(session: AsyncSession) -> None:
    lonely = await UserRepository(session).create(is_anonymous=True)
    await session.commit()
    assert await UserRepository(session).hard_delete(lonely.id) is True
    await session.commit()
    assert await count(session, "users") == 0


async def test_foreign_key_violations_are_caught_by_the_database(
    session: AsyncSession,
) -> None:
    """If this passes, the pragma is on and the cascade above was really FK-driven."""
    with pytest.raises(sa.exc.IntegrityError):
        await session.execute(
            text(
                "INSERT INTO chat_sessions (id, user_id, created_at)"
                " VALUES (:id, :user_id, '2026-01-01 00:00:00')"
            ),
            {"id": uuid.uuid4().hex, "user_id": uuid.uuid4().hex},
        )
    await session.rollback()


async def test_soft_delete_keeps_the_rows_until_a_purge(
    session: AsyncSession, demo_user: User
) -> None:
    await seed_everything(session, demo_user.id)
    users = UserRepository(session)
    deleted = await users.soft_delete(demo_user.id)
    assert deleted is not None and deleted.deleted_at is not None
    await session.commit()
    assert demo_user.is_deleted
    assert await count(session, "messages") == 3
    assert await users.list_active() == []
    # A second soft delete is a no-op, not an error.
    assert await users.soft_delete(demo_user.id) is None
