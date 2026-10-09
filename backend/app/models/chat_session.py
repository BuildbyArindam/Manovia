"""``chat_sessions`` — a run of messages between a user and the assistant."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TIMESTAMP_TYPE, Base, CreatedAtMixin

if TYPE_CHECKING:
    from app.models.message import Message
    from app.models.safety_event import SafetyEvent
    from app.models.user import User


class ChatSession(Base, CreatedAtMixin):
    """Container for :class:`~app.models.message.Message` rows.

    ``ended_at`` is null while the conversation is open. Deleting the session
    deletes its messages through ``ON DELETE CASCADE``.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_chat_sessions_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE)

    user: Mapped[User] = relationship(back_populates="chat_sessions")
    messages: Mapped[list[Message]] = relationship(
        back_populates="chat_session", cascade="all, delete-orphan", passive_deletes=True
    )
    safety_events: Mapped[list[SafetyEvent]] = relationship(
        back_populates="chat_session", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (sa.Index("ix_chat_sessions_user_id_created_at", "user_id", "created_at"),)
