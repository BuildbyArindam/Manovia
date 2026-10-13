"""output guard: prompt_leak and jailbreak categories (Day 29 red team)

The red-team pass found model outputs that recite or describe the system
prompt and outputs that play along with a jailbreak persona. Both now fail the
output guard and are recorded as closed category codes, so the CHECK
constraint on ``safety_events.category`` gains two values. Both fit the
existing ``String(20)`` column.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_safety_events_category_in_safetyeventcategory"
BEFORE = (
    "diagnosis",
    "medication",
    "self_harm_method",
    "identity_claim",
    "outcome_promise",
    "isolation_dependence",
    "too_long",
    "wrong_language",
    "missing_check_in",
)
AFTER = (*BEFORE, "prompt_leak", "jailbreak")


def _recreate(values: tuple[str, ...]) -> None:
    with op.batch_alter_table("safety_events") as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        joined = ", ".join(f"'{value}'" for value in values)
        batch.create_check_constraint(CONSTRAINT, f"category IN ({joined})")


def upgrade() -> None:
    _recreate(AFTER)


def downgrade() -> None:
    op.execute(
        "UPDATE safety_events SET category = NULL WHERE category IN ('prompt_leak', 'jailbreak')"
    )
    _recreate(BEFORE)
