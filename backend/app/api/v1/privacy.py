"""Privacy controls: ``GET/PATCH /privacy/settings``, consent history, export, delete.

Four surfaces, all of them about the same promise — *your data, your call*:

==================================================  ==========================================
route                                               what it does
==================================================  ==========================================
``GET   /privacy/settings``                         the three choices, with their defaults
``PATCH /privacy/settings``                         change ``store_chat``, ``store_sentiment``,
                                                    ``retention_days``
``GET   /privacy/consents``                         the append-only consent history
``GET   /privacy/export?format=zip|json``           everything we hold, decrypted, as a download
``DELETE /privacy/account``                         re-auth, then hard delete + a receipt
==================================================  ==========================================

**These routes need authentication, not consent.** Every other personal surface
in Manovia is built on ``require_consent(TERMS, PRIVACY)``, and these four
deliberately are not: somebody who has withdrawn consent must still be able to
take a copy of their data and to have it deleted. Gating a right behind an
agreement would be the same as not offering it.

**The export is the only place plaintext leaves the database deliberately.** It
is built for the owner, over an authenticated request, with a
``Content-Disposition: attachment`` so a browser saves it instead of rendering
it — and it is never cached (the API sends ``Cache-Control: no-store`` on every
response).

**Deletion returns a receipt, never content.** ``rows_deleted`` is a per-table
count; the identifier is an HMAC (see :mod:`app.services.privacy.deletion`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentUser, LockoutDep, SessionDep
from app.content import load_consent_documents
from app.core.errors import ApiError
from app.core.passwords import get_password_hasher
from app.db.repos import ConsentRepository
from app.models.privacy import (
    RETENTION_DAY_CHOICES,
    RETENTION_MAX_DAYS,
    RETENTION_MIN_DAYS,
    PrivacySetting,
)
from app.services.privacy import (
    EXPORT_FORMAT_VERSION,
    build_user_export,
    delete_account,
    load_privacy,
    render_zip,
)

router = APIRouter(prefix="/privacy", tags=["privacy"])

_log = structlog.get_logger()

#: The word a person has to type to delete their account. Deliberately blunt and
#: deliberately not localised into a guess: the UI shows this exact string.
CONFIRMATION_PHRASE = "DELETE"


def _lockout_key(user_id: uuid.UUID) -> str:
    """Lockout bucket for delete re-auth, kept apart from sign-in counters."""
    return f"delete:{user_id}"


# --------------------------------------------------------------------------- #
# Settings                                                                      #
# --------------------------------------------------------------------------- #


class PrivacySettingsOut(BaseModel):
    """The person's effective privacy choices, plus the vocabulary to render them."""

    store_chat: bool
    store_sentiment: bool
    retention_days: int | None
    #: True once the person has saved anything; False means these are defaults.
    been_saved: bool
    #: Closed set of windows the UI offers, in days.
    retention_choices: list[int] = Field(default_factory=lambda: list(RETENTION_DAY_CHOICES))
    retention_min_days: int = RETENTION_MIN_DAYS
    retention_max_days: int = RETENTION_MAX_DAYS
    updated_at: datetime | None = None


