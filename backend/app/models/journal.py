"""``journal_entries`` — a written reflection, stored envelope-encrypted at rest.

Title and body are separate AES-256-GCM ciphertext blobs bound to
``(user_id, "title")`` and ``(user_id, "body")`` via Associated Data (AAD) so
ciphertexts cannot be swapped across users or across fields. ``sentiment`` and
``emotion_scores`` are the only derived signals kept in the clear (numeric
values only, never text), and both are ``None`` when the user disables emotion
scoring on write.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import JSON_TYPE, TIMESTAMP_TYPE, Base, CreatedAtMixin, utcnow

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
    key_id: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, default="v1", server_default="v1"
    )
    sentiment: Mapped[float | None] = mapped_column(sa.Float())
    emotion_scores: Mapped[dict[str, float] | None] = mapped_column(JSON_TYPE)
    template: Mapped[str | None] = mapped_column(sa.String(32))
    prompt_id: Mapped[str | None] = mapped_column(sa.String(64))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, default=utcnow, server_default=sa.func.now()
    )

    user: Mapped[User] = relationship(back_populates="journal_entries")

    __table_args__ = (
        sa.Index("ix_journal_entries_user_id_created_at", "user_id", "created_at"),
        sa.CheckConstraint(
            "sentiment IS NULL OR (sentiment >= -1 AND sentiment <= 1)",
            name="sentiment_between_minus_1_and_1",
        ),
    )
