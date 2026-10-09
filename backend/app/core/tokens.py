"""JWT issue/verify for access and refresh tokens.

Access tokens are short-lived (default 15 minutes) and carry only ``sub`` (the
user id), ``type`` and timing claims. Refresh tokens (default 7 days) add
``jti`` (their own id) and ``fam`` (the rotation family); they are only useful
alongside the matching hashed row in ``refresh_tokens``.

The algorithm is fixed to HS256 and is deliberately **not** configurable: a
configurable ``alg`` header is the classic algorithm-confusion attack surface.
Verification always requires ``exp``, ``iat``, ``sub``, ``jti`` and ``type``.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt as pyjwt

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

_REQUIRED_CLAIMS = ("exp", "iat", "sub", "jti", "type")
_ALGORITHM = "HS256"


def hash_token(token: str) -> str:
    """SHA-256 hex of a token string — the only form ever stored.

    The database keeps this digest, never the bearer token itself, so a dump
    of ``refresh_tokens`` cannot be replayed against the API.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenError(Exception):
    """Base class for token failures."""


class TokenExpiredError(TokenError):
    """The token's ``exp`` is in the past."""


class TokenInvalidError(TokenError):
    """The token is malformed, wrongly signed, or has the wrong type/claims."""


@dataclass(frozen=True)
class TokenClaims:
    """The verified contents of one token."""

    subject: uuid.UUID
    token_id: uuid.UUID
    token_type: str
    family_id: uuid.UUID | None
    expires_at: datetime


class TokenService:
    """Issues and verifies the two token kinds for one application."""

    def __init__(
        self,
        secret: str,
        *,
        access_ttl: timedelta = timedelta(minutes=15),
        refresh_ttl: timedelta = timedelta(days=7),
    ) -> None:
        if not secret:
            raise ValueError("TokenService requires a non-empty signing secret")
        self._secret = secret
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl

    @property
    def access_ttl(self) -> timedelta:
        return self._access_ttl

    @property
    def refresh_ttl(self) -> timedelta:
        return self._refresh_ttl

    def issue_access(
        self, user_id: uuid.UUID, *, now: datetime | None = None
    ) -> tuple[str, TokenClaims]:
        """A signed access token for ``user_id``."""
        return self._issue(
            subject=user_id,
            token_type=ACCESS_TOKEN_TYPE,
            family_id=None,
            ttl=self._access_ttl,
            now=now,
        )

    def issue_refresh(
        self,
        user_id: uuid.UUID,
        *,
        family_id: uuid.UUID,
        token_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> tuple[str, TokenClaims]:
        """A signed refresh token inside rotation family ``family_id``."""
        return self._issue(
            subject=user_id,
            token_type=REFRESH_TOKEN_TYPE,
            family_id=family_id,
            ttl=self._refresh_ttl,
            now=now,
            token_id=token_id,
        )

    def verify(self, token: str, *, expected_type: str) -> TokenClaims:
        """Verify signature, timing claims and token type; return the claims.

        Raises :class:`TokenExpiredError` for an expired-but-well-formed token and
        :class:`TokenInvalidError` for everything else (bad signature, wrong type,
        missing claims, unparseable ids). The two cases are distinguished for
        the client ("sign in again" vs "that was never a token"); neither
        reveals anything about the account.
        """
        try:
            payload = pyjwt.decode(
                token,
                self._secret,
                algorithms=[_ALGORITHM],
                options={"require": list(_REQUIRED_CLAIMS)},
            )
        except pyjwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("token has expired") from exc
        except pyjwt.InvalidTokenError as exc:
            raise TokenInvalidError("token is not valid") from exc

        try:
            token_type = str(payload["type"])
            subject = uuid.UUID(str(payload["sub"]))
            token_id = uuid.UUID(str(payload["jti"]))
            expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
            raw_fam = payload.get("fam")
            family_id = uuid.UUID(str(raw_fam)) if raw_fam is not None else None
        except (KeyError, TypeError, ValueError) as exc:
            raise TokenInvalidError("token claims are not valid") from exc

        if token_type != expected_type:
            raise TokenInvalidError("token type does not match")
        if token_type == REFRESH_TOKEN_TYPE and family_id is None:
            raise TokenInvalidError("refresh token is missing its family")

        return TokenClaims(
            subject=subject,
            token_id=token_id,
            token_type=token_type,
            family_id=family_id,
            expires_at=expires_at,
        )

    def _issue(
        self,
        *,
        subject: uuid.UUID,
        token_type: str,
        family_id: uuid.UUID | None,
        ttl: timedelta,
        now: datetime | None,
        token_id: uuid.UUID | None = None,
    ) -> tuple[str, TokenClaims]:
        issued = now or datetime.now(UTC)
        expires = issued + ttl
        jti = token_id or uuid.uuid4()
        payload: dict[str, str | int] = {
            "sub": str(subject),
            "jti": str(jti),
            "type": token_type,
            "iat": int(issued.timestamp()),
            "exp": int(expires.timestamp()),
        }
        if family_id is not None:
            payload["fam"] = str(family_id)
        token = pyjwt.encode(payload, self._secret, algorithm=_ALGORITHM)
        claims = TokenClaims(
            subject=subject,
            token_id=jti,
            token_type=token_type,
            family_id=family_id,
            expires_at=expires,
        )
        return token, claims
