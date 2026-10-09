"""Consent: what the user must agree to, and what they have agreed to.

``GET /consent/requirements`` is the current, versioned document set (including
the AI disclosure text — AGENTS.md safety rule 3). ``POST /consent`` records
decisions through :class:`~app.db.repos.consents.ConsentRepository`, which is
append-only: withdrawing an agreement is a new row with ``granted=False``.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import ConsentDocumentsDep, CurrentUser, SessionDep
from app.core.errors import ApiError
from app.db.repos import ConsentRepository
from app.models.enums import ConsentKind

router = APIRouter(prefix="/consent", tags=["consent"])


class RequirementOut(BaseModel):
    """One agreement as currently published."""

    kind: ConsentKind
    version: str
    title: str
    summary: str
    # Present on the AI disclosure entry: the exact onboarding text (rule 3).
    text: str | None = None


class RequirementsResponse(BaseModel):
    """Everything the onboarding flow must show before it can ask for a grant."""

    documents: list[RequirementOut]


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: ConsentKind
    version: str = Field(min_length=1, max_length=32)
    granted: bool = True


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    grants: list[GrantIn] = Field(min_length=1, max_length=16)


class GrantOut(BaseModel):
    kind: ConsentKind
    version: str
    granted: bool
    created_at: datetime


class ConsentResponse(BaseModel):
    recorded: list[GrantOut]


@router.get("/requirements")
async def requirements(documents: ConsentDocumentsDep) -> RequirementsResponse:
    """The current document versions and the AI disclosure text. Public."""
    return RequirementsResponse(
        documents=[
            RequirementOut(
                kind=document.kind,
                version=document.version,
                title=document.title,
                summary=document.summary,
                text=document.text,
            )
            for document in documents.all()
        ]
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def record_consent(
    payload: ConsentRequest,
    user: CurrentUser,
    session: SessionDep,
    documents: ConsentDocumentsDep,
) -> ConsentResponse:
    """Record one or more consent decisions for the signed-in user.

    Versions must match the documents currently published — a grant for an old
    version would silently not satisfy :func:`app.api.deps.require_consent`,
    which checks the *current* version, so a mismatch is rejected loudly.
    """
    repo = ConsentRepository(session)
    recorded: list[GrantOut] = []
    for grant in payload.grants:
        if not documents.is_current(grant.kind, grant.version):
            raise ApiError(
                422,
                "consent_version_mismatch",
                "Consent version does not match the current document.",
            )
        consent = await repo.record(
            user_id=user.id,
            kind=grant.kind,
            version=grant.version,
            granted=grant.granted,
        )
        recorded.append(
            GrantOut(
                kind=consent.kind,
                version=consent.version,
                granted=consent.granted,
                created_at=consent.created_at,
            )
        )
    await session.commit()
    return ConsentResponse(recorded=recorded)
