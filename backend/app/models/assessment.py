"""``assessment_results`` — self-help questionnaire runs (PHQ-9, GAD-7).

These are screening *self-help* instruments: the band is a label for the person
to reflect on, never a diagnosis, and nothing here is shown to the LLM as
ground truth. Answers are stored in the clear because a user reviewing their own
history needs them and they contain no free text; the note-like fields that *do*
contain free text live on journal and mood rows, where they are encrypted.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import JSON_TYPE, Base, CreatedAtMixin, enum_check, enum_type
from app.models.enums import AssessmentBand, AssessmentInstrument

if TYPE_CHECKING:
    from app.models.user import User


class AssessmentResult(Base, CreatedAtMixin):
    """One completed questionnaire: raw answers, the total, and the band."""

    __tablename__ = "assessment_results"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_assessment_results_user_id_users", ondelete="CASCADE"),
        nullable=False,
    )
    instrument: Mapped[AssessmentInstrument] = mapped_column(
        enum_type(AssessmentInstrument), nullable=False
    )
    # Mapping of question id to answer value, e.g. {"phq9_1": 2, "phq9_2": 3}.
    answers: Mapped[dict[str, Any]] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    total: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    band: Mapped[AssessmentBand] = mapped_column(enum_type(AssessmentBand), nullable=False)

    user: Mapped[User] = relationship(back_populates="assessment_results")

    __table_args__ = (
        sa.Index("ix_assessment_results_user_id_created_at", "user_id", "created_at"),
        enum_check("instrument", AssessmentInstrument),
        enum_check("band", AssessmentBand),
        sa.CheckConstraint("total >= 0", name="total_not_negative"),
    )
