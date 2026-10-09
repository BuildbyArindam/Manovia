"""Authentication: guest, register, login, refresh (with rotation), logout, upgrade.

Design notes (see docs/adr/0004-authentication-and-consent.md):

- Guest mode is first-class: ``POST /auth/guest`` mints an anonymous account and
  a real session, so the product works before an email exists. ``POST
  /auth/upgrade`` later attaches an email/password to the *same* user row, so
  everything the guest wrote is kept.
- Login failures are constant-time and constant-message (``invalid_credentials``)
  whether the account is unknown, the password is wrong, or the account was
  soft-deleted; a dummy argon2 verification keeps the timing equal too.
- Refresh tokens live in the ``refresh_tokens`` table **hashed** (SHA-256) and
  rotate on every ``POST /auth/refresh``. Presenting a rotated/revoked token
  revokes the whole family — the standard stolen-token detection.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, LockoutDep, SessionDep, TokenServiceDep
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.passwords import (
    PasswordError,
    dummy_verify,
    get_password_hasher,
    is_plausible_email,
    normalise_email,
    validate_password,
)
from app.core.tokens import (
    REFRESH_TOKEN_TYPE,
    TokenExpiredError,
    TokenInvalidError,
    TokenService,
    hash_token,
)
from app.db.repos import RefreshTokenRepository, UserRepository
from app.models.base import as_utc, utcnow
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])


# --------------------------------------------------------------------------- #
# Request/response shapes                                                       #
# --------------------------------------------------------------------------- #


class EmailPasswordRequest(BaseModel):
    """Email + password. Policy checks run in the handlers for curated codes."""

    model_config = ConfigDict(extra="ignore")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=4096)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    refresh_token: str = Field(min_length=1, max_length=4096)


class UserOut(BaseModel):
    """The profile shape returned everywhere a user is described."""

    id: uuid.UUID
    email: str | None
    is_anonymous: bool
    language: str
    region: str | None
    created_at: datetime


class SessionOut(BaseModel):
    """One signed-in session: a token pair plus who it belongs to."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# --------------------------------------------------------------------------- #
# Shared helpers                                                                #
# --------------------------------------------------------------------------- #


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        is_anonymous=user.is_anonymous,
        language=user.language,
        region=user.region,
        created_at=user.created_at,
    )


def _check_credentials(email: str, password: str, settings: Settings) -> str:
    """Validate signup credentials; returns the normalised email.

    Raises :class:`ApiError` with a curated code (``invalid_email`` /
    ``password_invalid``) — never a generic validation error, so the UI can say
    what is wrong without echoing input.
    """
    if not is_plausible_email(email):
        raise ApiError(422, "invalid_email", "Enter a valid email address.")
    try:
        validate_password(
            password,
            min_length=settings.password_min_length,
            max_length=settings.password_max_length,
        )
    except PasswordError as exc:
        raise ApiError(422, "password_invalid", str(exc)) from exc
    return normalise_email(email)


async def _issue_session(
    session: AsyncSession,
    tokens: TokenService,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
) -> SessionOut:
    """Mint a new refresh token and an access token for ``user``.

    Pass ``family_id`` to rotate *inside* an existing family (the normal
    refresh path); omit it to start a new family (sign-in).
    """
    family = family_id or uuid.uuid4()
    refresh_token, refresh_claims = tokens.issue_refresh(user.id, family_id=family)
    access_token, _ = tokens.issue_access(user.id)
    await RefreshTokenRepository(session).create(
        token_id=refresh_claims.token_id,
        user_id=user.id,
        family_id=family,
        token_hash=hash_token(refresh_token),
        expires_at=refresh_claims.expires_at,
    )
    return SessionOut(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(tokens.access_ttl.total_seconds()),
        user=_user_out(user),
    )


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


# --------------------------------------------------------------------------- #
# Endpoints                                                                     #
# --------------------------------------------------------------------------- #


@router.post("/guest", status_code=status.HTTP_201_CREATED)
async def create_guest_session(session: SessionDep, tokens: TokenServiceDep) -> SessionOut:
    """Create an anonymous account and sign it in. No email, no password."""
    user = await UserRepository(session).create(is_anonymous=True)
    out = await _issue_session(session, tokens, user)
    await session.commit()
    return out


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    request: Request,
    payload: EmailPasswordRequest,
    session: SessionDep,
    tokens: TokenServiceDep,
) -> SessionOut:
    """Create an email/password account and sign it in."""
    settings = _settings(request)
    email = _check_credentials(payload.email, payload.password, settings)

    existing = await UserRepository(session).get_by_email(email)
    if existing is not None:
        # Deliberately blunt (no "or sign in"): registration cannot avoid
        # revealing that an address is taken. Login does not leak it.
        raise ApiError(409, "email_taken", "An account with this email already exists.")

    user = await UserRepository(session).create(
        email=email,
        password_hash=get_password_hasher().hash(payload.password),
        is_anonymous=False,
    )
    out = await _issue_session(session, tokens, user)
    await session.commit()
    return out