class PrivacySettingsPatch(BaseModel):
    """A partial update: only the fields present are changed."""

    store_chat: bool | None = None
    store_sentiment: bool | None = None
    #: ``None`` is meaningful here: it means "keep my data until I delete it",
    #: so the field is read from ``model_fields_set`` rather than from its value.
    retention_days: int | None = None

    @field_validator("retention_days")
    @classmethod
    def _within_bounds(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not RETENTION_MIN_DAYS <= value <= RETENTION_MAX_DAYS:
            raise ValueError(
                f"retention_days must be between {RETENTION_MIN_DAYS} and {RETENTION_MAX_DAYS}, "
                "or null to keep data until deletion"
            )
        return value


def _privacy_out(
    setting: PrivacySetting | None, snapshot_store_chat: bool, snapshot_store_sentiment: bool
) -> PrivacySettingsOut:
    if setting is None:
        return PrivacySettingsOut(
            store_chat=snapshot_store_chat,
            store_sentiment=snapshot_store_sentiment,
            retention_days=None,
            been_saved=False,
        )
    return PrivacySettingsOut(
        store_chat=bool(setting.store_chat),
        store_sentiment=bool(setting.store_sentiment),
        retention_days=setting.retention_days,
        been_saved=True,
        updated_at=setting.updated_at,
    )


@router.get("/settings", response_model=PrivacySettingsOut)
async def get_privacy_settings(user: CurrentUser, session: SessionDep) -> PrivacySettingsOut:
    """The caller's privacy choices (the product defaults if never saved)."""
    snapshot = await load_privacy(session, user.id)
    row = await session.get(PrivacySetting, user.id)
    return _privacy_out(row, snapshot.store_chat, snapshot.store_sentiment)


@router.patch("/settings", response_model=PrivacySettingsOut)
async def patch_privacy_settings(
    payload: PrivacySettingsPatch,
    user: CurrentUser,
    session: SessionDep,
) -> PrivacySettingsOut:
    """Change one or more privacy choices, creating the row on first write."""
    if not payload.model_fields_set:
        raise ApiError(
            422, "empty_update", "Send at least one of store_chat, store_sentiment, retention_days."
        )

    row = await session.get(PrivacySetting, user.id)
    if row is None:
        row = PrivacySetting(user_id=user.id)
        session.add(row)

    if "store_chat" in payload.model_fields_set and payload.store_chat is not None:
        row.store_chat = payload.store_chat
    if "store_sentiment" in payload.model_fields_set and payload.store_sentiment is not None:
        row.store_sentiment = payload.store_sentiment
    if "retention_days" in payload.model_fields_set:
        row.retention_days = payload.retention_days

    await session.flush()
    await session.commit()
    await session.refresh(row)

    # Counts and booleans only: never a person's words (AGENTS.md rule 5).
    _log.info(
        "privacy.settings_updated",
        user_id=str(user.id),
        store_chat=bool(row.store_chat),
        store_sentiment=bool(row.store_sentiment),
        retention_days=row.retention_days,
    )
    return _privacy_out(row, row.store_chat, row.store_sentiment)


# --------------------------------------------------------------------------- #
# Consent history                                                               #
# --------------------------------------------------------------------------- #


class ConsentHistoryItem(BaseModel):
    kind: str
    version: str
    granted: bool
    created_at: datetime
    #: True when this row matches the document version currently in force.
    current_version: bool


class ConsentHistoryOut(BaseModel):
    items: list[ConsentHistoryItem]
    #: Current document versions, so the UI can say what "current" means.
    current_versions: dict[str, str]


@router.get("/consents", response_model=ConsentHistoryOut)
async def list_consent_history(user: CurrentUser, session: SessionDep) -> ConsentHistoryOut:
    """Every agreement and withdrawal this account ever recorded, newest first."""
    documents = load_consent_documents()
    rows = await ConsentRepository(session).history(user.id)
    items = [
        ConsentHistoryItem(
            kind=row.kind.value,
            version=row.version,
            granted=bool(row.granted),
            created_at=row.created_at,
            current_version=documents.version_for(row.kind) == row.version,
        )
        for row in rows
    ]
    items.sort(key=lambda item: item.created_at, reverse=True)
    return ConsentHistoryOut(
        items=items,
        current_versions={document.kind.value: document.version for document in documents.all()},
    )


# --------------------------------------------------------------------------- #
# Export                                                                        #
# --------------------------------------------------------------------------- #


@router.get("/export")
async def export_user_data(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    format: Annotated[Literal["zip", "json"], Query()] = "zip",
) -> Response:
    """Every row Manovia holds for the caller, decrypted, as a download."""
    settings: Any = request.app.state.settings
    export = await build_user_export(
        session, user, max_rows_per_table=settings.privacy_export_max_rows
    )
    stamp = export.generated_at.strftime("%Y%m%d")

    # Counts only: the export's *contents* must never reach a log line.
    _log.info(
        "privacy.export",
        user_id=str(user.id),
        format=format,
        counts=dict(export.counts),
        truncated=dict(export.truncated),
        unreadable=dict(export.unreadable),
    )

    if format == "json":
        return JSONResponse(
            content=export.as_document(),
            headers={
                "Content-Disposition": f'attachment; filename="manovia-export-{stamp}.json"',
                "Cache-Control": "no-store",
            },
        )

    return Response(
        content=render_zip(export),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="manovia-export-{stamp}.zip"',
            "Cache-Control": "no-store",
            "X-Export-Format": EXPORT_FORMAT_VERSION,
        },
    )


