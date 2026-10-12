"""Building the owner's own copy of everything Manovia stores about them.

The export is the counterweight to encryption: a person cannot read their
ciphertext in a database viewer, so the product has to be able to hand it back —
**decrypted, for the owner only, over an authenticated request**.

Shape of the archive (a ZIP, or the same content as one JSON document):

    README.txt            what this is, in plain words
    manifest.json         format version, generated_at, file list, row counts
    profile.json          account fields (no password hash, ever)
    consents.json         the append-only consent history
    privacy-settings.json the three Day 22 choices, and their defaults
    chat.json             saved sessions, each with its decrypted turns
    journal.json          decrypted titles and bodies
    mood.json             check-ins with decrypted notes
    assessments.json      PHQ-9 / GAD-7 totals, bands and item answers
    exercises.json        exercise completions
    cbt.json              thought records, decrypted
    plan.json             daily-plan suggestions and their status
    safety-events.json    metadata-only safety audit rows (no text exists)

Two rules the code keeps:

1. **Nothing here is reachable without the owner's token.** The service takes a
   user and reads only rows whose ``user_id`` is that user.
2. **A row that will not decrypt is reported, not dropped and not faked.** A
   missing key (``FIELD_ENCRYPTION_KEY`` rotated away) yields
   ``"decryption_failed": true`` on that row and a count in the manifest; an
   export that silently omitted half a journal would be worse than useless.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import DecryptError, decrypt, decrypt_field, decrypt_optional
from app.db.repos import ChatRepository
from app.models.assessment import AssessmentResult
from app.models.base import utcnow
from app.models.cbt import CBT_TEXT_FIELDS, CBTRecord, cbt_aad
from app.models.chat_session import ChatSession
from app.models.consent import Consent
from app.models.exercise_completion import ExerciseCompletion
from app.models.journal import JournalEntry
from app.models.message import Message
from app.models.mood import MoodEntry
from app.models.plan import PlanItem
from app.models.privacy import PrivacySetting
from app.models.safety_event import SafetyEvent
from app.models.user import User
from app.services.privacy.store import DEFAULT_SNAPSHOT

#: Bumped when the archive layout changes in a way a reader must know about.
EXPORT_FORMAT_VERSION = "manovia-export-1"

#: Per-table ceiling. An export must finish and must not become a memory bomb;
#: when a table hits the ceiling the manifest says so explicitly, and the API
#: still returns everything up to it.
DEFAULT_MAX_ROWS_PER_TABLE = 5000

_README = """Manovia — your data export
==========================

This archive is everything Manovia stores about you, decrypted for you:
your chat history (only the conversations you chose to save), journal
entries, mood check-ins, self-screening results, guided-exercise history,
thought records, your daily-plan rows and your consent history.

What is NOT in here:
  * your password (only a one-way Argon2id hash is stored, and it never
    leaves the server),
  * your encryption keys,
  * anything about anyone else.

About the numbers: 'valence' and 'energy' are the 1-5 scales from a mood
check-in. 'sentiment' is a machine estimate between -1 and 1 that only
exists if you left sentiment scores on.

Your data was encrypted at rest; this copy is plain text, so keep it
somewhere you trust, and delete it when you are done.

