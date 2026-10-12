"""``privacy_settings`` and ``deletion_receipts`` — the Day 22 privacy controls.

Two small tables, both deliberately boring:

* ``privacy_settings`` — one row per person, written only when they change
  something in **Settings → Privacy**. The three columns are the ones the brief
  names: whether saved chat is allowed at all (``store_chat``), whether derived
  sentiment/emotion scores may be stored next to their words
  (``store_sentiment``), and how many days personal data is kept
  (``retention_days``). ``NULL`` retention means "keep until I delete it".

* ``deletion_receipts`` — what is left after a hard account deletion. The
  receipt is a *proof that a deletion happened*, so it must not reconstruct
  anything about the person: there is no ``user_id`` column, the account
  reference is an HMAC (see :mod:`app.services.privacy.deletion`) and the only
  content is per-table **row counts**. Nothing here can name a message, a
  journal entry or a mood note.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import JSON_TYPE, TIMESTAMP_TYPE, Base, utcnow

if TYPE_CHECKING:
    from app.models.user import User

#: Retention windows the API accepts, in days. The list is closed so the UI can
#: render it and a typo in a client cannot ask for a 10,000-day window.
RETENTION_DAY_CHOICES: tuple[int, ...] = (30, 90, 180, 365)

#: Bounds enforced by the database as well as by the API, so a hand-written
#: UPDATE cannot create an unusable window.
RETENTION_MIN_DAYS = 7
RETENTION_MAX_DAYS = 3650


class PrivacySetting(Base):
    """One person's privacy choices. Created lazily on the first change."""

    __tablename__ = "privacy_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_privacy_settings_user_id_users", ondelete="CASCADE"),
        primary_key=True,
    )
    #: ``True`` means "saving is allowed *if* I consent" — the consent record
    #: remains the primary control (Day 12), and this column is the off switch
    #: that overrides it. ``False`` is a hard veto: no new turn is written,
    #: whatever the client asks for. See docs/privacy-and-threat-model.md §6.
    store_chat: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=True, server_default=sa.true()
    )
    #: ``True`` by default because the chat already showed a numeric-free
    #: emotion hint since Day 12; ``False`` stops new sentiment/emotion scores
    #: from being written (existing rows are untouched until retention runs).
    store_sentiment: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=True, server_default=sa.true()
    )
    #: Days of personal data to keep, or ``NULL`` for "until I delete it".
    retention_days: Mapped[int | None] = mapped_column(sa.Integer())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=sa.func.now(),
    )

    user: Mapped[User] = relationship(back_populates="privacy_settings")

    __table_args__ = (
        sa.CheckConstraint(
            f"retention_days IS NULL OR (retention_days >= {RETENTION_MIN_DAYS} "
            f"AND retention_days <= {RETENTION_MAX_DAYS})",
            name="retention_days_within_bounds",
        ),
    )


class DeletionReceipt(Base):
    """A content-free record that one account was hard-deleted.

    ``account_ref`` is ``HMAC-SHA256(SECRET_KEY, user_id)`` truncated to 32
    hex characters. It lets an operator match a receipt the user was shown
    against the log without storing an identifier that can be reversed or
    cross-referenced against another table.
    """

    __tablename__ = "deletion_receipts"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    account_ref: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Which surface asked for the deletion ("api" today; a future support tool
    #: would be its own value). Free of personal content by construction.
    requested_via: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="api", server_default="api"
    )
    #: ``{"messages": 12, "mood_entries": 3, ...}`` — counts only.
    rows_deleted: Mapped[dict[str, int]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, default=utcnow, server_default=sa.func.now()
    )
