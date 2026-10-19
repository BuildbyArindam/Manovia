"""clinician shares: consented, time-limited, ciphertext-only appointment summaries (Day 47)

Three tables. ``clinician_shares`` holds the encrypted documents and the budget
(expiry, revocation, view limit, passcode hash). ``share_access_events`` is the
audit log the owner reads. ``share_preferences`` is the per-person switch that
makes sharing possible at all — it defaults to *off*, and the row is only written
by an explicit action.

No column here holds a decryption key, a share token, a passcode or a share's
contents. The token is stored as SHA-256; the documents are AES-256-GCM
ciphertext whose key was handed to the owner once, in the URL fragment.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clinician_shares",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_prefix", sa.String(length=8), nullable=False),
        sa.Column("passcode_hash", sa.LargeBinary(), nullable=False),
        sa.Column("passcode_salt", sa.LargeBinary(), nullable=False),
        sa.Column("passcode_failures", sa.SmallInteger(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_limit", sa.SmallInteger(), nullable=False),
        sa.Column("view_count", sa.Integer(), nullable=False),
        sa.Column("formats", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("payload_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("payload_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("pdf_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("pdf_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("fhir_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("fhir_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("cipher", sa.Enum("A256GCM", native_enum=False), nullable=False),
        sa.Column("consent_summary_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "cipher IN ('A256GCM')", name=op.f("ck_clinician_shares_cipher_in_sharecipher")
        ),
        sa.CheckConstraint(
            "view_limit >= 1", name=op.f("ck_clinician_shares_view_limit_at_least_one")
        ),
        sa.CheckConstraint(
            "view_count >= 0", name=op.f("ck_clinician_shares_view_count_not_negative")
        ),
        sa.CheckConstraint(
            "passcode_failures >= 0",
            name=op.f("ck_clinician_shares_passcode_failures_not_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_clinician_shares_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clinician_shares")),
    )
    op.create_index(
        "ux_clinician_shares_token_hash", "clinician_shares", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_clinician_shares_user_id_created_at", "clinician_shares", ["user_id", "created_at"]
    )

    op.create_table(
        "share_access_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("share_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "created",
                "opened",
                "passcode_failed",
                "locked",
                "revoked",
                "expired",
                "view_limit_reached",
                "access_after_revoke",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('created', 'opened', 'passcode_failed', 'locked', 'revoked', "
            "'expired', 'view_limit_reached', 'access_after_revoke')",
            name=op.f("ck_share_access_events_kind_in_shareeventkind"),
        ),
        sa.ForeignKeyConstraint(
            ["share_id"],
            ["clinician_shares.id"],
            name=op.f("fk_share_access_events_share_id_clinician_shares"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_share_access_events")),
    )
    op.create_index(
        "ix_share_access_events_share_id_created_at",
        "share_access_events",
        ["share_id", "created_at"],
    )

    op.create_table(
        "share_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_share_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_share_preferences")),
    )


def downgrade() -> None:
    op.drop_table("share_preferences")
    op.drop_index("ix_share_access_events_share_id_created_at", table_name="share_access_events")
    op.drop_table("share_access_events")
    op.drop_index("ix_clinician_shares_user_id_created_at", table_name="clinician_shares")
    op.drop_index("ux_clinician_shares_token_hash", table_name="clinician_shares")
    op.drop_table("clinician_shares")
