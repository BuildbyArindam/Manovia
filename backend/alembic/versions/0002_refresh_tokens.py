"""add refresh_tokens

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09

Day 4 refresh-token storage: one row per issued refresh token, kept as its
SHA-256 hash with the rotation family it belongs to. A token is only usable
alongside its row here, so the database alone can never mint a session.

Reviewed by hand after ``alembic revision --autogenerate``: every constraint
carries an explicit name (naming convention from the models), the child table
is removed by the user's ``ON DELETE CASCADE``, and ``downgrade()`` drops the
whole table so the round trip is tested on a fresh database.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every timestamp is an immutable UTC instant.
TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", TIMESTAMP, nullable=False),
        sa.Column("revoked_at", TIMESTAMP, nullable=True),
        sa.Column(
            "created_at", TIMESTAMP, nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_refresh_tokens_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        "ix_refresh_tokens_user_id_created_at",
        "refresh_tokens",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id_created_at", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
