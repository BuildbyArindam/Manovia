"""The retention job: only old rows, only for people who asked, always counted.

Every test here works on rows with **explicit timestamps** (the API always
stamps "now"), because "older than 30 days" is the entire contract and a test
that only ever writes today's rows cannot see the boundary.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import (
    ChatRepository,
    JournalRepository,
    MoodRepository,
    UserRepository,
)
from app.db.session import Database
from app.models.enums import MessageRole
from app.models.journal import JournalEntry
from app.models.message import Message
from app.models.mood import MoodEntry
from app.models.privacy import PrivacySetting
from app.services.privacy.retention import (
    _run_cli,
    apply_retention,
    plan_retention,
)

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


async def _person(session: AsyncSession) -> uuid.UUID:
    user = await UserRepository(session).create(is_anonymous=True)
    await session.commit()
    return user.id


async def _window(session: AsyncSession, user_id: uuid.UUID, days: int) -> None:
    session.add(PrivacySetting(user_id=user_id, retention_days=days))
    await session.commit()


async def _mood(session: AsyncSession, user_id: uuid.UUID, *, at: datetime, note: str) -> MoodEntry:
    entry = await MoodRepository(session).record(
        user_id=user_id, valence=3, energy=3, emotions=[], factors={}, note=note
    )
    entry.created_at = at
    await session.commit()
    return entry


async def _journal(session: AsyncSession, user_id: uuid.UUID, *, at: datetime) -> JournalEntry:
    entry = await JournalRepository(session).create(
        user_id=user_id, title="old", body="body", created_at=at
    )
    await session.commit()
    return entry


async def _chat(session: AsyncSession, user_id: uuid.UUID, *, at: datetime) -> uuid.UUID:
    """A saved session with one turn, both stamped at ``at``."""
    chats = ChatRepository(session)
    chat_session = await chats.create(user_id=user_id)
    chat_session.created_at = at
    message = await chats.add_message(
        session_id=chat_session.id, role=MessageRole.USER, content="hello"
    )
    message.created_at = at
    await session.commit()
    return chat_session.id


async def _count(session: AsyncSession, model: Any, user_id: uuid.UUID) -> int:
    statement = select(func.count()).select_from(model).where(model.user_id == user_id)
    return int((await session.execute(statement)).scalar_one())


async def test_only_users_with_a_window_are_planned(session: AsyncSession) -> None:
    with_window = await _person(session)
    without = await _person(session)
    await _window(session, with_window, 30)
    await _mood(session, with_window, at=NOW - timedelta(days=60), note="old")
    await _mood(session, without, at=NOW - timedelta(days=60), note="old")

    plans = await plan_retention(session, now=NOW)
    assert [plan.user_id for plan in plans] == [with_window]
    assert plans[0].retention_days == 30
    assert plans[0].cutoff == NOW - timedelta(days=30)
    assert plans[0].rows == {"mood_entries": 1}


async def test_a_window_with_nothing_to_delete_is_not_an_affected_user(
    session: AsyncSession,
) -> None:
    person = await _person(session)
    await _window(session, person, 30)
    await _mood(session, person, at=NOW - timedelta(days=1), note="recent")
    assert await plan_retention(session, now=NOW) == []

    report = await apply_retention(session, now=NOW)
    assert report.users_affected == 0 and report.total_deleted == 0


async def test_a_dry_run_deletes_nothing_but_reports_the_truth(
    session: AsyncSession,
) -> None:
    person = await _person(session)
    await _window(session, person, 30)
    await _mood(session, person, at=NOW - timedelta(days=45), note="old")
    await _journal(session, person, at=NOW - timedelta(days=45))

    report = await apply_retention(session, now=NOW, dry_run=True)
    assert report.dry_run is True
    assert report.rows_deleted == {"journal_entries": 1, "mood_entries": 1}
    assert report.total_deleted == 2
    assert await _count(session, MoodEntry, person) == 1
    assert await _count(session, JournalEntry, person) == 1


async def test_the_boundary_is_exclusive_and_the_rest_survives(
    session: AsyncSession,
) -> None:
    person = await _person(session)
    await _window(session, person, 30)
    just_inside = await _mood(session, person, at=NOW - timedelta(days=30), note="edge")
    old = await _mood(session, person, at=NOW - timedelta(days=30, seconds=1), note="old")
    recent = await _mood(session, person, at=NOW - timedelta(days=1), note="recent")
    old_id, inside_id, recent_id = old.id, just_inside.id, recent.id

    report = await apply_retention(session, now=NOW)
    assert report.rows_deleted == {"mood_entries": 1}

    remaining = {
        row.id
        for row in (
            (await session.execute(select(MoodEntry).where(MoodEntry.user_id == person)))
            .scalars()
            .all()
        )
    }
    assert remaining == {inside_id, recent_id}
    assert old_id not in remaining


async def test_messages_are_deleted_through_their_session(session: AsyncSession) -> None:
    person = await _person(session)
    await _window(session, person, 30)
    old_session = await _chat(session, person, at=NOW - timedelta(days=40))
    recent_session = await _chat(session, person, at=NOW - timedelta(days=2))

    report = await apply_retention(session, now=NOW)
    assert report.rows_deleted.get("messages") == 1
    assert report.rows_deleted.get("chat_sessions") == 1

    remaining = (await session.execute(select(Message.session_id))).scalars().all()
    assert set(remaining) == {recent_session}
    assert old_session not in set(remaining)


async def test_another_persons_rows_are_never_touched(session: AsyncSession) -> None:
    mine = await _person(session)
    theirs = await _person(session)
    await _window(session, mine, 30)
    await _mood(session, theirs, at=NOW - timedelta(days=90), note="not mine")
    await _mood(session, mine, at=NOW - timedelta(days=90), note="mine")

    report = await apply_retention(session, now=NOW)
    assert report.rows_deleted == {"mood_entries": 1}
    assert await _count(session, MoodEntry, theirs) == 1
    assert await _count(session, MoodEntry, mine) == 0


async def test_two_windows_do_not_share_a_cutoff(session: AsyncSession) -> None:
    short = await _person(session)
    long = await _person(session)
    await _window(session, short, 30)
    await _window(session, long, 365)
    await _mood(session, short, at=NOW - timedelta(days=60), note="goes")
    await _mood(session, long, at=NOW - timedelta(days=60), note="stays")

    report = await apply_retention(session, now=NOW)
    assert report.rows_deleted == {"mood_entries": 1}
    assert report.per_user[0]["retention_days"] == 30
    assert await _count(session, MoodEntry, long) == 1


async def test_the_cli_reports_json_and_needs_an_explicit_run_to_delete(
    session: AsyncSession, database: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """The operator-facing path: dry run first, then a real run."""
    person = await _person(session)
    await _window(session, person, 30)
    await _mood(session, person, at=NOW - timedelta(days=40), note="old")

    exit_code = await _run_cli(dry_run=True, as_json=True, database=database)
    assert exit_code == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and dry["rows_deleted"] == {"mood_entries": 1}
    assert await _count(session, MoodEntry, person) == 1

    exit_code = await _run_cli(dry_run=False, as_json=False, database=database)
    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "retention applied" in printed
    assert "mood_entries: 1" in printed
    assert await _count(session, MoodEntry, person) == 0
    # The report is counts and ids only — never a note.
    assert "old" not in printed
