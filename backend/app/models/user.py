"""``users`` — one row per account, anonymous or identified.

Deletion semantics: ``deleted_at`` is the user-facing soft delete (the account
disappears from the app while rows remain until a purge runs); the hard
"delete everything about me" path is a plain SQL ``DELETE``, and every child
table hangs off ``user_id`` with ``ON DELETE CASCADE`` so removing one row here
removes the whole history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TIMESTAMP_TYPE, Base, CreatedAtMixin

if TYPE_CHECKING:
    from app.models.assessment import AssessmentResult
    from app.models.chat_session import ChatSession
    from app.models.consent import Consent
    from app.models.journal import JournalEntry
    from app.models.mood import MoodEntry
    from app.models.safety_event import SafetyEvent


class User(Base, CreatedAtMixin):
    """An app user. ``email``/``password_hash`` are null for anonymous users."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    email: Mapped[str | None] = mapped_column(sa.String(320), unique=True)
    password_hash: Mapped[str | None] = mapped_column(sa.String(255))
    is_anonymous: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    # BCP 47 language tag ("en", "en-GB", "hi") and ISO 3166-1 alpha-2 region.
    language: Mapped[str] = mapped_column(sa.String(35), nullable=False, default="en")
    region: Mapped[str | None] = mapped_column(sa.String(2))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE)

    consents: Mapped[list[Consent]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    chat_sessions: Mapped[list[ChatSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    mood_entries: Mapped[list[MoodEntry]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    journal_entries: Mapped[list[JournalEntry]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    assessment_results: Mapped[list[AssessmentResult]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    safety_events: Mapped[list[SafetyEvent]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_deleted(self) -> bool:
        """True once the account has been soft-deleted."""
        return self.deleted_at is not None
