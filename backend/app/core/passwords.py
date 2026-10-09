"""Password hashing and the password policy.

Hashing is `argon2-cffi` (Argon2id) behind a tiny :class:`PasswordHasher`
protocol so the algorithm can be swapped without touching the auth flow.

The password policy is deliberately boring (NIST SP 800-63B): length only.
No composition theatre — requiring upper/lower/digit/symbol pushes users to
predictable substitutions and does not stop real attackers. Login failures are
kept constant-time by hashing a dummy when the account does not exist, so
"unknown email" and "wrong password" cost the same.
"""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable

from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

#: Default policy bounds. Length is the only rule (see module docstring).
DEFAULT_MIN_PASSWORD_LENGTH = 10
DEFAULT_MAX_PASSWORD_LENGTH = 256


class PasswordError(ValueError):
    """Base class for password-policy failures."""


class PasswordTooShortError(PasswordError):
    """The password is shorter than the configured minimum."""


class PasswordTooLongError(PasswordError):
    """The password is longer than the configured maximum (hash-input DoS guard)."""


@runtime_checkable
class PasswordHasher(Protocol):
    """Hash/verify interface for password hashing algorithms."""

    def hash(self, password: str) -> str:
        """Return a new self-describing hash for ``password``."""

    def verify(self, password_hash: str, password: str) -> bool:
        """True only on a real match; every failure mode returns ``False``."""


class Argon2Hasher:
    """Argon2id hashing via `argon2-cffi`.

    The library's defaults are used unless overridden; they are tuned for
    interactive logins and require no configuration to be safe.
    """

    def __init__(self) -> None:
        self._hasher = Argon2PasswordHasher()

    def hash(self, password: str) -> str:
        """Return a new Argon2id hash (``argon2id$v=19$...``)."""
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        """True only on a real match; every failure mode returns ``False``.

        A malformed/foreign hash (e.g. a legacy ``scrypt$`` row) never raises —
        callers see one failure for every reason.
        """
        try:
            return bool(self._hasher.verify(password_hash, password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False


class FakePasswordHasher:
    """Deterministic, fast stand-in for tests that do not exercise argon2.

    Not a security primitive: it uses SHA-256 and exists only so unrelated
    tests can run without paying argon2's (intentional) cost. The auth tests
    themselves use the real :class:`Argon2Hasher`.
    """

    def hash(self, password: str) -> str:
        return f"fake${self._digest(password)}"

    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"fake${self._digest(password)}"

    @staticmethod
    def _digest(password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()


def validate_password(
    password: str,
    *,
    min_length: int = DEFAULT_MIN_PASSWORD_LENGTH,
    max_length: int = DEFAULT_MAX_PASSWORD_LENGTH,
) -> None:
    """Enforce the password policy (length only). Raises :class:`PasswordError`."""
    if len(password) < min_length:
        raise PasswordTooShortError(f"Password must be at least {min_length} characters.")
    if len(password) > max_length:
        raise PasswordTooLongError(f"Password must be at most {max_length} characters.")


def normalise_email(email: str) -> str:
    """Canonical form used for storage and lookup: trimmed and lower-cased.

    The address itself is not re-validated here — format checks belong to the
    API layer so this stays usable for lookups of arbitrary input.
    """
    return email.strip().casefold()


def is_plausible_email(email: str) -> bool:
    """Pragmatic address check: one ``@``, no whitespace, dot after ``@``.

    Not full RFC 5322 (an open-ended parser is its own attack surface); this
    rejects obvious garbage and matches what the account-recovery milestone
    needs (local part + domain with a dot).
    """
    if email.count("@") != 1 or len(email) > 320:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and not any(c.isspace() for c in email)


_hasher: Argon2Hasher | None = None
_dummy_hash: str | None = None


def get_password_hasher() -> PasswordHasher:
    """The process-wide argon2 hasher (constructed on first use)."""
    global _hasher
    if _hasher is None:
        _hasher = Argon2Hasher()
    return _hasher


def dummy_verify(password: str) -> bool:
    """Burn the same CPU as a real verification for a non-existent account.

    Always returns ``False``. Used to keep "unknown email" and "wrong password"
    indistinguishable by timing.
    """
    global _dummy_hash
    hasher = get_password_hasher()
    if _dummy_hash is None:
        _dummy_hash = hasher.hash("manovia-timing-equaliser-not-a-real-password")
    hasher.verify(_dummy_hash, password)
    return False
