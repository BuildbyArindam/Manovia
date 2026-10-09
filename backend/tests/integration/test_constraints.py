"""The constraints the schema enforces by itself, in the database, not in Python."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import (
    ChatRepository,
    ConsentRepository,
    JournalRepository,
    MoodRepository,
    SafetyEventRepository,
)
from app.models import ConsentKind, MessageRole, RiskLevel, SafetyEventSource
from app.models.user import User


async def test_mood_scales_are_bounded_to_1_to_5(session: AsyncSession, demo_user: User) -> None:
    moods = MoodRepository(session)
    # A rollback expires every object in the session, so keep the id locally.
    user_id = demo_user.id
    await moods.record(user_id=user_id, valence=5, energy=1)
    await session.commit()
    for bad in (0, 6, -1):
        with pytest.raises(IntegrityError):
            await moods.record(user_id=user_id, valence=bad, energy=3)
        await session.rollback()


async def test_risk_level_is_bounded_to_the_risk_scale(
    session: AsyncSession, demo_user: User
) -> None:
    chats = ChatRepository(session)
    chat = await chats.create(user_id=demo_user.id)
    await session.commit()
    message = await chats.add_message(
        session_id=chat.id, role=MessageRole.USER, content="ok", risk_level=RiskLevel.ELEVATED
    )
    assert message.risk_level == 2
    await session.commit()
    with pytest.raises(IntegrityError):
        await chats.add_message(
            session_id=chat.id, role=MessageRole.USER, content="nope", risk_level=9
        )
    await session.rollback()


async def test_unknown_enum_values_are_rejected_by_the_database(
    session: AsyncSession, demo_user: User
) -> None:
    """Raw SQL bypasses the ORM's enum check; the CHECK constraint still wins."""
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO consents (id, user_id, kind, version, granted, created_at)"
                " VALUES (:id, :user_id, 'whatever', '1', 1, '2026-01-01 00:00:00')"
            ),
            {"id": uuid.uuid4().hex, "user_id": demo_user.id.hex},
        )
    await session.rollback()


async def test_consent_history_is_append_only_for_one_version(
    session: AsyncSession, demo_user: User
) -> None:
    """No unique constraint on (user_id, kind, version): a withdrawal is a new row.

    The row that wins is the newest one, which is what the repository returns.
    """
    consents = ConsentRepository(session)
    await consents.record(user_id=demo_user.id, kind=ConsentKind.TERMS, version="2026-10")
    await consents.record(
        user_id=demo_user.id, kind=ConsentKind.TERMS, version="2026-10", granted=False
    )
    await session.commit()
    assert len(await consents.history(demo_user.id)) == 2
    assert not await consents.is_granted(
        user_id=demo_user.id, kind=ConsentKind.TERMS, version="2026-10"
    )


async def test_emails_are_unique_but_many_anonymous_users_share_null(
    session: AsyncSession,
) -> None:
    """NULL email means "anonymous": the unique index must allow any number of them."""

    async def insert(email: str | None) -> None:
        await session.execute(
            text(
                "INSERT INTO users (id, email, is_anonymous, language, created_at)"
                " VALUES (:id, :email, 1, 'en', '2026-01-01 00:00:00')"
            ),
            {"id": uuid.uuid4().hex, "email": email},
        )

    await insert("a@example.test")
    await insert(None)
    await insert(None)
    await session.commit()
    with pytest.raises(IntegrityError):
        await insert("a@example.test")
    await session.rollback()


async def test_a_message_needs_a_real_session(session: AsyncSession, demo_user: User) -> None:
    """Proves SQLite is enforcing FKs, so the cascade tests mean something."""
    chats = ChatRepository(session)
    with pytest.raises(IntegrityError):
        await chats.add_message(session_id=uuid.uuid4(), role=MessageRole.USER, content="orphaned")
    await session.rollback()


async def test_safety_event_needs_a_known_risk_level_and_source(
    session: AsyncSession, demo_user: User
) -> None:
    safety = SafetyEventRepository(session)
    event = await safety.record(
        user_id=demo_user.id,
        risk_level=RiskLevel.CRISIS,
        source=SafetyEventSource.RULES,
    )
    await session.commit()
    assert event.session_id is None
    with pytest.raises(IntegrityError):
        await safety.record(
            user_id=demo_user.id, risk_level=int(RiskLevel.CRISIS) + 5, source=SafetyEventSource.ML
        )
    await session.rollback()
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO safety_events (id, risk_level, source, created_at)"
                " VALUES (:id, 3, 'guesswork', '2026-01-01 00:00:00')"
            ),
            {"id": uuid.uuid4().hex},
        )
    await session.rollback()


async def test_journal_sentiment_is_a_float_in_minus_one_to_one(
    session: AsyncSession, demo_user: User
) -> None:
    journals = JournalRepository(session)
    entry = await journals.create(
        user_id=demo_user.id, title="down", body="a rough day", sentiment=-0.75
    )
    await session.commit()
    assert entry.sentiment == pytest.approx(-0.75)
    with pytest.raises(IntegrityError):
        await journals.create(user_id=demo_user.id, title="x", body="y", sentiment=3.0)
    await session.rollback()
