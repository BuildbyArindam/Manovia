"""Declarative base, naming conventions and column helpers for all models.

The helpers here keep the schema portable: the same DDL has to work on SQLite
(development and tests) and PostgreSQL (production), so we avoid
backend-specific types, use :class:`sqlalchemy.Uuid` for identifiers, and keep
enum-ish columns as ``VARCHAR`` plus an explicit named ``CHECK`` constraint
rather than a native PostgreSQL enum type (which is painful to alter later).
"""

from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Deterministic constraint names: Alembic needs stable names to be able to
# drop/recreate them, and PostgreSQL truncates auto-generated names.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSON documents are stored as JSONB on PostgreSQL and plain JSON elsewhere.
JSON_TYPE: sa.JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=None), "postgresql")

# Every timestamp in the schema is an immutable UTC instant.
TIMESTAMP_TYPE = sa.DateTime(timezone=True)


def utcnow() -> datetime:
    """Timezone-aware "now" in UTC (the only clock the schema uses)."""
    return datetime.now(UTC)


def enum_type(enum_cls: type[StrEnum]) -> sa.Enum:
    """A ``VARCHAR`` column restricted to ``enum_cls`` values.

    ``native_enum=False`` means no PostgreSQL ``CREATE TYPE`` (which cannot be
    extended transactionally), and ``create_constraint=False`` because the
    matching ``CHECK`` constraint is declared explicitly per table with
    :func:`enum_check` so that it receives a proper name from the naming
    convention.
    """
    return sa.Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        values_callable=lambda cls: [member.value for member in cls],
    )


def enum_check(column: str, enum_cls: type[StrEnum]) -> sa.CheckConstraint:
    """Named ``CHECK`` constraint allowing only ``enum_cls`` values."""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return sa.CheckConstraint(
        f"{column} IN ({values})", name=f"{column}_in_{enum_cls.__name__.lower()}"
    )


def range_check(column: str, low: int, high: int) -> sa.CheckConstraint:
    """Named ``CHECK`` constraint bounding an integer column to ``[low, high]``."""
    return sa.CheckConstraint(
        f"{column} >= {low} AND {column} <= {high}", name=f"{column}_between_{low}_and_{high}"
    )


class CreatedAtMixin:
    """Immutable creation timestamp, defaulted by the ORM and by the server."""

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP_TYPE, nullable=False, default=utcnow, server_default=sa.func.now()
    )


class Base(DeclarativeBase):
    """Base class for every Manovia ORM model.

    ``metadata`` carries the naming convention, so a constraint created without
    an explicit name still gets a stable, dialect-independent one.
    """

    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
