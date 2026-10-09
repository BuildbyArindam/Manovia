"""``mood_entries`` — a check-in: two 1-5 scales, tags, and an optional note.

``note_encrypted`` is the only free-text field and is stored encrypted like chat
content. ``emotions`` and ``factors`` are JSON documents (a list of emotion
labels and a mapping of factor name to severity) so that the questionnaires can
grow without a schema change.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import JSON_TYPE, Base, CreatedAtMixin, range_check

if TYPE_CHECKING:
    from app.models.user import User


class MoodEntry(Base, CreatedAtMixin):
    """One mood check-in by one user."""

    __tablename__ = "mood_entries"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_mood_entries_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    # Both scales are integers 1..5, enforced by the CHECK constraints below.
    valence: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    energy: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    emotions: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False, default=list)
    factors: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    note_encrypted: Mapped[bytes | None] = mapped_column(sa.LargeBinary())

    user: Mapped[User] = relationship(back_populates="mood_entries")

    __table_args__ = (
        sa.Index("ix_mood_entries_user_id_created_at", "user_id", "created_at"),
        range_check("valence", 1, 5),
        range_check("energy", 1, 5),
    )
