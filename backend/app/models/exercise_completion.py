"""Exercise history: public content provenance and optional numeric ratings only.

There is no free-text column or stored planner choice. Like mood scales, ratings
are numeric metadata in the clear, restricted to the owner by the API. Hard
account deletion cascades to this table.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, range_check

if TYPE_CHECKING:
    from app.models.user import User


class ExerciseCompletion(Base, CreatedAtMixin):
    __tablename__ = "exercise_completions"

    # Client-generated UUIDs make explicit retry after an offline save idempotent.
    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    exercise_id: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    exercise_title: Mapped[str] = mapped_column(sa.String(1600), nullable=False)
    definition_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    pre_rating: Mapped[int | None] = mapped_column(sa.SmallInteger())
    post_rating: Mapped[int | None] = mapped_column(sa.SmallInteger())

    user: Mapped[User] = relationship(back_populates="exercise_completions")

    __table_args__ = (
        sa.Index("ix_exercise_completions_user_id_created_at", "user_id", "created_at"),
        range_check("duration_seconds", 1, 1800),
        range_check("pre_rating", 1, 5),
        range_check("post_rating", 1, 5),
    )
