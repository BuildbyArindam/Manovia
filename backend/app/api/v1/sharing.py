"""Owner-facing endpoints for the clinician-share bridge (Day 47).

Everything here is behind the normal auth *and* the TERMS + PRIVACY consent
gate: a person who has not agreed to the privacy policy cannot create a document
that leaves the app.

The endpoints, in the order a person meets them:

``GET  /shares/options``
    The catalogue (what can be shared, and the one-line sentence that explains
    each), the deployment's defaults, and — deliberately — the *metadata* of the
    entries that can be selected. A thought record is offered as id, date,
    emotion and the two intensities. Its text is not offered, because this
    response goes to the browser before the person has confirmed anything.

``POST /shares/preferences``
    The switch. Sharing is off until it is turned on.

``POST /shares/preview``
    Build the document without storing anything, and return the consent lines
    plus their hash.

``POST /shares``
    Create the share. Requires the consent hash from the preview: if the data
    moved in between, this returns ``409 consent_mismatch`` and the person looks
    again. The response carries the token, the key and the passcode **once**.

``GET  /shares`` / ``GET /shares/{id}`` / ``GET /shares/{id}/audit``
    The person's own links, and the audit line the brief asks for: how many
    times it was opened.

``POST /shares/{id}/revoke`` / ``POST /shares/revoke-all``
    Kill one link, or all of them in one click.

Logs from this module carry counts and ids only, never document content
(AGENTS.md safety rule 5).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, require_consent
from app.core.config import Settings
from app.core.errors import ApiError
from app.models.cbt import CBTRecord
from app.models.enums import ConsentKind, ShareFormat
from app.models.share import ClinicianShare
from app.models.user import User
from app.services.sharing.items import (
    DISCLAIMER,
    DISCLAIMER_TITLE,
    MAX_NOTE_CHARS,
    MAX_QUESTIONS,
    MAX_QUESTION_CHARS,
    MAX_THOUGHT_RECORDS,
    NEVER_INCLUDED,
    ShareRequest,
    WINDOW_CHOICES,
)
from app.services.sharing.service import (
    ConsentMismatchError,
    CreateOptions,
    CreatedShare,
    ShareLimitError,
    ShareNotFoundError,
    ShareRequestError,
    ShareService,
    SharingDisabledError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/shares", tags=["sharing"])

#: Creating or reading shares needs the privacy agreement, like every other
#: data-leaves-the-app feature.
ShareUser = Annotated[
    User, Depends(require_consent(ConsentKind.TERMS, ConsentKind.PRIVACY))
]


def _settings(request: Request) -> Settings:
    """The deployment settings, the way every other router reads them."""

    settings: Settings = request.app.state.settings
    return settings


async def get_share_service(request: Request, session: SessionDep) -> ShareService:
    """Build the share service for this request."""

    return ShareService(session, _settings(request))


ShareServiceDep = Annotated[ShareService, Depends(get_share_service)]

# --------------------------------------------------------------------------- #
# request / response shapes
# --------------------------------------------------------------------------- #


class PreferenceIn(BaseModel):
    """Turn sharing on or off."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class PreferenceOut(BaseModel):
    """The state of the switch."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    deployment_enabled: bool
    updated_at: datetime | None = None


class ThoughtRecordChoice(BaseModel):
    """One selectable thought record, as metadata only.

    No text here on purpose: this list is fetched before the person confirms
    anything, and the browser does not need the words to draw a checkbox.
    """

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    created_at: datetime
    emotion: str | None = None
    intensity_before: int | None = None
    intensity_after: int | None = None


class CatalogueItemOut(BaseModel):
    """One line of the "what can be shared" catalogue."""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    blurb: str
    includes: list[str]
    excludes: list[str]
    default: bool
    needs_selection: bool


class OptionsOut(BaseModel):
    """Everything the builder page needs before the person decides anything."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    deployment_enabled: bool
    defaults: dict[str, Any]
    items: list[CatalogueItemOut]
    thought_records: list[ThoughtRecordChoice]
    never_included: list[str]
    disclaimer_title: str
    disclaimer: str
    limits: dict[str, Any]


#: Fields the create body adds on top of the selection itself.
_CREATE_ONLY = frozenset(
    {"formats", "expires_in_days", "view_limit", "passcode", "consent_sha256"}
)


