"""add output-safety categories and encrypted rolling chat memory

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11

Day 14: ``safety_events.category`` stores only a closed category code for output
safety decisions, never a response fragment. ``chat_sessions.memory_encrypted``
holds a redacted, encrypted rolling summary; ``memory_deleted_at`` is a tombstone
that prevents an existing session from recreating memory after deletion.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)
CATEGORY_VALUES = (
    "diagnosis",
    "medication",
    "self_harm_method",
    "identity_claim",
    "outcome_promise",
    "isolation_dependence",
    "too_long",
    "wrong_language",
    "missing_check_in",
)


def upgrade() -> None:
    with op.batch_alter_table("safety_events") as batch:
        batch.add_column(sa.Column("category", sa.String(length=20), nullable=True))
        values = ", ".join(f"'{value}'" for value in CATEGORY_VALUES)
        batch.create_check_constraint(
            "ck_safety_events_category_in_safetyeventcategory",
            f"category IN ({values})",
        )

    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(sa.Column("memory_encrypted", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("memory_deleted_at", TIMESTAMP, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_column("memory_deleted_at")
        batch.drop_column("memory_encrypted")

    with op.batch_alter_table("safety_events") as batch:
        batch.drop_constraint("ck_safety_events_category_in_safetyeventcategory", type_="check")
        batch.drop_column("category")