Manovia is a self-help companion, not therapy, not a diagnosis and not a
crisis service. If you are in danger, contact your local emergency number.
"""


@dataclass(frozen=True)
class ExportFile:
    """One file inside the archive."""

    name: str
    content: bytes


@dataclass
class UserExport:
    """The assembled export, before it is zipped or serialised."""

    user_id: uuid.UUID
    generated_at: datetime
    files: list[ExportFile] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    #: Tables whose export was cut off at ``max_rows``; the manifest says so.
    truncated: dict[str, int] = field(default_factory=dict)
    #: Rows whose ciphertext could not be decrypted with the active key.
    unreadable: dict[str, int] = field(default_factory=dict)
    format_version: str = EXPORT_FORMAT_VERSION

    def file(self, name: str) -> ExportFile | None:
        """Find one file by name (used by the tests and the JSON view)."""
        for item in self.files:
            if item.name == name:
                return item
        return None

    def as_document(self) -> dict[str, Any]:
        """The whole export as one JSON-serialisable document."""
        return {
            "format_version": self.format_version,
            "generated_at": self.generated_at.isoformat(),
            "counts": dict(self.counts),
            "truncated": dict(self.truncated),
            "unreadable": dict(self.unreadable),
            "files": {item.name: _parse(item) for item in self.files},
        }


def _dump(value: Any) -> bytes:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False).encode("utf-8")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(item: ExportFile) -> Any:
    """JSON files become objects; the README stays a string."""
    text = item.content.decode("utf-8")
    if not item.name.endswith(".json"):
        return text
    return json.loads(text)


async def _scalars(
    session: AsyncSession, statement: Any, *, max_rows: int
) -> tuple[list[Any], bool]:
    """Fetch at most ``max_rows`` rows, and say whether more existed.

    One extra row is requested so "the export was truncated" is a fact the
    manifest can state rather than a guess based on an exact-length result.
    """
    result = await session.execute(statement.limit(max_rows + 1))
    rows = list(result.scalars().all())
    return rows[:max_rows], len(rows) > max_rows


def _decrypt_or_flag(
    blob: bytes,
    *,
    user_id: uuid.UUID,
    field_name: str | None,
    wrapped_dek: bytes | None,
    row: dict[str, Any],
    key: str,
) -> None:
    """Decrypt one field into ``row[key]``, or mark the row unreadable.

    AAD-bound fields (everything under envelope encryption) verify
    ``(user_id, field_name)``, so this cannot be fooled by a ciphertext copied
    from another account or another column.
    """
    try:
        if field_name is None:
            row[key] = decrypt(blob)
        else:
            row[key] = decrypt_field(
                blob, user_id=user_id, field_name=field_name, wrapped_dek=wrapped_dek
            )
    except (DecryptError, ValueError):
        row[key] = None
        row["decryption_failed"] = True


async def build_user_export(
    session: AsyncSession,
    user: User,
    *,
    now: datetime | None = None,
    max_rows_per_table: int = DEFAULT_MAX_ROWS_PER_TABLE,
) -> UserExport:
    """Assemble everything Manovia stores for ``user``, decrypted for its owner."""
    generated_at = now or utcnow()
    export = UserExport(user_id=user.id, generated_at=generated_at)
    uid = user.id

    # --- account ----------------------------------------------------------
    export.files.append(
        ExportFile(
            "profile.json",
            _dump(
                {
                    "id": str(user.id),
                    "email": user.email,
                    "is_anonymous": bool(user.is_anonymous),
                    "language": user.language,
                    "region": user.region,
                    "created_at": _iso(user.created_at),
                    "deleted_at": _iso(user.deleted_at),
                    "note": "password_hash is deliberately not exported; it is a one-way hash.",
                }
            ),
        )
    )

    # --- consent history ---------------------------------------------------
    consents, _ = await _scalars(
        session,
        select(Consent).where(Consent.user_id == uid).order_by(Consent.created_at, Consent.id),
        max_rows=max_rows_per_table,
    )
    export.counts["consents"] = len(consents)
    export.files.append(
        ExportFile(
            "consents.json",
            _dump(
                [
                    {
                        "kind": row.kind.value,
                        "version": row.version,
                        "granted": bool(row.granted),
                        "created_at": _iso(row.created_at),
                    }
                    for row in consents
                ]
            ),
        )
    )

    # --- privacy settings --------------------------------------------------
    privacy = await session.get(PrivacySetting, uid)
    export.files.append(
        ExportFile(
            "privacy-settings.json",
            _dump(
                {
                    "store_chat": bool(privacy.store_chat)
                    if privacy
                    else DEFAULT_SNAPSHOT.store_chat,
                    "store_sentiment": bool(privacy.store_sentiment)
                    if privacy
                    else DEFAULT_SNAPSHOT.store_sentiment,
                    "retention_days": privacy.retention_days if privacy else None,
                    "updated_at": _iso(privacy.updated_at) if privacy else None,
                    "been_saved": privacy is not None,
                    "note": (
                        "retention_days null means 'keep until I delete it'. "
                        "store_chat false is a veto on saving new chat turns."
                    ),
                }
            ),
        )
    )

    # --- chat --------------------------------------------------------------
    chat_repo = ChatRepository(session)
    sessions, sessions_cut = await _scalars(
        session,
        select(ChatSession)
        .where(ChatSession.user_id == uid)
        .order_by(ChatSession.created_at, ChatSession.id),
        max_rows=max_rows_per_table,
    )
    chat_document: list[dict[str, Any]] = []
    message_count = 0
    unreadable_messages = 0
    for chat_session in sessions:
        messages, _ = await _scalars(
            session,
            select(Message)
            .where(Message.session_id == chat_session.id)
            .order_by(Message.created_at, Message.id),
            max_rows=max_rows_per_table,
        )
        memory, memory_deleted = await chat_repo.memory_summary(chat_session.id)
        turns: list[dict[str, Any]] = []
        for message in messages:
            row: dict[str, Any] = {
                "id": str(message.id),
                "role": message.role.value,
                "risk_level": int(message.risk_level),
                "emotion": message.emotion,
                "created_at": _iso(message.created_at),
            }
            try:
                row["text"] = decrypt(message.content_encrypted)
            except (DecryptError, ValueError):
                row["text"] = None
                row["decryption_failed"] = True
                unreadable_messages += 1
            turns.append(row)
        message_count += len(turns)
        chat_document.append(
            {
                "id": str(chat_session.id),
                "created_at": _iso(chat_session.created_at),
                "ended_at": _iso(chat_session.ended_at),
                "memory_summary": memory,
                "memory_deleted": memory_deleted,
                "messages": turns,
            }
        )
    export.counts["chat_sessions"] = len(chat_document)
    export.counts["messages"] = message_count
    export.unreadable["messages"] = unreadable_messages
    if sessions_cut:
        export.truncated["chat_sessions"] = max_rows_per_table
    export.files.append(ExportFile("chat.json", _dump(chat_document)))

    # --- journal -----------------------------------------------------------
    entries, entries_cut = await _scalars(
        session,
        select(JournalEntry)
        .where(JournalEntry.user_id == uid)
        .order_by(JournalEntry.created_at, JournalEntry.id),
        max_rows=max_rows_per_table,
    )
    journal_document: list[dict[str, Any]] = []
    unreadable_entries = 0
    for entry in entries:
        row = {
            "id": str(entry.id),
            "sentiment": entry.sentiment,
            "emotion_scores": entry.emotion_scores,
            "template": entry.template,
            "prompt_id": entry.prompt_id,
            "created_at": _iso(entry.created_at),
            "updated_at": _iso(entry.updated_at),
        }
        faulty = False
        _decrypt_or_flag(
            entry.title_encrypted,
            user_id=uid,
            field_name="title",
            wrapped_dek=user.data_key_encrypted,
            row=row,
            key="title",
        )
        _decrypt_or_flag(
            entry.body_encrypted,
            user_id=uid,
            field_name="body",
            wrapped_dek=user.data_key_encrypted,
            row=row,
            key="body",
        )
        faulty = bool(row.get("decryption_failed"))
        if faulty:
            unreadable_entries += 1
        journal_document.append(row)
    export.counts["journal_entries"] = len(journal_document)
    export.unreadable["journal_entries"] = unreadable_entries
    if entries_cut:
        export.truncated["journal_entries"] = max_rows_per_table
    export.files.append(ExportFile("journal.json", _dump(journal_document)))

    # --- mood --------------------------------------------------------------
    moods, moods_cut = await _scalars(
        session,
        select(MoodEntry)
        .where(MoodEntry.user_id == uid)
        .order_by(MoodEntry.created_at, MoodEntry.id),
        max_rows=max_rows_per_table,
    )
    export.counts["mood_entries"] = len(moods)
    if moods_cut:
        export.truncated["mood_entries"] = max_rows_per_table
    export.files.append(
        ExportFile(
            "mood.json",
            _dump(
                [
                    {
                        "id": str(row.id),
                        "valence": row.valence,
                        "energy": row.energy,
                        "emotions": list(row.emotions or []),
                        "factors": row.factors or {},
                        "note": decrypt_optional(row.note_encrypted),
                        "created_at": _iso(row.created_at),
                    }
                    for row in moods
                ]
            ),
        )
    )

    # --- assessments -------------------------------------------------------
    assessments, assessments_cut = await _scalars(
        session,
        select(AssessmentResult)
        .where(AssessmentResult.user_id == uid)
        .order_by(AssessmentResult.created_at, AssessmentResult.id),
        max_rows=max_rows_per_table,
    )
    export.counts["assessment_results"] = len(assessments)
    if assessments_cut:
        export.truncated["assessment_results"] = max_rows_per_table
    export.files.append(
        ExportFile(
            "assessments.json",
            _dump(
                [
                    {
                        "id": str(row.id),
                        "instrument": row.instrument.value,
                        "answers": row.answers,
                        "total": row.total,
                        "band": row.band.value,
                        "created_at": _iso(row.created_at),
                        "note": "A screening score, not a diagnosis.",
                    }
                    for row in assessments
                ]
            ),
        )
    )

    # --- exercises ---------------------------------------------------------
    completions, completions_cut = await _scalars(
        session,
        select(ExerciseCompletion)
        .where(ExerciseCompletion.user_id == uid)
        .order_by(ExerciseCompletion.created_at, ExerciseCompletion.id),
        max_rows=max_rows_per_table,
    )
    export.counts["exercise_completions"] = len(completions)
    if completions_cut:
        export.truncated["exercise_completions"] = max_rows_per_table
    export.files.append(
        ExportFile(
            "exercises.json",
            _dump(
                [
                    {
                        "id": str(row.id),
                        "exercise_id": row.exercise_id,
                        "exercise_title": row.exercise_title,
                        "definition_sha256": row.definition_sha256,
                        "duration_seconds": row.duration_seconds,
                        "pre_rating": row.pre_rating,
                        "post_rating": row.post_rating,
                        "created_at": _iso(row.created_at),
                    }
                    for row in completions
                ]
            ),
        )
    )

    # --- CBT thought records ----------------------------------------------
    records, records_cut = await _scalars(
        session,
        select(CBTRecord)
        .where(CBTRecord.user_id == uid)
        .order_by(CBTRecord.created_at, CBTRecord.id),
        max_rows=max_rows_per_table,
    )
    cbt_document: list[dict[str, Any]] = []
    unreadable_records = 0
    for record in records:
        cbt_row: dict[str, Any] = {
            "id": str(record.id),
            "emotion": record.emotion,
            "intensity_before": record.intensity_before,
            "intensity_after": record.intensity_after,
            "what_helped": list(record.what_helped or []),
            "created_at": _iso(record.created_at),
        }
        # The AAD comes from the model, never from a literal here: the export
        # must read a row exactly as the write path encrypted it.
        for field_name in CBT_TEXT_FIELDS:
            blob = getattr(record, f"{field_name}_encrypted")
            _decrypt_or_flag(
                blob,
                user_id=uid,
                field_name=cbt_aad(field_name),
                wrapped_dek=user.data_key_encrypted,
                row=cbt_row,
                key=field_name,
            )
        if cbt_row.get("decryption_failed"):
            unreadable_records += 1
        cbt_document.append(cbt_row)
    export.counts["cbt_records"] = len(cbt_document)
    export.unreadable["cbt_records"] = unreadable_records
    if records_cut:
        export.truncated["cbt_records"] = max_rows_per_table
    export.files.append(ExportFile("cbt.json", _dump(cbt_document)))

    # --- daily plan --------------------------------------------------------
    plan_items, plan_cut = await _scalars(
        session,
        select(PlanItem).where(PlanItem.user_id == uid).order_by(PlanItem.created_at, PlanItem.id),
        max_rows=max_rows_per_table,
    )
    export.counts["plan_items"] = len(plan_items)
    if plan_cut:
        export.truncated["plan_items"] = max_rows_per_table
    export.files.append(
        ExportFile(
            "plan.json",
            _dump(
                [
                    {
                        "id": str(row.id),
                        "plan_date": row.plan_date.isoformat(),
                        "kind": row.kind.value,
                        "ref": row.ref,
                        "title": row.title,
                        "why": row.why,
                        "status": row.status.value,
                        "created_at": _iso(row.created_at),
                    }
                    for row in plan_items
                ]
            ),
        )
    )

    # --- safety events (metadata only; no free-text column exists) ---------
    events, events_cut = await _scalars(
        session,
        select(SafetyEvent)
        .where(SafetyEvent.user_id == uid)
        .order_by(SafetyEvent.created_at, SafetyEvent.id),
        max_rows=max_rows_per_table,
    )
    export.counts["safety_events"] = len(events)
    if events_cut:
        export.truncated["safety_events"] = max_rows_per_table
    export.files.append(
        ExportFile(
            "safety-events.json",
            _dump(
                [
                    {
                        "id": str(row.id),
                        "risk_level": int(row.risk_level),
                        "source": row.source.value,
                        "category": row.category.value if row.category else None,
                        "session_id": str(row.session_id) if row.session_id else None,
                        "created_at": _iso(row.created_at),
                        "note": "Metadata only — Manovia never stores your words in this table.",
                    }
                    for row in events
                ]
            ),
        )
    )

    # --- manifest ----------------------------------------------------------
    manifest = {
        "format_version": export.format_version,
        "generated_at": export.generated_at.isoformat(),
        "user_id": str(uid),
        "files": [item.name for item in export.files] + ["README.txt", "manifest.json"],
        "counts": dict(export.counts),
        "truncated_tables": dict(export.truncated),
        "unreadable_rows": dict(export.unreadable),
        "max_rows_per_table": max_rows_per_table,
        "contents_are_decrypted": True,
        "encryption_note": (
            "Your data is encrypted at rest with a per-account key. This archive is a "
            "plain-text copy made for you, the account owner."
        ),
    }
    export.files.append(ExportFile("manifest.json", _dump(manifest)))
    export.files.append(ExportFile("README.txt", _README.encode("utf-8")))
    return export


def render_zip(export: UserExport) -> bytes:
    """Render the export as a ZIP archive with stable file order and timestamps."""
    buffer = io.BytesIO()
    stamp = (1980, 1, 1, 0, 0, 0)  # fixed: two exports of the same data are byte-stable
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in _ordered(export.files):
            info = zipfile.ZipInfo(filename=f"manovia-export/{item.name}", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, item.content)
    return buffer.getvalue()


def _ordered(files: Iterable[ExportFile]) -> Sequence[ExportFile]:
    """The archive listing: the reader first, then the manifest, then the data."""
    ordered = list(files)
    ordered.sort(
        key=lambda item: (item.name != "README.txt", item.name != "manifest.json", item.name)
    )
    return ordered
