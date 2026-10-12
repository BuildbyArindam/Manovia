"""Hard account deletion: count, delete, and hand back a content-free receipt.

Deleting an account is one ``DELETE`` on ``users`` plus the foreign-key cascade
every child table was built with (``ON DELETE CASCADE`` since Day 3). That is
the whole mechanism, and it is deliberately the *only* one: there is no
soft-delete path here, no "we will get to it", and no archive table that keeps a
copy "just in case". Destroying ``users.data_key_encrypted`` in the same
statement destroys the per-account data key, so ciphertext that somehow survived
elsewhere (a backup, a replica) is no longer readable with the application's
master key alone.

Two deliberate limits, stated rather than hidden:

* **Rows that belong to nobody are not touched.** ``safety_events`` written for
  an ephemeral (never-saved) conversation have ``user_id IS NULL`` by design
  (ADR 0011 §3) — there is no link to this account to delete, and deleting the
  whole table would erase an audit trail that other people's safety depends on.
* **The receipt is not a person.** ``account_ref`` is an HMAC over the deleted
  user id, and the only payload is per-table row counts, so the receipt proves
  the deletion happened without becoming a new copy of the account.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentResult
from app.models.base import utcnow
from app.models.cbt import CBTRecord
from app.models.chat_session import ChatSession
from app.models.consent import Consent
from app.models.exercise_completion import ExerciseCompletion
from app.models.journal import JournalEntry
from app.models.message import Message
from app.models.mood import MoodEntry
from app.models.plan import PlanItem
from app.models.privacy import DeletionReceipt, PrivacySetting
from app.models.refresh_token import RefreshToken
from app.models.safety_event import SafetyEvent
from app.models.user import User
from app.models.user_settings import UserSettings

#: Every table that can hold a row belonging to one account, in the order the
#: receipt lists them. ``messages`` has no ``user_id`` and is counted through
#: its session (see :func:`count_user_rows`).
USER_OWNED_MODELS: tuple[tuple[str, Any], ...] = (
    ("consents", Consent),
    ("chat_sessions", ChatSession),
    ("mood_entries", MoodEntry),
    ("journal_entries", JournalEntry),
    ("assessment_results", AssessmentResult),
    ("exercise_completions", ExerciseCompletion),
    ("cbt_records", CBTRecord),
    ("plan_items", PlanItem),
    ("safety_events", SafetyEvent),
    ("refresh_tokens", RefreshToken),
    ("user_settings", UserSettings),
    ("privacy_settings", PrivacySetting),
)


@dataclass(frozen=True)
class DeletionResult:
    """The receipt returned to the caller (and stored, without the contents)."""

    receipt_id: uuid.UUID
    account_ref: str
    deleted_at: datetime
    rows_deleted: dict[str, int] = field(default_factory=dict)
    irreversible: bool = True

    @property
    def total_rows(self) -> int:
        return sum(self.rows_deleted.values())


def account_reference(user_id: uuid.UUID, *, secret_key: str) -> str:
    """A stable, non-reversible handle for a deleted account.

    Truncated to 32 hex characters: enough to match a receipt the user was
    shown against a log line, far too little to brute-force a UUID back out of,
    and useless without the deployment's ``SECRET_KEY``.
    """
    digest = hmac.new(secret_key.encode("utf-8"), str(user_id).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:32]


async def count_user_rows(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """How many rows of each table belong to this account (messages via session)."""
    counts: dict[str, int] = {}
    for name, model in USER_OWNED_MODELS:
        statement = select(func.count()).select_from(model).where(model.user_id == user_id)
        counts[name] = int((await session.execute(statement)).scalar() or 0)
    session_ids = select(ChatSession.id).where(ChatSession.user_id == user_id)
    messages = select(func.count()).select_from(Message).where(Message.session_id.in_(session_ids))
    counts["messages"] = int((await session.execute(messages)).scalar() or 0)
    return counts


async def delete_account(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    secret_key: str,
    requested_via: str = "api",
    now: datetime | None = None,
) -> DeletionResult:
    """Delete the account and every row that hangs off it; write the receipt.

    The caller owns the transaction: nothing is committed here, so a failure
    anywhere (including while writing the receipt) rolls the whole deletion back
    rather than leaving a half-erased account behind.
    """
    moment = now or utcnow()
    counts = await count_user_rows(session, user_id)

    outcome = await session.execute(delete(User).where(User.id == user_id))
    if int(getattr(outcome, "rowcount", 0) or 0) != 1:
        raise LookupError("no such account")

    reference = account_reference(user_id, secret_key=secret_key)
    receipt = DeletionReceipt(
        account_ref=reference,
        requested_via=requested_via,
        rows_deleted={name: count for name, count in counts.items() if count},
        created_at=moment,
    )
    session.add(receipt)
    await session.flush()

    return DeletionResult(
        receipt_id=receipt.id,
        account_ref=reference,
        deleted_at=moment,
        rows_deleted=dict(receipt.rows_deleted),
    )
