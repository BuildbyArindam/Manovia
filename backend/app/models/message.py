"""``messages`` — chat turns, with the text stored encrypted at rest.

``content_encrypted`` holds a ciphertext blob produced by
:data:`app.core.crypto`; there is deliberately **no** plaintext column, and the
ORM attribute is named so that a careless ``repr`` or log line cannot leak a
user's words. Reading plaintext back is explicit::

    text = ChatRepository(session).message_text(message)

Risk triage happens before the LLM (AGENTS.md rule 1), which is why
``risk_level`` lives next to the message rather than in a separate table.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, enum_check, enum_type, range_check
from app.models.enums import MessageRole, RiskLevel

if TYPE_CHECKING:
    from app.models.chat_session import ChatSession


class Message(Base, CreatedAtMixin):
    """One chat turn. The words themselves are only ever stored encrypted."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey(
            "chat_sessions.id", name="fk_messages_session_id_chat_sessions", ondelete="CASCADE"
        ),
        nullable=False,
    )
    role: Mapped[MessageRole] = mapped_column(enum_type(MessageRole), nullable=False)
    content_encrypted: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    risk_level: Mapped[int] = mapped_column(
        sa.SmallInteger(), nullable=False, default=RiskLevel.NONE
    )
    # Optional emotion label from the (later) emotion classifier, e.g. "anxious".
    emotion: Mapped[str | None] = mapped_column(sa.String(48))

    chat_session: Mapped[ChatSession] = relationship(back_populates="messages")

    __table_args__ = (
        # Conversations are always read in order for one session.
        sa.Index("ix_messages_session_id_created_at", "session_id", "created_at"),
        enum_check("role", MessageRole),
        range_check("risk_level", int(RiskLevel.NONE), int(RiskLevel.CRISIS)),
    )