class CreateIn(ShareRequest):
    """The create body: the selection plus the link's own settings."""

    model_config = ConfigDict(extra="forbid")

    formats: list[ShareFormat] = Field(
        default_factory=lambda: [ShareFormat.PDF],
        min_length=1,
        description="Which exports to build.",
    )
    expires_in_days: int | None = None
    view_limit: int | None = None
    passcode: str | None = Field(default=None, max_length=64)
    consent_sha256: str = Field(
        min_length=64,
        max_length=64,
        description="The hash returned by POST /shares/preview, echoed back.",
    )


class PreviewOut(BaseModel):
    """What would be shared, and the sentence to confirm."""

    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]
    consent_lines: list[str]
    consent_sha256: str
    payload_sha256: str


class CreatedShareOut(BaseModel):
    """The only response that ever carries the key or the passcode."""

    model_config = ConfigDict(extra="forbid")

    share_id: uuid.UUID
    share_path: str
    token: str
    secret: str
    fragment: str
    passcode: str
    passcode_generated: bool
    expires_at: datetime
    view_limit: int
    formats: list[str]
    items: list[str]
    consent_sha256: str
    payload_sha256: str
    document_bytes: dict[str, int]
    consent_lines: list[str]
    warning: str


class ShareSummaryOut(BaseModel):
    """One link as the owner sees it in their list."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    status: str
    token_prefix: str
    formats: list[str]
    items: list[str]
    view_count: int
    view_limit: int
    views_remaining: int
    opened_count: int
    passcode_failures: int
    locked_until: datetime | None = None
    consent_sha256: str
    summary: str


class AuditEventOut(BaseModel):
    """One audit line. Kind, who, when — nothing else was ever stored."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    actor: str
    created_at: datetime


class AuditOut(BaseModel):
    """The history of one link."""

    model_config = ConfigDict(extra="forbid")

    share: ShareSummaryOut
    events: list[AuditEventOut]
    counts: dict[str, int]
    summary: str


class RevokeOut(BaseModel):
    """Confirmation that a link is dead."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    revoked_at: datetime
    status: str


class RevokeAllOut(BaseModel):
    """Confirmation that every live link is dead."""

    model_config = ConfigDict(extra="forbid")

    revoked: int
    ids: list[uuid.UUID]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _opened_count(counts: dict[str, int]) -> int:
    """How many times the document was actually handed out."""

    return int(counts.get("opened", 0))


def _audit_summary(opened: int, revoked: bool, expired: bool, limit: int) -> str:
    """One human sentence about a link's life so far."""

    word = "time" if opened == 1 else "times"
    line = f"Opened {opened} {word} of {limit} allowed"
    if revoked:
        return f"{line}. Revoked — it no longer works."
    if expired:
        return f"{line}. Expired — it no longer works."
    return f"{line}."


def _summary_out(share: ClinicianShare, opened: int) -> ShareSummaryOut:
    """Map a row onto its owner-facing shape."""

    return ShareSummaryOut(
        id=share.id,
        created_at=share.created_at,
        expires_at=share.expires_at,
        revoked_at=share.revoked_at,
        status=share.status,
        token_prefix=share.token_prefix,
        formats=[member.value for member in share.format_list()],
        items=[member.value for member in share.item_list()],
        view_count=share.view_count,
        view_limit=share.view_limit,
        views_remaining=share.views_remaining,
        opened_count=opened,
        passcode_failures=share.passcode_failures,
        locked_until=share.locked_until,
        consent_sha256=share.consent_summary_sha256,
        summary=_audit_summary(opened, share.is_revoked, share.is_expired, share.view_limit),
    )


#: The message attached to the one response that cannot be reproduced.
_SECRET_WARNING = (
    "Save this link now. The key is part of it and is not stored anywhere: "
    "no one — not you, not us — can open this share later from the link alone."
)


def _raise_for(exc: Exception) -> ApiError:
    """Map a service refusal onto the right status and code."""

    if isinstance(exc, SharingDisabledError):
        return ApiError(403, "sharing_disabled", str(exc))
    return ApiError(422, "share_request_invalid", str(exc))


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #


@router.get("/options", response_model=OptionsOut)
async def share_options(
    request: Request,
    user: ShareUser,
    service: ShareServiceDep,
    session: SessionDep,
) -> OptionsOut:
    """The catalogue, the defaults, and the entries that can be selected."""

    enabled = await service.is_enabled(user)
    statement = (
        select(CBTRecord)
        .where(CBTRecord.user_id == user.id)
        .order_by(CBTRecord.created_at.desc())
        .limit(MAX_THOUGHT_RECORDS * 5)
    )
    records = list((await session.execute(statement)).scalars().all())
    return OptionsOut(
        enabled=enabled,
        deployment_enabled=_settings(request).share_enabled,
        defaults=service.defaults(),
        items=[CatalogueItemOut.model_validate(item) for item in service.catalogue()],
        thought_records=[
            ThoughtRecordChoice(
                id=record.id,
                created_at=record.created_at,
                emotion=record.emotion,
                intensity_before=record.intensity_before,
                intensity_after=record.intensity_after,
            )
            for record in records
        ],
        never_included=list(NEVER_INCLUDED),
        disclaimer_title=DISCLAIMER_TITLE,
        disclaimer=DISCLAIMER,
        limits={
            "note_chars": MAX_NOTE_CHARS,
            "questions": MAX_QUESTIONS,
            "question_chars": MAX_QUESTION_CHARS,
            "thought_records": MAX_THOUGHT_RECORDS,
            "windows": list(WINDOW_CHOICES),
        },
    )


@router.post("/preferences", response_model=PreferenceOut)
async def set_share_preference(
    body: PreferenceIn,
    request: Request,
    user: ShareUser,
    service: ShareServiceDep,
    session: SessionDep,
) -> PreferenceOut:
    """Turn sharing on or off. Off by default; turning it on is the person's act."""

    try:
        row = await service.set_enabled(user, enabled=body.enabled)
    except SharingDisabledError as exc:
        raise _raise_for(exc) from exc
    await session.commit()
    logger.info(
        "Share preference set: user_id=%s enabled=%s", str(user.id), str(body.enabled)
    )
    return PreferenceOut(
        enabled=bool(row.enabled),
        deployment_enabled=_settings(request).share_enabled,
        updated_at=row.updated_at,
    )


@router.get("/preferences", response_model=PreferenceOut)
async def get_share_preference(
    request: Request,
    user: ShareUser,
    service: ShareServiceDep,
) -> PreferenceOut:
    """Read the switch (no row yet means off)."""

    row = await service.preference(user)
    return PreferenceOut(
        enabled=bool(row.enabled) if row else False,
        deployment_enabled=_settings(request).share_enabled,
        updated_at=row.updated_at if row else None,
    )


@router.post("/preview", response_model=PreviewOut)
async def preview_share(
    body: ShareRequest,
    user: ShareUser,
    service: ShareServiceDep,
) -> PreviewOut:
    """Build the document without storing it, so the person can read it first."""

    try:
        result = await service.preview(user, body)
    except (ShareRequestError, SharingDisabledError) as exc:
        raise _raise_for(exc) from exc
    return PreviewOut(
        payload=result["payload"].as_dict(),
        consent_lines=result["consent_lines"],
        consent_sha256=result["consent_sha256"],
        payload_sha256=result["payload_sha256"],
    )


@router.post("", response_model=CreatedShareOut, status_code=201)
async def create_share(
    body: CreateIn,
    request: Request,
    user: ShareUser,
    service: ShareServiceDep,
    session: SessionDep,
) -> CreatedShareOut:
    """Create one time-limited, passcode-protected, encrypted share link."""

    raw = body.model_dump()
    selection = ShareRequest.model_validate({k: v for k, v in raw.items() if k not in _CREATE_ONLY})
    options = CreateOptions(
        formats=tuple(body.formats),
        expires_in_days=body.expires_in_days,
        view_limit=body.view_limit,
        passcode=body.passcode,
        consent_sha256=body.consent_sha256,
    )
    try:
        created = await service.create(user, selection, options)
    except ConsentMismatchError as exc:
        raise ApiError(409, "consent_mismatch", str(exc)) from exc
    except ShareLimitError as exc:
        raise ApiError(409, "share_limit", str(exc)) from exc
    except (ShareRequestError, SharingDisabledError) as exc:
        raise _raise_for(exc) from exc
    # The row and its "created" audit line are one transaction: either the link
    # exists and is recorded, or neither does.
    await session.commit()

    logger.info(
        "Share created: user_id=%s share_id=%s items=%d formats=%s ttl_days=%d "
        "view_limit=%d passcode_generated=%s payload_sha256=%s",
        str(user.id),
        str(created.share_id),
        len(created.items),
        [member.value for member in created.formats],
        options.expires_in_days or 0,
        created.view_limit,
        created.passcode_generated,
        created.payload_sha256[:12],
    )
    return _created_out(created)