@router.post("/login")
async def login(
    payload: EmailPasswordRequest,
    session: SessionDep,
    tokens: TokenServiceDep,
    lockout: LockoutDep,
) -> SessionOut:
    """Sign in with email + password. Every failure looks exactly the same."""
    key = normalise_email(payload.email)

    remaining = lockout.remaining_lock(key)
    if remaining is not None:
        raise ApiError(
            429,
            "account_locked",
            "Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(int(remaining.total_seconds()) + 1)},
        )

    user = await UserRepository(session).get_by_email(key)
    password_ok = False
    if user is not None and user.password_hash is not None:
        password_ok = get_password_hasher().verify(user.password_hash, payload.password)
    else:
        # Same work for "no such account" as for "wrong password".
        dummy_verify(payload.password)

    if user is not None and user.password_hash is not None and not user.is_deleted and password_ok:
        lockout.record_success(key)
        out = await _issue_session(session, tokens, user)
        await session.commit()
        return out

    failure = lockout.record_failure(key)
    headers = None
    if failure.locked_until is not None:
        remaining = lockout.remaining_lock(key)
        headers = {"Retry-After": str(int(remaining.total_seconds()) + 1 if remaining else 1)}
    raise ApiError(
        401,
        "invalid_credentials",
        "Email or password is incorrect.",
        headers=headers,
    )


@router.post("/refresh")
async def refresh(
    payload: RefreshRequest,
    session: SessionDep,
    tokens: TokenServiceDep,
) -> SessionOut:
    """Rotate a refresh token into a new pair.

    Reuse detection: if the presented token was already rotated or revoked, the
    whole family is revoked (the token leaked, so none of them can be trusted)
    and the caller gets ``401 refresh_token_reused``.
    """
    raw = payload.refresh_token
    try:
        claims = tokens.verify(raw, expected_type=REFRESH_TOKEN_TYPE)
    except TokenExpiredError as exc:
        raise ApiError(
            401, "refresh_token_expired", "Your session has expired. Please sign in again."
        ) from exc
    except TokenInvalidError as exc:
        raise ApiError(401, "refresh_token_invalid", "Sign in to continue.") from exc

    repo = RefreshTokenRepository(session)
    row = await repo.get_by_hash(hash_token(raw))
    if row is None or claims.token_id != row.id or claims.family_id != row.family_id:
        raise ApiError(401, "refresh_token_invalid", "Sign in to continue.")
    if row.revoked_at is not None:
        # Reuse of a rotated/revoked token: burn the whole family.
        await repo.revoke_family(row.family_id, revoked_at=utcnow())
        await session.commit()
        raise ApiError(
            401,
            "refresh_token_reused",
            "This session was already signed out. Please sign in again.",
        )
    if as_utc(row.expires_at) <= utcnow():
        raise ApiError(
            401, "refresh_token_expired", "Your session has expired. Please sign in again."
        )

    user = await UserRepository(session).get(row.user_id)
    if user is None or user.is_deleted:
        raise ApiError(401, "account_deleted", "This account no longer exists.")

    row.revoked_at = utcnow()
    out = await _issue_session(session, tokens, user, family_id=row.family_id)
    await session.commit()
    return out


@router.post("/logout")
async def logout(payload: RefreshRequest, session: SessionDep) -> dict[str, str]:
    """Revoke the session this refresh token belongs to (idempotent).

    Anyone holding the token can end the session; unknown or already-revoked
    tokens get the same answer so the endpoint is not an oracle.
    """
    repo = RefreshTokenRepository(session)
    row = await repo.get_by_hash(hash_token(payload.refresh_token))
    if row is not None:
        await repo.revoke_family(row.family_id, revoked_at=utcnow())
        await session.commit()
    return {"status": "logged_out"}


@router.post("/upgrade")
async def upgrade_account(
    request: Request,
    payload: EmailPasswordRequest,
    user: CurrentUser,
    session: SessionDep,
    tokens: TokenServiceDep,
) -> SessionOut:
    """Turn the anonymous caller into a registered account, keeping all data.

    The user id never changes, so chat sessions, journal entries, mood entries
    and consents stay attached. Every existing refresh token is revoked — an
    identity change signs everything out — and one fresh pair is returned.
    """
    if not user.is_anonymous:
        raise ApiError(409, "already_registered", "This account is already registered.")
    settings = _settings(request)
    email = _check_credentials(payload.email, payload.password, settings)

    repo = UserRepository(session)
    existing = await repo.get_by_email(email)
    if existing is not None and existing.id != user.id:
        raise ApiError(409, "email_taken", "An account with this email already exists.")

    user.email = email
    user.password_hash = get_password_hasher().hash(payload.password)
    user.is_anonymous = False
    await session.flush()
    await RefreshTokenRepository(session).revoke_all_for_user(user.id, revoked_at=utcnow())
    out = await _issue_session(session, tokens, user)
    await session.commit()
    return out


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    """The signed-in profile. The simplest protected endpoint."""
    return _user_out(user)
