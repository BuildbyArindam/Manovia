"""Share passcodes: generated or typed, stored only as a slow salted hash.

The passcode is the second half of the link. The first half — the 256-bit secret
in the URL fragment — is what makes the ciphertext unreadable; the passcode is
what stops a forwarded link from being opened by whoever receives it, and it also
feeds the key derivation (:func:`app.services.sharing.crypto.derive_key`), so it
is cryptographic material and not merely a gate.

Defaults are conservative on purpose:

* **PBKDF2-HMAC-SHA256, 210,000 iterations** — the same count Private Mode uses
  for its passphrase keys (``frontend/src/features/private/crypto``), so one
  number is documented, reviewed and benchmarked instead of two.
* **16 random bytes of salt per share**, stored next to the hash: a leaked table
  cannot be attacked with one precomputed dictionary.
* **Constant-time comparison**, because the alternative leaks how many leading
  bytes an attacker got right.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as pysecrets

#: A generated passcode is six digits: easy to read aloud over the phone, which
#: is how most people will give it to their clinician. Its strength is acceptable
#: because the URL secret (256 bits) is what protects the ciphertext — see
#: ``docs/clinical-sharing.md`` §"Threat model".
GENERATED_DIGITS = 6

#: What the API accepts when a person types their own.
PASSCODE_MIN_LENGTH = 6
PASSCODE_MAX_LENGTH = 64

SALT_BYTES = 16
KEY_BYTES = 32
ITERATIONS = 210_000


class PasscodeError(ValueError):
    """A passcode the API will not accept (too short, too long, only spaces)."""


def generate_passcode() -> str:
    """A six-digit numeric passcode from the CSPRNG."""

    return "".join(str(pysecrets.randbelow(10)) for _ in range(GENERATED_DIGITS))


def validate(passcode: str) -> str:
    """Normalise and check a typed passcode, or raise :class:`PasscodeError`."""

    candidate = passcode.strip()
    if len(candidate) < PASSCODE_MIN_LENGTH:
        raise PasscodeError(f"Use at least {PASSCODE_MIN_LENGTH} characters.")
    if len(candidate) > PASSCODE_MAX_LENGTH:
        raise PasscodeError(f"Use at most {PASSCODE_MAX_LENGTH} characters.")
    return candidate


def new_salt() -> bytes:
    """A fresh per-share salt."""

    return pysecrets.token_bytes(SALT_BYTES)


def hash_passcode(passcode: str, salt: bytes, *, iterations: int = ITERATIONS) -> bytes:
    """PBKDF2-HMAC-SHA256 over the passcode and its salt."""

    if iterations < 1:
        raise ValueError("iterations must be positive")
    return hashlib.pbkdf2_hmac(
        "sha256", passcode.encode("utf-8"), salt, iterations, dklen=KEY_BYTES
    )


def verify_passcode(
    passcode: str, salt: bytes, expected: bytes, *, iterations: int = ITERATIONS
) -> bool:
    """Constant-time verification of a presented passcode."""

    return hmac.compare_digest(hash_passcode(passcode, salt, iterations=iterations), expected)


__all__ = [
    "GENERATED_DIGITS",
    "ITERATIONS",
    "KEY_BYTES",
    "PASSCODE_MAX_LENGTH",
    "PASSCODE_MIN_LENGTH",
    "SALT_BYTES",
    "PasscodeError",
    "generate_passcode",
    "hash_passcode",
    "new_salt",
    "validate",
    "verify_passcode",
]
