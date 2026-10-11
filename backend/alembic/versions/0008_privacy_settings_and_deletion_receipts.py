"""privacy settings and deletion receipts (Day 22)

Two tables, both described in ``app/models/privacy.py``:

* ``privacy_settings`` — the three account-level privacy choices the person
  makes in Settings → Privacy (``store_chat``, ``store_sentiment``,
  ``retention_days``).
* ``deletion_receipts`` — a content-free proof that a hard account deletion
  happened. Deliberately **no** ``user_id`` column: the account is gone, and a
  receipt that could be joined back to a person would be a second copy of the
  thing we were asked to delete. ``account_ref`` is an HMAC, and
  ``rows_deleted`` holds counts only.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "privacy_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("store_chat", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("store_sentiment", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "retention_days IS NULL OR (retention_days >= 7 AND retention_days <= 3650)",
            name=op.f("ck_privacy_settings_retention_days_within_bounds"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_privacy_settings_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_privacy_settings")),
    )

    op.create_table(
        "deletion_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_ref", sa.String(64), nullable=False),
        sa.Column("requested_via", sa.String(16), nullable=False, server_default="api"),
        sa.Column("rows_deleted", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deletion_receipts")),
    )


def downgrade() -> None:
    op.drop_table("deletion_receipts")
    op.drop_table("privacy_settings")
