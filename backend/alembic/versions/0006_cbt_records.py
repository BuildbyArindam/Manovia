"""CBT thought records (encrypted free-text fields).

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cbt_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        *[
            sa.Column(f"{name}_encrypted", sa.LargeBinary(), nullable=False)
            for name in (
                "situation",
                "automatic_thought",
                "evidence_for",
                "evidence_against",
                "balanced_thought",
            )
        ],
        sa.Column("emotion", sa.String(32), nullable=False),
        sa.Column("intensity_before", sa.SmallInteger(), nullable=False),
        sa.Column("intensity_after", sa.SmallInteger(), nullable=False),
        sa.Column("what_helped", sa.JSON(), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "intensity_before BETWEEN 0 AND 100", name=op.f("ck_cbt_records_cbt_before_range")
        ),
        sa.CheckConstraint(
            "intensity_after BETWEEN 0 AND 100", name=op.f("ck_cbt_records_cbt_after_range")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_cbt_records_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cbt_records")),
    )
    op.create_index("ix_cbt_records_user_id_created_at", "cbt_records", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_cbt_records_user_id_created_at", table_name="cbt_records")
    op.drop_table("cbt_records")
