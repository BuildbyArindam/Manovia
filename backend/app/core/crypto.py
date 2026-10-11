"""Field-level encryption for the personal columns in :mod:`app.models`.

Day 17 upgrades the Day-3 single-key Fernet stub to full AES-256-GCM envelope
encryption (:mod:`app.services.journal.crypto`):

* Each user gets a random 256-bit data key (AES-256-GCM) stored wrapped by the
  master key (``FIELD_ENCRYPTION_KEY``, with ``key_id`` rotation support).
* :func:`encrypt_field` and :func:`decrypt_field` authenticate ``(user_id,
  field_name)`` as Associated Data (AAD) so ciphertexts cannot be swapped across
  users or across columns.
* :class:`FernetCipher` remains compatible with the Day-3 :class:`FieldCipher`
  protocol and can still read legacy Day-3 Fernet tokens during the Alembic
  ``0004`` data migration and key rotation script.
"""

from __future__ import annotations

from typing import Protocol

from app.core.config import Settings
from app.services.journal.crypto import (
    CipherNotConfiguredError,
    DecryptError,
    EnvelopeCipher,
    MasterKeyRing,
    decrypt_field,
    encrypt_field,
    generate_data_key,
    generate_master_key,
    inspect_envelope_key_id,
    is_envelope_ciphertext,
    is_legacy_fernet_ciphertext,
    rotate_database_encryption,
    unwrap_data_key,
    wrap_data_key,
)


class FieldCipher(Protocol):
    """The interface the data layer needs in order to encrypt a field."""

    def encrypt(self, plaintext: str) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> str: ...


class FernetCipher(EnvelopeCipher):
    """Envelope-capable :class:`FieldCipher` with legacy Day-3 Fernet read support."""

    @classmethod
    def from_settings(cls, settings: Settings) -> FernetCipher | None:
        """Build the cipher from configuration, or ``None`` when no key is set."""
        key = settings.field_encryption_key
        if not key:
            return None
        default_kid = getattr(settings, "field_encryption_key_id", "v1") or "v1"
        prev_keys = getattr(settings, "field_encryption_previous_keys", None)
        return cls(key, default_key_id=default_kid, previous_keys=prev_keys)


_cipher: FieldCipher | None = None


def configure_cipher(cipher: FieldCipher | None) -> None:
    """Install the process-wide cipher (``None`` clears it, used by tests)."""
    global _cipher
    _cipher = cipher


def is_cipher_configured() -> bool:
    """Return True when a process-wide cipher is currently installed."""
    return _cipher is not None


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


__all__ = [
    "CipherNotConfiguredError",
    "DecryptError",
    "EnvelopeCipher",
    "FernetCipher",
    "FieldCipher",
    "MasterKeyRing",
    "configure_cipher",
    "decrypt",
    "decrypt_field",
    "decrypt_optional",
    "encrypt",
    "encrypt_field",
    "encrypt_optional",
    "generate_data_key",
    "generate_master_key",
    "get_cipher",
    "inspect_envelope_key_id",
    "is_cipher_configured",
    "is_envelope_ciphertext",
    "is_legacy_fernet_ciphertext",
    "rotate_database_encryption",
    "unwrap_data_key",
    "wrap_data_key",
]
