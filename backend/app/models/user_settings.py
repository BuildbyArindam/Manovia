"""``user_settings`` — the planner preferences a person can change in Settings.

One row per person, created on first save. Only planner-relevant choices live
here: which exercise types they prefer (a subset of the catalogue's tags) and
their quiet hours, when the planner suggests nothing at all.

The columns are deliberately boring — a JSON list of tag strings and two
``HH:MM`` strings — so the planner can read them as data, validate them on
write, and never interpret a half-written value. The API validates tags against
the shipped exercise catalogue before anything is stored.
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

#: Quiet-hour bounds the planner can reason about: 24-hour ``HH:MM`` strings.
QUIET_TIME_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"


class UserSettings(Base):
    """Planner preferences for one person."""

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(),
        sa.ForeignKey("users.id", name="fk_user_settings_user_id_users", ondelete="CASCADE"),
        primary_key=True,
    )
    #: Exercise tags the person prefers (e.g. ``["breathing"]``). Empty means
    #: "no preference" — the planner may suggest anything appropriate.
    preferred_exercise_tags: Mapped[list[str]] = mapped_column(
        JSON_TYPE, nullable=False, default=list
    )
    #: While quiet hours are on, the planner emits nothing (support options
    #: after a positive PHQ-9 item 9 are the documented exception).
    quiet_hours_enabled: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    quiet_start: Mapped[str] = mapped_column(sa.String(5), nullable=False, default="22:00")
    quiet_end: Mapped[str] = mapped_column(sa.String(5), nullable=False, default="07:00")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=sa.func.now(),
    )

    user: Mapped[User] = relationship(back_populates="settings")
