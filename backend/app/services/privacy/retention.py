"""The retention job: delete personal data older than each person's window.

A person who sets *keep for 90 days* in Settings → Privacy is making a promise
the code has to keep on its own, because nobody is going to log in and hand-us
the deletion. This module is that mechanism, and it is deliberately the same
code for all three callers:

* ``python -m app.services.privacy.retention`` (``make retention``) — the CLI an
  operator or a cron entry runs,
* the nightly job a deployment wires to that CLI,
* the tests, which call :func:`plan_retention` and :func:`apply_retention`
  directly.

Three rules it keeps:

1. **Only rows older than the window.** The cutoff is computed from the run's
   ``now`` in UTC, and rows are matched on their ``created_at``. A 30-day
   window never removes today's check-in.
2. **Nothing is deleted without a plan.** :func:`plan_retention` is pure
   accounting: it says, per person and per table, exactly what would go. The
   CLI prints it with ``--dry-run`` before anything is touched.
3. **No text in the output.** The report contains counts, table names, user
   ids and cutoffs — never a row's content.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import Database, build_database
from app.models.assessment import AssessmentResult
from app.models.base import utcnow
from app.models.cbt import CBTRecord
from app.models.chat_session import ChatSession
from app.models.exercise_completion import ExerciseCompletion
from app.models.journal import JournalEntry
from app.models.message import Message
from app.models.mood import MoodEntry
from app.models.plan import PlanItem
from app.models.privacy import PrivacySetting
from app.models.safety_event import SafetyEvent
from app.models.share import ClinicianShare

#: Tables that hold a person's own rows and carry ``created_at``. ``messages``
#: is handled separately: it has no ``user_id``, so ownership comes through
#: ``chat_sessions``.
_RETENTION_MODELS: tuple[tuple[str, Any], ...] = (
    ("chat_sessions", ChatSession),
    ("journal_entries", JournalEntry),
    ("mood_entries", MoodEntry),
    ("cbt_records", CBTRecord),
    ("assessment_results", AssessmentResult),
    ("exercise_completions", ExerciseCompletion),
    ("plan_items", PlanItem),
    ("safety_events", SafetyEvent),
    # Day 47: share rows and their audit lines. Expiry makes a link stop working
    # the moment it passes; this is what removes the ciphertext afterwards. The
    # events cascade with the share by foreign key.
    ("clinician_shares", ClinicianShare),
)


@dataclass(frozen=True)
class UserRetentionPlan:
    """What one person's window would remove on this run."""

    user_id: uuid.UUID
    retention_days: int
    cutoff: datetime
    rows: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.rows.values())


@dataclass
class RetentionReport:
    """The whole run: who was considered, what was removed."""

    ran_at: datetime
    dry_run: bool
    users_considered: int = 0
    users_affected: int = 0
    rows_deleted: dict[str, int] = field(default_factory=dict)
    per_user: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_deleted(self) -> int:
        return sum(self.rows_deleted.values())

    def as_document(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at.isoformat(),
            "dry_run": self.dry_run,
            "users_considered": self.users_considered,
            "users_affected": self.users_affected,
            "rows_deleted": dict(self.rows_deleted),
            "total_deleted": self.total_deleted,
            "per_user": self.per_user,
        }


async def _count_for_user(
    session: AsyncSession, model: Any, *, user_id: uuid.UUID, cutoff: datetime
) -> int:
    statement = (
        select(func.count())
        .select_from(model)
        .where(model.user_id == user_id, model.created_at < cutoff)
    )
    return int((await session.execute(statement)).scalar() or 0)


async def _count_messages(session: AsyncSession, *, user_id: uuid.UUID, cutoff: datetime) -> int:
    sessions = select(ChatSession.id).where(ChatSession.user_id == user_id)
    statement = (
        select(func.count())
        .select_from(Message)
        .where(Message.session_id.in_(sessions), Message.created_at < cutoff)
    )
    return int((await session.execute(statement)).scalar() or 0)


