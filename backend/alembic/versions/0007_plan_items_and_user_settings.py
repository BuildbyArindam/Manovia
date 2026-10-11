"""daily-plan items and planner preferences

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plan_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("check_in", "exercise", "thought_record", "rest", "support", native_enum=False),
            nullable=False,
        ),
        sa.Column("ref", sa.String(64), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("why", sa.String(1000), nullable=False),
        sa.Column(
            "status", sa.Enum("pending", "done", "skipped", native_enum=False), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('check_in', 'exercise', 'thought_record', 'rest', 'support')",
            name=op.f("ck_plan_items_kind_in_planitemkind"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done', 'skipped')",
            name=op.f("ck_plan_items_status_in_planitemstatus"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_plan_items_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_items")),
        sa.UniqueConstraint(
            "user_id", "plan_date", "kind", name=op.f("uq_plan_items_user_id_plan_date_kind")
        ),
    )
    op.create_index("ix_plan_items_user_id_plan_date", "plan_items", ["user_id", "plan_date"])

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("preferred_exercise_tags", sa.JSON(), nullable=False),
        sa.Column("quiet_hours_enabled", sa.Boolean(), nullable=False),
        sa.Column("quiet_start", sa.String(5), nullable=False, server_default="22:00"),
        sa.Column("quiet_end", sa.String(5), nullable=False, server_default="07:00"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_settings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_settings")),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
    op.drop_index("ix_plan_items_user_id_plan_date", table_name="plan_items")
    op.drop_table("plan_items")
