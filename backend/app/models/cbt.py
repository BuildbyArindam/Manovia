"""Encrypted CBT thought records; only numeric ratings and fixed labels remain clear."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import JSON_TYPE, Base, CreatedAtMixin

if TYPE_CHECKING:
    from app.models.user import User


#: The free-text fields, in the order the UI walks them.
CBT_TEXT_FIELDS: tuple[str, ...] = (
    "situation",
    "automatic_thought",
    "evidence_for",
    "evidence_against",
    "balanced_thought",
)


def cbt_aad(field: str) -> str:
    """Associated Data for a thought-record field, from the *one* place it lives.

    AES-256-GCM binds every ciphertext to the name it was encrypted under, so a
    writer and a reader that disagree about the name do not fail loudly at
    build time — they fail at read time, as "this row will not decrypt". That is
    exactly what happened here: the write path used ``cbt.situation`` (naming
    the table as well as the column, so a thought record's ciphertext cannot be
    confused with a journal body of the same name) while the Day-22 export read
    it back under ``situation``. Every thought record exported as ``null`` with
    ``decryption_failed``, and the test that should have caught it built its rows
    with the *export's* spelling, so the test agreed with the bug.

    Hence a function, not a string literal at each call site: one definition
    cannot disagree with itself.
    """
    return f"cbt.{field}"


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
