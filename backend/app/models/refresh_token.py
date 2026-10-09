"""``refresh_tokens`` — one row per issued refresh token, stored **hashed**.

Rotation model: every login/guest/register starts a *family* (``family_id``);
each refresh rotates the token inside that family. Only the SHA-256 hash of
the token string is stored, so a database leak cannot be replayed as a session.

Reuse detection: presenting a token whose row is already ``revoked_at`` means
either a stolen rotated token or a replayed logout — the whole family is
revoked and the client must sign in again.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TIMESTAMP_TYPE, Base, CreatedAtMixin


class RefreshToken(Base, CreatedAtMixin):
    """One issued refresh token (identified by its JWT id, stored hashed)."""

    __tablename__ = "refresh_tokens"

    # The JWT ``jti`` claim: the token is addressed by this id as well as by hash.
    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_refresh_tokens_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    # Tokens issued in one sign-in (and all of its rotations) share a family.
    family_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), nullable=False)
    # SHA-256 hex of the refresh token string. Never the token itself.
    token_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP_TYPE, nullable=False)
    # Set when rotated, logged out, or revoked as part of a reuse response.
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP_TYPE)

    __table_args__ = (
        sa.Index("ix_refresh_tokens_user_id_created_at", "user_id", "created_at"),
        sa.Index("ix_refresh_tokens_family_id", "family_id"),
    )

    @property
    def is_active(self) -> bool:
        """True when the token has not been revoked (expiry is checked separately)."""
        return self.revoked_at is None
