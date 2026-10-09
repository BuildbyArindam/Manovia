"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-09

The starting point for the Manovia data layer: users, consents, chat sessions
and messages (text encrypted at rest), mood check-ins, journal entries,
assessment results, and safety events (metadata only).

Reviewed by hand after ``alembic revision --autogenerate``: every constraint
carries an explicit name, JSON columns are JSONB on PostgreSQL, enum-ish
columns are ``VARCHAR`` plus a ``CHECK`` (no PostgreSQL enum type to alter
later), and every child table is dropped by the user's ``ON DELETE CASCADE``.
``downgrade()`` removes the whole schema, so the round trip is tested on a
fresh database.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSON documents: JSONB on PostgreSQL, plain JSON on SQLite.
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
# Every timestamp is an immutable UTC instant.
TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_anonymous", sa.Boolean(), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column("region", sa.String(length=2), nullable=True),
        sa.Column("deleted_at", TIMESTAMP, nullable=True),
        sa.Column(
            "created_at", TIMESTAMP, nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("terms", "privacy", "ai_disclosure", "store_chat", native_enum=False),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", TIMESTAMP, nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")
        ),
        sa.CheckConstraint(
            "kind IN ('terms', 'privacy', 'ai_disclosure', 'store_chat')",
            name=op.f("ck_consents_kind_in_consentkind"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_consents_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consents")),
    )
    op.create_index(
        "ix_consents_user_id_created_at", "consents", ["user_id", "created_at"], unique=False
    )

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("ended_at", TIMESTAMP, nullable=True),
        sa.Column(
            "created_at", TIMESTAMP, nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_chat_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_sessions")),
    )
    op.create_index(
        "ix_chat_sessions_user_id_created_at",
        "chat_sessions",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role", sa.Enum("user", "assistant", "system", native_enum=False), nullable=False
        ),
        # Ciphertext only. There is no plaintext column to write to.
        sa.Column("content_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("risk_level", sa.SmallInteger(), nullable=False),
        sa.Column("emotion", sa.String(length=48), nullable=True),
        sa.Column(
            "created_at", TIMESTAMP, nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name=op.f("ck_messages_role_in_messagerole")
        ),
        sa.CheckConstraint(
            "risk_level >= 0 AND risk_level <= 3",
            name=op.f("ck_messages_risk_level_between_0_and_3"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_messages_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        "ix_messages_session_id_created_at",
        "messages",
        ["session_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "mood_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("valence", sa.SmallInteger(), nullable=False),
        sa.Column("energy", sa.SmallInteger(), nullable=False),
        sa.Column("emotions", JSON_TYPE, nullable=False),
        sa.Column("factors", JSON_TYPE, nullable=False),
        sa.Column("note_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at", TIMESTAMP, nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")
        ),
        sa.CheckConstraint(
            "energy >= 1 AND energy <= 5", name=op.f("ck_mood_entries_energy_between_1_and_5")
        ),
        sa.CheckConstraint(
            "valence >= 1 AND valence <= 5", name=op.f("ck_mood_entries_valence_between_1_and_5")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_mood_entries_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mood_entries")),
    )
    op.create_index(
        "ix_mood_entries_user_id_created_at",
        "mood_entries",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("body_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("sentiment", sa.Float(), nullable=True),
        sa.Column(
            "created_at", TIMESTAMP, nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")
        ),
        sa.CheckConstraint(
            "sentiment IS NULL OR (sentiment >= -1 AND sentiment <= 1)",
            name=op.f("ck_journal_entries_sentiment_between_minus_1_and_1"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_journal_entries_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_entries")),
    )
    op.create_index(
        "ix_journal_entries_user_id_created_at",
        "journal_entries",
        ["user_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "assessment_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("instrument", sa.Enum("phq9", "gad7", native_enum=False), nullable=False),
        sa.Column("answers", JSON_TYPE, nullable=False),
        sa.Column("total", sa.SmallInteger(), nullable=False),
        sa.Column(
            "band",
            sa.Enum(
                "minimal",
                "mild",
                "moderate",
                "moderately_severe",
                "severe",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at", TIMESTAMP, nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")
        ),
        sa.CheckConstraint(
            "band IN ('minimal', 'mild', 'moderate', 'moderately_severe', 'severe')",
            name=op.f("ck_assessment_results_band_in_assessmentband"),
        ),
        sa.CheckConstraint(
            "instrument IN ('phq9', 'gad7')",
            name=op.f("ck_assessment_results_instrument_in_assessmentinstrument"),
        ),
        sa.CheckConstraint("total >= 0", name=op.f("ck_assessment_results_total_not_negative")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_assessment_results_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assessment_results")),
    )
    op.create_index(
        "ix_assessment_results_user_id_created_at",
        "assessment_results",
        ["user_id", "created_at"],
        unique=False,
    )

    # Metadata only: no textual column, by design. See app/models/safety_event.py.
    op.create_table(
        "safety_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("risk_level", sa.SmallInteger(), nullable=False),
        sa.Column(
            "source",
            sa.Enum("rules", "ml", "llm_output", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "created_at", TIMESTAMP, nullable=False, server_default=sa.text("(CURRENT_TIMESTAMP)")
        ),
        sa.CheckConstraint(
            "risk_level >= 0 AND risk_level <= 3",
            name=op.f("ck_safety_events_risk_level_between_0_and_3"),
        ),
        sa.CheckConstraint(
            "source IN ('rules', 'ml', 'llm_output')",
            name=op.f("ck_safety_events_source_in_safetyeventsource"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_safety_events_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_safety_events_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_safety_events")),
    )
    op.create_index(
        "ix_safety_events_session_id_created_at",
        "safety_events",
        ["session_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_safety_events_user_id_created_at",
        "safety_events",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # Indexes are removed with their tables by both SQLite and PostgreSQL, so
    # there is nothing else to drop apart from the tables themselves (children
    # first, users last, because the FKs point at it).
    for table in (
        "safety_events",
        "assessment_results",
        "journal_entries",
        "mood_entries",
        "messages",
        "chat_sessions",
        "consents",
    ):
        op.drop_table(table)
    op.drop_table("users")