def _created_out(created: CreatedShare) -> CreatedShareOut:
    """Map a created share onto the wire shape (the secret appears once)."""

    return CreatedShareOut(
        share_id=created.share_id,
        share_path=created.share_path,
        token=created.token,
        secret=created.secret,
        fragment=created.fragment,
        passcode=created.passcode,
        passcode_generated=created.passcode_generated,
        expires_at=created.expires_at,
        view_limit=created.view_limit,
        formats=[member.value for member in created.formats],
        items=[member.value for member in created.items],
        consent_sha256=created.consent_sha256,
        payload_sha256=created.payload_sha256,
        document_bytes=created.document_bytes,
        consent_lines=created.consent_lines,
        warning=_SECRET_WARNING,
    )


@router.get("", response_model=list[ShareSummaryOut])
async def list_shares(
    user: ShareUser,
    service: ShareServiceDep,
) -> list[ShareSummaryOut]:
    """The person's own links, newest first."""

    shares = await service.list_shares(user)
    return [_summary_out(share, share.view_count) for share in shares]


@router.post("/revoke-all", response_model=RevokeAllOut)
async def revoke_all_shares(
    user: ShareUser,
    service: ShareServiceDep,
    session: SessionDep,
) -> RevokeAllOut:
    """One click: every live link stops working."""

    ids = await service.revoke_all(user)
    await session.commit()
    logger.info("Shares revoked (all): user_id=%s count=%d", str(user.id), len(ids))
    return RevokeAllOut(revoked=len(ids), ids=ids)


@router.get("/{share_id}", response_model=ShareSummaryOut)
async def share_detail(
    share_id: uuid.UUID,
    user: ShareUser,
    service: ShareServiceDep,
) -> ShareSummaryOut:
    """One link, with its open count."""

    try:
        share, _, counts = await service.audit(user, share_id)
    except ShareNotFoundError as exc:
        raise ApiError(404, "share_not_found", str(exc)) from exc
    return _summary_out(share, _opened_count(counts))


@router.get("/{share_id}/audit", response_model=AuditOut)
async def share_audit(
    share_id: uuid.UUID,
    user: ShareUser,
    service: ShareServiceDep,
) -> AuditOut:
    """The audit log the owner can read: what happened, and when."""

    try:
        share, events, counts = await service.audit(user, share_id)
    except ShareNotFoundError as exc:
        raise ApiError(404, "share_not_found", str(exc)) from exc
    opened = _opened_count(counts)
    return AuditOut(
        share=_summary_out(share, opened),
        events=[
            AuditEventOut(kind=event.kind.value, actor=event.actor, created_at=event.created_at)
            for event in events
        ],
        counts=dict(counts),
        summary=_audit_summary(opened, share.is_revoked, share.is_expired, share.view_limit),
    )


@router.post("/{share_id}/revoke", response_model=RevokeOut)
async def revoke_share(
    share_id: uuid.UUID,
    user: ShareUser,
    service: ShareServiceDep,
    session: SessionDep,
) -> RevokeOut:
    """Revoke one link. Idempotent."""

    try:
        share = await service.revoke(user, share_id)
    except ShareNotFoundError as exc:
        raise ApiError(404, "share_not_found", str(exc)) from exc
    await session.commit()
    logger.info("Share revoked: user_id=%s share_id=%s", str(user.id), str(share.id))
    return RevokeOut(
        id=share.id, revoked_at=share.revoked_at or share.created_at, status=share.status
    )


__all__ = ["router", "get_share_service"]
