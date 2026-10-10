"""``ChatRepository.recent_messages`` — the tail of a thread, in reading order."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import ChatRepository
from app.models import User
from app.models.enums import MessageRole


@pytest.fixture
async def thread(session: AsyncSession, demo_user: User) -> tuple[ChatRepository, object]:
    repo = ChatRepository(session)
    chat = await repo.create(user_id=demo_user.id)
    for index in range(6):
        await repo.add_message(
            session_id=chat.id,
            role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            content=f"m{index}",
        )
    await session.commit()
    return repo, chat.id


async def test_returns_the_newest_n_oldest_first(thread: tuple[ChatRepository, object]) -> None:
    repo, session_id = thread
    rows = await repo.recent_messages(session_id, limit=3)  # type: ignore[arg-type]
    assert [repo.message_text(row) for row in rows] == ["m3", "m4", "m5"]


async def test_a_limit_larger_than_the_thread_returns_all_of_it(
    thread: tuple[ChatRepository, object],
) -> None:
    repo, session_id = thread
    rows = await repo.recent_messages(session_id, limit=100)  # type: ignore[arg-type]
    assert [repo.message_text(row) for row in rows] == [f"m{i}" for i in range(6)]


@pytest.mark.parametrize("limit", [0, -1])
async def test_a_non_positive_limit_is_an_empty_window_not_everything(
    thread: tuple[ChatRepository, object], limit: int
) -> None:
    repo, session_id = thread
    assert await repo.recent_messages(session_id, limit=limit) == []  # type: ignore[arg-type]
