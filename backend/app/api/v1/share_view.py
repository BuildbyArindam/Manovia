"""Public (unauthenticated) endpoints for a shared link — Day 47.

Two endpoints, and both are designed to be unhelpful:

``GET  /api/v1/public/shares/{token}``
    Says only that a link exists and needs a passcode. Unknown, revoked,
    expired and out-of-budget tokens all answer the same ``404`` with the same
    body, so the endpoint cannot be used to discover whether a token somebody
    guessed is real.

``POST /api/v1/public/shares/{token}/open``
    Takes the passcode and, if everything checks out, returns the **ciphertext**
    of each document plus its nonce. The key is in the URL fragment and never
    arrives here, so what this endpoint hands over is unreadable without it.

Why unauthenticated is safe enough to ship: the documents are encrypted, the
passcode is part of the key derivation (so a leaked ciphertext is not a leaked
document), the link expires, it has a view budget, and both endpoints sit behind
a per-address rate limit that is much tighter than the global one.

Logging follows AGENTS.md safety rule 5: ids, counts and outcomes. Never a
passcode, never a key, never document content. A wrong-passcode line records the
token prefix and nothing about what was typed.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import SessionDep
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.ratelimit import InMemoryRateLimiter
from app.services.sharing.passcode import PASSCODE_MAX_LENGTH
from app.services.sharing.service import (
    PasscodeIncorrectError,
    PasscodeLockedError,
    ShareService,
    ShareUnavailableError,
    documents_as_json,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/shares", tags=["sharing-public"])

#: What a token can look like. A path that fails this is answered with the same
#: 404 as a token that is well-formed but unknown: shape is not existence.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

#: The single refusal message. Deliberately identical for every "no".
_UNAVAILABLE_MESSAGE = "This link is not available."
_UNAVAILABLE_CODE = "share_unavailable"


async def get_public_share_service(request: Request, session: SessionDep) -> ShareService:
    """The share service for a public request (no user, no consent gate)."""

    return ShareService(session, request.app.state.settings)


PublicShareServiceDep = Annotated[ShareService, Depends(get_public_share_service)]


class PublicStatusOut(BaseModel):
    """What the viewer may show before anybody types a passcode."""

    model_config = ConfigDict(extra="forbid")

    available: bool
    requires_passcode: bool
    token_prefix: str
    formats: list[str]
    disclaimer_title: str
    disclaimer: str


class OpenIn(BaseModel):
    """The passcode. Nothing else is accepted from an anonymous viewer."""

    model_config = ConfigDict(extra="forbid")

    passcode: str = Field(min_length=1, max_length=PASSCODE_MAX_LENGTH)


class OpenOut(BaseModel):
    """The ciphertext bundle the viewer decrypts locally."""

    model_config = ConfigDict(extra="forbid")

    share_id: str
    token_prefix: str
    generated_at: str
    expires_at: str
    views_remaining: int
    formats: list[str]
    items: list[str]
    algorithm: str
    key_derivation: str
    disclaimer_title: str
    disclaimer: str
    documents: list[dict[str, Any]]


async def _share_rate_limit(request: Request) -> None:
    """Per-address (and per-address-per-token) budget on the public endpoints.

    Tighter than the global limiter because these paths are unauthenticated by
    design and are exactly the ones somebody would script.
    """

    settings: Settings = request.app.state.settings
    limiter: InMemoryRateLimiter | None = getattr(request.app.state, "rate_limit_share", None)
    if limiter is None:
        return
    client = request.client.host if request.client else "unknown"
    token = str(request.path_params.get("token", ""))
    for key in (f"share:{client}", f"share:{client}:{token}"):
        result = limiter.hit(key)
        if not result.allowed:
            logger.warning(
                "Public share rate limit hit: address=%s token_prefix=%s",
                client[:16],
                token[:8],
            )
            raise ApiError(
                429,
                "rate_limited",
                "Too many attempts. Please wait a minute and try again.",
                headers={"Retry-After": str(max(1, result.retry_after))},
            )


#: Attached to both public routes. Deliberately a route dependency rather than
#: an ``Annotated`` parameter: the endpoints do not need its value, only its effect.
SHARE_RATE_LIMIT = (Depends(_share_rate_limit),)


def _unavailable() -> ApiError:
    """The one refusal every unusable token gets."""

    return ApiError(404, _UNAVAILABLE_CODE, _UNAVAILABLE_MESSAGE)


@router.get("/{token}", response_model=PublicStatusOut, dependencies=SHARE_RATE_LIMIT)
async def share_status(
    token: str,
    service: PublicShareServiceDep,
    session: SessionDep,
) -> PublicStatusOut:
    """Is this link usable, and what will it ask for?

    Reveals existence for a live link and nothing else. Every other answer is
    the same 404, whether the token was never issued, was revoked, has expired
    or has run out of views.
    """

    if not _TOKEN_RE.match(token):
        raise _unavailable()
    try:
        status = await service.public_status(token)
    except ShareUnavailableError:
        # A refusal is still an event the owner should see ("somebody tried an
        # old link"), so the audit line is committed before the 404 goes out.
        await session.commit()
        raise _unavailable() from None
    return PublicStatusOut.model_validate(status)


@router.post("/{token}/open", response_model=OpenOut, dependencies=SHARE_RATE_LIMIT)
async def open_share(
    token: str,
    body: OpenIn,
    service: PublicShareServiceDep,
    session: SessionDep,
) -> OpenOut:
    """Hand over the ciphertext, or refuse.

    Order of refusals matters for the audit log, not for the caller: an unusable
    link is 404, a locked link is 423 with a retry hint, a wrong passcode is 403
    with the number of tries left.
    """

    if not _TOKEN_RE.match(token):
        raise _unavailable()
    try:
        opened = await service.open_public(token, body.passcode)
    except ShareUnavailableError:
        logger.info("Public share open refused (unavailable): token_prefix=%s", token[:8])
        await session.commit()
        raise _unavailable() from None
    except PasscodeIncorrectError as exc:
        await session.commit()
        logger.info(
            "Public share open refused (wrong passcode): token_prefix=%s attempts_remaining=%d",
            token[:8],
            exc.attempts_remaining,
        )
        remaining = exc.attempts_remaining
        word = "try" if remaining == 1 else "tries"
        raise ApiError(
            403,
            "passcode_incorrect",
            # The count is in the message because the error envelope is fixed
            # (code, message, request_id) and this is the one number an honest
            # viewer needs. It is our own counter, never anything they typed.
            f"That passcode is not right. {remaining} {word} left before this link locks.",
        ) from None
    except PasscodeLockedError as exc:
        await session.commit()
        logger.info(
            "Public share open refused (locked): token_prefix=%s retry_after=%d",
            token[:8],
            exc.retry_after_seconds,
        )
        minutes = max(1, exc.retry_after_seconds // 60)
        raise ApiError(
            423,
            "passcode_locked",
            f"Too many wrong passcodes. This link is locked for about {minutes} minutes.",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from None

    # The view was claimed and the "opened" line written: commit them together,
    # so the audit count and the budget can never disagree.
    await session.commit()
    logger.info(
        "Public share opened: share_id=%s views_remaining=%d documents=%d",
        str(opened.share_id),
        opened.views_remaining,
        len(opened.documents),
    )
    return OpenOut.model_validate(documents_as_json(opened))


__all__ = [
    "OpenIn",
    "OpenOut",
    "PublicStatusOut",
    "get_public_share_service",
    "router",
]