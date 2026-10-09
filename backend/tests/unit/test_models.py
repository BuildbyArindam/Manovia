"""Schema shape: what the tables hold, and — just as importantly — what they must not."""

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Dialect

from app.models import (
    AssessmentResult,
    Base,
    ChatSession,
    Consent,
    JournalEntry,
    Message,
    MoodEntry,
    SafetyEvent,
    User,
)


def _columns(model: type[object]) -> set[str]:
    return {column.name for column in model.__table__.columns}  # type: ignore[attr-defined]


def _dialect(name: str) -> Dialect:
    """A driver-less dialect, so DDL can be compiled for both backends here."""
    return sa.create_mock_engine(f"{name}://", executor=None).dialect


def test_every_model_is_registered_with_a_table() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "consents",
        "chat_sessions",
        "messages",
        "mood_entries",
        "journal_entries",
        "assessment_results",
        "safety_events",
        "refresh_tokens",
    }


def test_user_columns_match_the_spec() -> None:
    assert _columns(User) == {
        "id",
        "email",
        "password_hash",
        "is_anonymous",
        "language",
        "region",
        "created_at",
        "deleted_at",
    }
    table = User.__table__
    assert table.c.email.unique is True
    for name in ("email", "password_hash", "deleted_at"):
        assert table.c[name].nullable, f"{name} must stay nullable"
    for name in ("id", "is_anonymous", "language", "created_at"):
        assert not table.c[name].nullable, f"{name} must be required"


def test_consent_columns_match_the_spec() -> None:
    assert _columns(Consent) == {
        "id",
        "user_id",
        "kind",
        "version",
        "granted",
        "created_at",
    }


def test_chat_columns_match_the_spec() -> None:
    assert _columns(ChatSession) == {"id", "user_id", "created_at", "ended_at"}
    assert _columns(Message) == {
        "id",
        "session_id",
        "role",
        "content_encrypted",
        "risk_level",
        "emotion",
        "created_at",
    }
    assert isinstance(Message.__table__.c.content_encrypted.type, sa.LargeBinary)


def test_mood_and_journal_columns_match_the_spec() -> None:
    assert _columns(MoodEntry) == {
        "id",
        "user_id",
        "valence",
        "energy",
        "emotions",
        "factors",
        "note_encrypted",
        "created_at",
    }
    assert _columns(JournalEntry) == {
        "id",
        "user_id",
        "title_encrypted",
        "body_encrypted",
        "sentiment",
        "created_at",
    }


def test_assessment_columns_match_the_spec() -> None:
    assert _columns(AssessmentResult) == {
        "id",
        "user_id",
        "instrument",
        "answers",
        "total",
        "band",
        "created_at",
    }


def test_safety_event_has_no_textual_column() -> None:
    """Safety events are metadata only: no place to put what somebody said.

    The check is a whitelist of types rather than a blacklist of names, so a
    future column has to be an id, a small number, or a timestamp. ``sa.Enum``
    appears in the list because it is how ``source`` stays a short controlled
    value (it subclasses ``String``, so a "no String columns" rule would be both
    wrong and unhelpful).
    """
    assert _columns(SafetyEvent) == {
        "id",
        "user_id",
        "session_id",
        "risk_level",
        "source",
        "created_at",
    }
    allowed = (sa.Uuid, sa.SmallInteger, sa.Enum, sa.DateTime)
    for column in SafetyEvent.__table__.columns:
        assert isinstance(column.type, allowed), (
            f"safety_events.{column.name} is {type(column.type).__name__}, not metadata"
        )
    # Only the id columns may be null; a detection always has a tier and a source.
    assert SafetyEvent.__table__.c.user_id.nullable
    assert SafetyEvent.__table__.c.session_id.nullable
    assert not SafetyEvent.__table__.c.risk_level.nullable
    assert not SafetyEvent.__table__.c.source.nullable


def test_personal_free_text_columns_are_binary() -> None:
    """Anything that carries a person's words is a byte column (ciphertext)."""
    binary = (
        Message.__table__.c.content_encrypted,
        MoodEntry.__table__.c.note_encrypted,
        JournalEntry.__table__.c.title_encrypted,
        JournalEntry.__table__.c.body_encrypted,
    )
    for column in binary:
        assert isinstance(column.type, sa.LargeBinary), f"{column.name} is not a byte column"


def test_every_child_table_cascades_from_users() -> None:
    for table in Base.metadata.sorted_tables:
        for constraint in table.foreign_keys:
            assert constraint.column.table.name in {"users", "chat_sessions"}
            assert constraint.ondelete == "CASCADE", (
                f"{table.name}.{constraint.parent.name} must cascade"
            )


def test_history_is_indexed_by_user_and_time() -> None:
    expected = {
        "consents": ("user_id", "created_at"),
        "chat_sessions": ("user_id", "created_at"),
        "messages": ("session_id", "created_at"),
        "mood_entries": ("user_id", "created_at"),
        "journal_entries": ("user_id", "created_at"),
        "assessment_results": ("user_id", "created_at"),
        "safety_events": ("user_id", "created_at"),
    }
    for name, columns in expected.items():
        table = Base.metadata.tables[name]
        indexed = {tuple(col.name for col in index.columns) for index in table.indexes}
        assert columns in indexed, f"{name} is not indexed on {columns}"


def test_enum_and_range_constraints_are_named_and_present() -> None:
    named = {
        constraint.name
        for table in Base.metadata.sorted_tables
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert {
        "ck_consents_kind_in_consentkind",
        "ck_messages_role_in_messagerole",
        "ck_messages_risk_level_between_0_and_3",
        "ck_mood_entries_valence_between_1_and_5",
        "ck_mood_entries_energy_between_1_and_5",
        "ck_assessment_results_instrument_in_assessmentinstrument",
        "ck_assessment_results_band_in_assessmentband",
        "ck_safety_events_source_in_safetyeventsource",
    } <= named


@pytest.mark.parametrize("table", Base.metadata.sorted_tables, ids=lambda t: t.name)
def test_every_table_compiles_for_both_dialects(table: sa.Table) -> None:
    """Compiling the DDL for SQLite and PostgreSQL catches unusable types early."""
    for dialect in (_dialect("sqlite"), _dialect("postgresql")):
        ddl = str(sa.schema.CreateTable(table).compile(dialect=dialect))
        assert table.name in ddl
        # A native PostgreSQL enum type would make later migrations painful.
        assert "CREATE TYPE" not in ddl


def test_json_documents_are_jsonb_on_postgres_and_json_on_sqlite() -> None:
    table = Base.metadata.tables["mood_entries"]
    for dialect, expected in ((_dialect("sqlite"), "JSON"), (_dialect("postgresql"), "JSONB")):
        assert expected in str(sa.schema.CreateTable(table).compile(dialect=dialect))


def test_enum_columns_are_bounded_varchar_on_both_dialects() -> None:
    for name, column in (
        ("consents", "kind"),
        ("messages", "role"),
        ("safety_events", "source"),
        ("assessment_results", "band"),
    ):
        table = Base.metadata.tables[name]
        for dialect in (_dialect("sqlite"), _dialect("postgresql")):
            ddl = str(sa.schema.CreateTable(table).compile(dialect=dialect))
            assert f"{column} VARCHAR(" in ddl, f"{name}.{column} is not a bounded VARCHAR"
