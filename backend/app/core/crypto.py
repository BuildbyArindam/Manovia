"""Field-level encryption for the personal columns in :mod:`app.models`.

The interface is intentionally tiny: a cipher turns a short text value into an
opaque blob and back. Today one deployment-wide key (``FIELD_ENCRYPTION_KEY``,
see :class:`~app.core.config.Settings`) protects every field of every user.
Per-user data keys — wrapped so that destroying one key destroys a person's
data — are a later milestone in the key-management plan; nothing outside this
module may depend on the current single-key layout, and ciphertext has no
backwards-compatibility guarantee.

Usage::

    from app.core.crypto import decrypt, encrypt

    row.note_encrypted = encrypt("a rough week")
    text = decrypt(row.note_encrypted)

Fernet gives authenticated encryption (a tampered blob fails to decrypt rather
than returning garbage) and a fresh IV per call, so encrypting the same string
twice yields different bytes. Keys are passed in by the caller; nothing here
reads them from the environment, and no key is ever logged.
"""

from __future__ import annotations

import base64
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings


class CipherNotConfiguredError(RuntimeError):
    """Raised when a cipher is used before :func:`configure_cipher` ran."""


class DecryptError(ValueError):
    """Raised when a blob was not produced by the configured key."""


class FieldCipher(Protocol):
    """The one interface the data layer needs in order to encrypt a field.

    The bodies are empty because the protocol documents a shape: any cipher
    (today :class:`FernetCipher`, later one that resolves a per-user key) has to
    provide these two methods and nothing else.
    """

    def encrypt(self, plaintext: str) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> str: ...


class FernetCipher:
    """:class:`FieldCipher` backed by a single Fernet key."""

    def __init__(self, key: bytes | str) -> None:
        raw = key.encode() if isinstance(key, str) else key
        try:
            decoded = base64.urlsafe_b64decode(raw)
        except ValueError as exc:
            raise ValueError("FIELD_ENCRYPTION_KEY is not valid base64url") from exc
        if len(decoded) != 32:
            raise ValueError("FIELD_ENCRYPTION_KEY must be 32 bytes, base64url encoded")
        self._fernet = Fernet(raw)

    @classmethod
    def generate_key(cls) -> str:
        """Create a fresh key for local development or tests (never commit one)."""
        return Fernet.generate_key().decode()

    @classmethod
    def from_settings(cls, settings: Settings) -> FernetCipher | None:
        """Build the cipher from configuration, or ``None`` when no key is set."""
        key = settings.field_encryption_key
        return cls(key) if key else None

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as exc:
            raise DecryptError("Ciphertext was not produced by the configured key") from exc


_cipher: FieldCipher | None = None


def configure_cipher(cipher: FieldCipher | None) -> None:
    """Install the process-wide cipher (``None`` clears it, used by tests)."""
    global _cipher
    _cipher = cipher


def get_cipher() -> FieldCipher:
    """Return the configured cipher, or raise :class:`CipherNotConfiguredError`."""
    if _cipher is None:
        raise CipherNotConfiguredError(
            "No field cipher configured: set FIELD_ENCRYPTION_KEY (see .env.example)"
        )
    return _cipher


def encrypt(plaintext: str) -> bytes:
    """Encrypt a value for storage in a ``*_encrypted`` column."""
    return get_cipher().encrypt(plaintext)


def decrypt(ciphertext: bytes) -> str:
    """Decrypt a value read from a ``*_encrypted`` column."""
    return get_cipher().decrypt(ciphertext)


def encrypt_optional(plaintext: str | None) -> bytes | None:
    """:func:`encrypt` for nullable columns."""
    return None if plaintext is None else encrypt(plaintext)


def decrypt_optional(ciphertext: bytes | None) -> str | None:
    """:func:`decrypt` for nullable columns."""
    return None if ciphertext is None else decrypt(ciphertext)
