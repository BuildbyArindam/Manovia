"""``consents`` — append-only history of every agreement shown to a user.

Records are never updated in place: re-consent (a new policy version, a
withdrawal) appends a row, so "what did this user accept on the day they signed
up" stays answerable. ``granted=False`` expresses a withdrawal.

Because a withdrawal is a *new* row for the same ``kind`` and ``version``, there
is no unique constraint on ``(user_id, kind, version)``. The invariant that
matters — the newest row wins — is enforced by the repository, which always
reads with ``ORDER BY created_at DESC, id``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, enum_check, enum_type
from app.models.enums import ConsentKind

if TYPE_CHECKING:
    from app.models.user import User


class Consent(Base, CreatedAtMixin):
    """One decision by one user about one policy document at one version."""

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_consents_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[ConsentKind] = mapped_column(enum_type(ConsentKind), nullable=False)
    # Policy/content version, e.g. "2026-10-01" or a content hash.
    version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    granted: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)

    user: Mapped[User] = relationship(back_populates="consents")

    __table_args__ = (
        sa.Index("ix_consents_user_id_created_at", "user_id", "created_at"),
        enum_check("kind", ConsentKind),
    )
