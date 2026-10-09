"""``journal_entries`` — a written reflection, stored encrypted at rest.

Title and body are separate ciphertext blobs so a future list view can show
"when did I write what" without decrypting the body. ``sentiment`` is the only
derived signal kept in the clear because the trend charts need to aggregate it
in SQL; it is a float in ``[-1, 1]``, not a judgement of the person.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin

if TYPE_CHECKING:
    from app.models.user import User


class JournalEntry(Base, CreatedAtMixin):
    """One journal entry. ``title_encrypted``/``body_encrypted`` are ciphertext."""

    __tablename__ = "journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_journal_entries_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    title_encrypted: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    body_encrypted: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    sentiment: Mapped[float | None] = mapped_column(sa.Float())

    user: Mapped[User] = relationship(back_populates="journal_entries")

    __table_args__ = (
        sa.Index("ix_journal_entries_user_id_created_at", "user_id", "created_at"),
        sa.CheckConstraint(
            "sentiment IS NULL OR (sentiment >= -1 AND sentiment <= 1)",
            name="sentiment_between_minus_1_and_1",
        ),
    )
