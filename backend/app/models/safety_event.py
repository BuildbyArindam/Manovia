"""``safety_events`` — triage audit trail, metadata only.

A safety event says *that* a detector fired, when, at which tier, and from
which detector. There is no textual column here by design: the words that
triggered a detection already live (encrypted) on the message row, and copying
them into a second table would create an unencrypted trail of a person in
crisis. Keep it that way — add a column only after an ADR says why.

``user_id`` and ``session_id`` are both nullable because a detection can arrive
before an account exists (anonymous first-run) or without a chat context (an
onboarding screen). Both are indexed so an operator can answer "what happened
to this user" quickly.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, enum_check, enum_type, range_check
from app.models.enums import RiskLevel, SafetyEventSource

if TYPE_CHECKING:
    from app.models.chat_session import ChatSession
    from app.models.user import User


class SafetyEvent(Base, CreatedAtMixin):
    """One detection: ids, a risk tier, a detector, and a timestamp. Nothing else."""

    __tablename__ = "safety_events"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_safety_events_user_id_users", ondelete="CASCADE"),
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey(
            "chat_sessions.id", name="fk_safety_events_session_id_chat_sessions", ondelete="CASCADE"
        ),
    )
    risk_level: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    source: Mapped[SafetyEventSource] = mapped_column(enum_type(SafetyEventSource), nullable=False)

    user: Mapped[User | None] = relationship(back_populates="safety_events")
    chat_session: Mapped[ChatSession | None] = relationship(back_populates="safety_events")

    __table_args__ = (
        sa.Index("ix_safety_events_user_id_created_at", "user_id", "created_at"),
        sa.Index("ix_safety_events_session_id_created_at", "session_id", "created_at"),
        enum_check("source", SafetyEventSource),
        range_check("risk_level", int(RiskLevel.NONE), int(RiskLevel.CRISIS)),
    )
