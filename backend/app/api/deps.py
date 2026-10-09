"""FastAPI dependencies: the data layer, the current user, and consent gates.

``get_session`` hands out a session but does not commit: a request that writes
must call ``await session.commit()`` itself (or use ``session.begin()`` for
explicit transaction scope). Keeping commits out of the dependency makes the
write boundary visible in the endpoint instead of hidden in teardown code.

``get_current_user`` authenticates the bearer access token (JWT, 15 minutes)
and loads the live user; ``require_consent(*kinds)`` builds on it and refuses
endpoints until the current versions of those agreements are granted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.content import ConsentDocuments, HelplineContent, load_consent_documents, load_helplines
from app.core.errors import ApiError
from app.core.lockout import LoginLockout
from app.core.tokens import ACCESS_TOKEN_TYPE, TokenError, TokenExpiredError, TokenService
from app.db.repos import ConsentRepository, UserRepository
from app.db.session import Database
from app.models.enums import ConsentKind
from app.models.user import User


def get_database(request: Request) -> Database:
    """The engine/session factory owned by this application instance."""
    database: Database = request.app.state.db
    return database


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A per-request :class:`AsyncSession`."""
    database: Database = request.app.state.db
    async with database.session_factory() as session:
        yield session


def get_token_service(request: Request) -> TokenService:
    """The JWT service installed by :func:`app.main.create_app`."""
    service: TokenService = request.app.state.token_service
    return service


def get_login_lockout(request: Request) -> LoginLockout:
    """The failed-login tracker installed by :func:`app.main.create_app`."""
    lockout: LoginLockout = request.app.state.login_lockout
    return lockout


def get_consent_documents() -> ConsentDocuments:
    """The shipped, versioned consent documents."""
    return load_consent_documents()


def get_helplines() -> HelplineContent:
    """The shipped, human-verified helpline set."""
    return load_helplines()


SessionDep = Annotated[AsyncSession, Depends(get_session)]
TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]
ConsentDocumentsDep = Annotated[ConsentDocuments, Depends(get_consent_documents)]
HelplinesDep = Annotated[HelplineContent, Depends(get_helplines)]
LockoutDep = Annotated[LoginLockout, Depends(get_login_lockout)]


def _bearer_token(request: Request) -> str:
    """Extract the bearer token, or raise the 401 the caller deserves."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(
            401,
            "token_missing",
            "Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


async def get_current_user(
    request: Request,
    session: SessionDep,
    tokens: TokenServiceDep,
) -> User:
    """Authenticate the request and return the live user.

    Accepts a valid **access** token only. Anonymous (guest) users authenticate
    normally — that is the point of guest mode — but a soft-deleted account can
    never sign in again. Failure codes distinguish the cases for the client
    (``token_missing`` / ``token_expired`` / ``token_invalid`` /
    ``account_deleted``) without leaking whether an account exists.
    """
    raw = _bearer_token(request)
    try:
        claims = tokens.verify(raw, expected_type=ACCESS_TOKEN_TYPE)
    except TokenExpiredError as exc:
        raise ApiError(
            401,
            "token_expired",
            "Your session has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except TokenError as exc:
        raise ApiError(
            401,
            "token_invalid",
            "Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await UserRepository(session).get(claims.subject)
    if user is None or user.is_deleted:
        raise ApiError(
            401,
            "account_deleted",
            "This account no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_consent(*kinds: ConsentKind) -> Callable[..., Awaitable[User]]:
    """Dependency factory: the current user must have granted ``kinds``.

    The gate checks each grant against the **current** document version (see
    ``app/content/consent_documents.json``), so an agreement to an old version
    does not silently satisfy a new one. Chat, journal and mood endpoints are
    built on this (``ai_disclosure`` + ``terms``); the error is
    ``403 consent_required`` and names the missing kinds.
    """
    if not kinds:
        raise ValueError("require_consent needs at least one consent kind")

    async def dependency(
        user: CurrentUser,
        session: SessionDep,
        documents: ConsentDocumentsDep,
    ) -> User:
        repo = ConsentRepository(session)
        missing = [
            kind.value
            for kind in kinds
            if not await repo.is_granted(
                user_id=user.id, kind=kind, version=documents.version_for(kind)
            )
        ]
        if missing:
            raise ApiError(
                403,
                "consent_required",
                f"Consent required: {', '.join(missing)}.",
            )
        return user

    return dependency
