"""Encrypted CBT thought records; only numeric ratings and fixed labels remain clear."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import JSON_TYPE, Base, CreatedAtMixin

if TYPE_CHECKING:
    from app.models.user import User


class CBTRecord(Base, CreatedAtMixin):
    __tablename__ = "cbt_records"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    situation_encrypted: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    automatic_thought_encrypted: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    evidence_for_encrypted: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    evidence_against_encrypted: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    balanced_thought_encrypted: Mapped[bytes] = mapped_column(sa.LargeBinary(), nullable=False)
    emotion: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    intensity_before: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    intensity_after: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    what_helped: Mapped[list[str]] = mapped_column(JSON_TYPE, nullable=False)
    key_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    user: Mapped[User] = relationship(back_populates="cbt_records")
    __table_args__ = (
        sa.Index("ix_cbt_records_user_id_created_at", "user_id", "created_at"),
        sa.CheckConstraint("intensity_before BETWEEN 0 AND 100", name="cbt_before_range"),
        sa.CheckConstraint("intensity_after BETWEEN 0 AND 100", name="cbt_after_range"),
    )