# --------------------------------------------------------------------------- #
# Account deletion                                                              #
# --------------------------------------------------------------------------- #


class DeleteAccountIn(BaseModel):
    """Re-authentication material for an irreversible request."""

    #: Must be exactly ``"DELETE"``. A person cannot delete an account by
    #: accident, and the UI can say exactly what to type.
    confirm: str = Field(max_length=32)
    #: Required for an account with a password. Anonymous (guest) accounts have
    #: no credential to re-present, so the confirmation phrase is the whole
    #: re-auth — see docs/privacy-and-threat-model.md §7.
    password: str | None = Field(default=None, max_length=256)


class DeletionReceiptOut(BaseModel):
    receipt_id: uuid.UUID
    account_ref: str
    deleted_at: datetime
    rows_deleted: dict[str, int]
    total_rows: int
    irreversible: bool = True
    note: str = (
        "Your account and its data were deleted. This receipt lists row counts only — "
        "there is no copy of your content in it."
    )


@router.delete("/account", response_model=DeletionReceiptOut)
async def delete_my_account(
    request: Request,
    payload: Annotated[DeleteAccountIn, Body()],
    user: CurrentUser,
    session: SessionDep,
    lockout: LockoutDep,
    request_via: Annotated[str, Query(max_length=16)] = "api",
) -> DeletionReceiptOut:
    """Permanently delete this account and everything that belongs to it."""
    key = _lockout_key(user.id)
    remaining = lockout.remaining_lock(key)
    if remaining is not None:
        raise ApiError(
            429,
            "account_locked",
            "Too many failed attempts. Try again later.",
            headers={"Retry-After": str(max(1, int(remaining.total_seconds())))},
        )

    if payload.confirm.strip().upper() != CONFIRMATION_PHRASE:
        raise ApiError(
            400,
            "confirmation_required",
            f"Type {CONFIRMATION_PHRASE} to confirm. Nothing was deleted.",
        )

    if user.password_hash is not None:
        if not payload.password:
            raise ApiError(
                403,
                "reauth_required",
                "Enter your password to confirm. Nothing was deleted.",
            )
        if not get_password_hasher().verify(user.password_hash, payload.password):
            lockout.record_failure(key)
            _log.warning("privacy.delete_reauth_failed", user_id=str(user.id))
            raise ApiError(
                403, "invalid_credentials", "That password is not correct. Nothing was deleted."
            )

    settings: Any = request.app.state.settings
    result = await delete_account(
        session,
        user_id=user.id,
        secret_key=settings.secret_key or "",
        requested_via=request_via.strip() or "api",
    )
    await session.commit()
    lockout.record_success(key)

    _log.info(
        "privacy.account_deleted",
        receipt_id=str(result.receipt_id),
        rows_deleted=dict(result.rows_deleted),
        requested_via=request_via,
    )
    return DeletionReceiptOut(
        receipt_id=result.receipt_id,
        account_ref=result.account_ref,
        deleted_at=result.deleted_at,
        rows_deleted=result.rows_deleted,
        total_rows=result.total_rows,
    )
