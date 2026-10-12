"""mood check-ins: idempotency key for offline sync (Day 25)

A check-in made while the device is offline is queued on the device and sent
when the connection returns. Sending it can be retried — the response to the
first attempt may never arrive — so each queued check-in carries a random
``client_id`` and the pair ``(user_id, client_id)`` is unique: a retry finds the
stored row instead of creating a second one. Existing rows keep ``NULL`` (NULLs
never collide in a unique index on SQLite or PostgreSQL).

A unique *index* rather than a table constraint, because SQLite cannot add a
constraint to an existing table without a batch rebuild, and an index enforces
the same rule on both backends.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mood_entries", sa.Column("client_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ux_mood_entries_user_id_client_id",
        "mood_entries",
        ["user_id", "client_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_mood_entries_user_id_client_id", table_name="mood_entries")
    with op.batch_alter_table("mood_entries") as batch:
        batch.drop_column("client_id")
