"""The repository layer: every query the app will run, against a real database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import CipherNotConfiguredError, FieldCipher, configure_cipher
from app.db.repos import (
    AssessmentRepository,
    ChatRepository,
    ConsentRepository,
    JournalRepository,
    MoodRepository,
    SafetyEventRepository,
    UserRepository,
)
from app.db.session import Database
from app.models import (
    AssessmentBand,
    AssessmentInstrument,
    ConsentKind,
    MessageRole,
    RiskLevel,
    SafetyEventSource,
    User,
)


async def test_user_repository_lifecycle(session: AsyncSession) -> None:
    users = UserRepository(session)
    created = await users.create(
        email="Person@Example.test", language="en-GB", region="GB", is_anonymous=False
    )
    await session.commit()
    assert created.id is not None
    assert created.created_at is not None
    assert await users.get(created.id) is created

    # Exact match: normalisation is the account layer's business, not the repo's.
    assert await users.get_by_email("Person@Example.test") is created
    assert await users.get_by_email("person@example.test") is None
    assert await users.get(uuid.uuid4()) is None

    second = await users.create(is_anonymous=True)
    await session.commit()
    assert {user.id for user in await users.list_active()} == {created.id, second.id}
    deleted = await users.soft_delete(second.id)
    assert deleted is not None and deleted.is_deleted
    await session.commit()
    assert [user.id for user in await users.list_active()] == [created.id]
    assert await users.hard_delete(created.id) is True
    await session.commit()
    assert await users.list_active() == []
    assert await users.hard_delete(created.id) is False, "deleting twice is not an error"


async def test_anonymous_users_need_no_email_and_default_to_english(
    session: AsyncSession,
) -> None:
    user = await UserRepository(session).create(is_anonymous=True)
    await session.commit()
    assert user.email is None
    assert user.password_hash is None
    assert user.language == "en"
    assert user.is_anonymous is True
    assert user.deleted_at is None


async def test_consent_repository_appends_and_withdraws(
    session: AsyncSession, demo_user: User
) -> None:
    consents = ConsentRepository(session)
    granted = await consents.record(
        user_id=demo_user.id, kind=ConsentKind.AI_DISCLOSURE, version="1"
    )
    assert granted.granted is True
    await session.commit()

    assert await consents.is_granted(
        user_id=demo_user.id, kind=ConsentKind.AI_DISCLOSURE, version="1"
    )
    assert not await consents.is_granted(
        user_id=demo_user.id, kind=ConsentKind.STORE_CHAT, version="1"
    )
    assert await consents.latest(user_id=demo_user.id, kind=ConsentKind.STORE_CHAT) is None

    # A withdrawal of the *same* version is legal and becomes the newest word.
    withdrawn = await consents.record(
        user_id=demo_user.id, kind=ConsentKind.AI_DISCLOSURE, version="1", granted=False
    )
    await session.commit()
    assert not await consents.is_granted(
        user_id=demo_user.id, kind=ConsentKind.AI_DISCLOSURE, version="1"
    )
    latest = await consents.latest(user_id=demo_user.id, kind=ConsentKind.AI_DISCLOSURE)
    assert latest is not None and latest.id == withdrawn.id
    assert [consent.granted for consent in await consents.history(demo_user.id)] == [True, False]


async def test_chat_repository_encrypts_on_the_way_in(
    session: AsyncSession, demo_user: User, cipher: FieldCipher
) -> None:
    chats = ChatRepository(session)
    chat = await chats.create(user_id=demo_user.id)
    assert await chats.get(chat.id) is not None
    assert await chats.end(chat.id) is not None
    assert await chats.end(chat.id) is None, "closing twice is a no-op"
    closed = await chats.get(chat.id)
    assert closed is not None and closed.ended_at is not None

    first = await chats.add_message(
        session_id=chat.id, role=MessageRole.USER, content="one", risk_level=RiskLevel.NONE
    )
    await chats.add_message(session_id=chat.id, role=MessageRole.ASSISTANT, content="two")
    await session.commit()
    assert first.risk_level == int(RiskLevel.NONE)
    assert first.emotion is None
    assert first.content_encrypted != b"one"

    messages = await chats.list_messages(chat.id)
    assert [chats.message_text(message) for message in messages] == ["one", "two"]
    assert [message.role for message in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert [item.id for item in await chats.list_for_user(demo_user.id, limit=1)] == [chat.id]
    assert [chats.message_text(m) for m in await chats.list_messages(chat.id, offset=1)] == ["two"]


async def test_writing_a_message_without_a_key_fails_instead_of_storing_plaintext(
    session: AsyncSession, demo_user: User, cipher: FieldCipher
) -> None:
    chat = await ChatRepository(session).create(user_id=demo_user.id)
    await session.commit()
    configure_cipher(None)
    with pytest.raises(CipherNotConfiguredError):
        await ChatRepository(session).add_message(
            session_id=chat.id, role=MessageRole.USER, content="secret"
        )
    configure_cipher(cipher)
    await session.rollback()


async def test_mood_repository_round_trip(
    session: AsyncSession, demo_user: User, cipher: FieldCipher
) -> None:
    moods = MoodRepository(session)
    entry = await moods.record(
        user_id=demo_user.id,
        valence=2,
        energy=4,
        emotions=["anxious", "hopeful"],
        factors={"sleep": 2, "work": 4},
        note="rough morning",
    )
    await session.commit()
    assert entry.emotions == ["anxious", "hopeful"]
    assert entry.factors == {"sleep": 2, "work": 4}
    assert moods.note(entry) == "rough morning"
    assert await moods.get(entry.id) is entry

    # The `since` window is what the trend chart uses.
    assert (
        await moods.list_for_user(demo_user.id, since=datetime.now(UTC) + timedelta(days=1)) == []
    )
    assert len(await moods.list_for_user(demo_user.id, since=datetime(2020, 1, 1, tzinfo=UTC))) == 1
    assert [item.id for item in await moods.list_for_user(demo_user.id, limit=1)] == [entry.id]

    blank = await moods.record(user_id=demo_user.id, valence=5, energy=5)
    await session.commit()
    assert blank.emotions == []
    assert blank.factors == {}
    assert moods.note(blank) is None


async def test_journal_repository_keeps_title_and_body_separate(
    session: AsyncSession, demo_user: User, cipher: FieldCipher
) -> None:
    journals = JournalRepository(session)
    entry = await journals.create(
        user_id=demo_user.id, title="Monday", body="Busy but fine.", sentiment=0.1
    )
    await journals.create(user_id=demo_user.id, title="Tuesday", body="Hard.")
    await session.commit()
    assert journals.title(entry) == "Monday"
    assert journals.body(entry) == "Busy but fine."
    assert entry.sentiment == pytest.approx(0.1)
    assert await journals.get(entry.id) is entry

    listed = await journals.list_for_user(demo_user.id)
    assert {journals.title(item) for item in listed} == {"Monday", "Tuesday"}
    assert {journals.body(item) for item in listed} == {"Busy but fine.", "Hard."}
    assert len(await journals.list_for_user(demo_user.id, limit=1)) == 1
    assert await journals.list_for_user(demo_user.id, since=datetime(2020, 1, 1, tzinfo=UTC))
    assert await journals.list_for_user(demo_user.id, since=datetime(2999, 1, 1, tzinfo=UTC)) == []


async def test_assessment_repository_stores_scores(session: AsyncSession, demo_user: User) -> None:
    assessments = AssessmentRepository(session)
    result = await assessments.record(
        user_id=demo_user.id,
        instrument=AssessmentInstrument.PHQ9,
        answers={"1": 2, "2": 0},
        total=2,
        band=AssessmentBand.MINIMAL,
    )
    await session.commit()
    assert result.answers == {"1": 2, "2": 0}
    assert result.total == 2
    assert result.band is AssessmentBand.MINIMAL
    assert await assessments.get(result.id) is result
    assert len(await assessments.list_for_user(demo_user.id)) == 1
    assert await assessments.list_for_user(demo_user.id, instrument=AssessmentInstrument.GAD7) == []
    assert await assessments.list_for_user(demo_user.id, offset=5) == []


async def test_safety_repository_records_metadata_and_aggregates(
    session: AsyncSession, demo_user: User
) -> None:
    safety = SafetyEventRepository(session)
    chat = await ChatRepository(session).create(user_id=demo_user.id)
    await safety.record(
        user_id=demo_user.id,
        session_id=chat.id,
        risk_level=RiskLevel.CRISIS,
        source=SafetyEventSource.RULES,
    )
    await safety.record(
        user_id=demo_user.id, risk_level=RiskLevel.CAUTION, source=SafetyEventSource.ML
    )
    await safety.record(risk_level=RiskLevel.ELEVATED, source=SafetyEventSource.LLM_OUTPUT)
    await session.commit()

    events = await safety.list_for_user(demo_user.id)
    assert [event.source for event in events] == [SafetyEventSource.ML, SafetyEventSource.RULES]
    assert all(event.session_id in (None, chat.id) for event in events)
    assert await safety.list_for_user(demo_user.id, since=datetime(2999, 1, 1, tzinfo=UTC)) == []

    assert await safety.count_by_risk_level() == {
        int(RiskLevel.CRISIS): 1,
        int(RiskLevel.CAUTION): 1,
        int(RiskLevel.ELEVATED): 1,
    }
    assert await safety.count_by_risk_level(user_id=demo_user.id) == {
        int(RiskLevel.CRISIS): 1,
        int(RiskLevel.CAUTION): 1,
    }


async def test_repositories_do_not_commit_their_own_transactions(
    session: AsyncSession, database: Database
) -> None:
    """The caller owns the transaction, so a second session must not see the row."""
    users = UserRepository(session)
    created = await users.create(email="uncommitted@example.test")
    assert created.id is not None
    async with database.session_factory() as other:
        assert await UserRepository(other).get_by_email("uncommitted@example.test") is None
    await session.rollback()
    async with database.session_factory() as other:
        assert await UserRepository(other).get_by_email("uncommitted@example.test") is None