async def plan_retention(
    session: AsyncSession, *, now: datetime | None = None
) -> list[UserRetentionPlan]:
    """Work out what a run would delete, for every person with a window set."""
    moment = now or utcnow()
    rows = (
        (
            await session.execute(
                select(PrivacySetting).where(PrivacySetting.retention_days.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    plans: list[UserRetentionPlan] = []
    for setting in rows:
        days = setting.retention_days
        if days is None:  # defensive: the query already filtered these out
            continue
        cutoff = moment - timedelta(days=int(days))
        counts: dict[str, int] = {
            "messages": await _count_messages(session, user_id=setting.user_id, cutoff=cutoff)
        }
        for name, model in _RETENTION_MODELS:
            counts[name] = await _count_for_user(
                session, model, user_id=setting.user_id, cutoff=cutoff
            )
        if sum(counts.values()) == 0:
            # Nothing to do: an untouched window is not an "affected user".
            continue
        plans.append(
            UserRetentionPlan(
                user_id=setting.user_id,
                retention_days=int(days),
                cutoff=cutoff,
                rows={name: count for name, count in counts.items() if count},
            )
        )
    return plans


async def apply_retention(
    session: AsyncSession, *, now: datetime | None = None, dry_run: bool = False
) -> RetentionReport:
    """Delete rows older than each person's window; return the counts.

    With ``dry_run=True`` nothing is written — the report is exactly what the
    real run would do, which is what the CLI prints before its first write.
    """
    moment = now or utcnow()
    settings_rows = (
        (
            await session.execute(
                select(PrivacySetting).where(PrivacySetting.retention_days.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    report = RetentionReport(ran_at=moment, dry_run=dry_run, users_considered=len(settings_rows))

    for setting in settings_rows:
        days = setting.retention_days
        if days is None:
            continue
        cutoff = moment - timedelta(days=int(days))
        deleted: dict[str, int] = {}

        messages = await _delete_messages(
            session, user_id=setting.user_id, cutoff=cutoff, dry_run=dry_run
        )
        if messages:
            deleted["messages"] = messages
        for name, model in _RETENTION_MODELS:
            statement = (
                delete(model)
                .where(model.user_id == setting.user_id, model.created_at < cutoff)
                .execution_options(synchronize_session=False)
            )
            if dry_run:
                count = await _count_for_user(
                    session, model, user_id=setting.user_id, cutoff=cutoff
                )
            else:
                result = await session.execute(statement)
                count = int(getattr(result, "rowcount", 0) or 0)
            if count:
                deleted[name] = count

        if not deleted:
            continue
        report.users_affected += 1
        for name, count in deleted.items():
            report.rows_deleted[name] = report.rows_deleted.get(name, 0) + count
        report.per_user.append(
            {
                "user_id": str(setting.user_id),
                "retention_days": int(days),
                "cutoff": cutoff.isoformat(),
                "rows": deleted,
            }
        )

    if not dry_run:
        await session.commit()
    return report


async def _delete_messages(
    session: AsyncSession, *, user_id: uuid.UUID, cutoff: datetime, dry_run: bool
) -> int:
    """Delete old messages of one person (ownership via ``chat_sessions``)."""
    if dry_run:
        return await _count_messages(session, user_id=user_id, cutoff=cutoff)
    sessions = select(ChatSession.id).where(ChatSession.user_id == user_id)
    statement = delete(Message).where(Message.session_id.in_(sessions), Message.created_at < cutoff)
    result = await session.execute(statement)
    return int(getattr(result, "rowcount", 0) or 0)


async def _run_cli(*, dry_run: bool, as_json: bool, database: Database | None = None) -> int:
    built = database or build_database(Settings())
    try:
        async with built.session_factory() as session:
            report = await apply_retention(session, dry_run=dry_run)
    finally:
        if database is None:
            await built.dispose()

    if as_json:
        print(json.dumps(report.as_document(), indent=2))
        return 0

    mode = "DRY RUN — nothing was deleted" if dry_run else "applied"
    print(f"retention {mode}")
    print(f"  users with a window: {report.users_considered}")
    print(f"  users affected:      {report.users_affected}")
    print(f"  rows {'that would go' if dry_run else 'deleted'}: {report.total_deleted}")
    for name, count in sorted(report.rows_deleted.items()):
        print(f"    {name}: {count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``python -m app.services.privacy.retention``."""
    parser = argparse.ArgumentParser(
        description=(
            "Delete personal data older than each person's retention window. "
            "With --dry-run (recommended first) nothing is written."
        )
    )
    parser.add_argument("--dry-run", action="store_true", help="report only; delete nothing")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    return asyncio.run(_run_cli(dry_run=args.dry_run, as_json=args.json))


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
