"""guided exercise completions and optional before/after ratings

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exercise_completions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("exercise_id", sa.String(64), nullable=False),
        sa.Column("exercise_title", sa.String(1600), nullable=False),
        sa.Column("definition_sha256", sa.String(64), nullable=False),
        sa.Column("duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("pre_rating", sa.SmallInteger(), nullable=True),
        sa.Column("post_rating", sa.SmallInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "duration_seconds >= 1 AND duration_seconds <= 1800",
            name=op.f("ck_exercise_completions_duration_seconds_between_1_and_1800"),
        ),
        sa.CheckConstraint(
            "pre_rating >= 1 AND pre_rating <= 5",
            name=op.f("ck_exercise_completions_pre_rating_between_1_and_5"),
        ),
        sa.CheckConstraint(
            "post_rating >= 1 AND post_rating <= 5",
            name=op.f("ck_exercise_completions_post_rating_between_1_and_5"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_exercise_completions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exercise_completions")),
    )
    op.create_index(
        "ix_exercise_completions_user_id_created_at",
        "exercise_completions",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_exercise_completions_user_id_created_at", table_name="exercise_completions")
    op.drop_table("exercise_completions")
