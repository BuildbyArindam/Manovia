"""Envelope encryption for journal entries and personal fields (Day 17).

 Cryptographic Architecture
==========================
1. **Per-user Data Encryption Key (DEK)**:
   Each user gets an independent 256-bit (32-byte) random data key generated
   from the OS CSPRNG (``os.urandom(32)``) and used with **AES-256-GCM**.
2. **Master Key Wrapping (KEK) & Rotation**:
   A user's DEK is never stored in the clear. It is wrapped with the active
   256-bit master key from ``FIELD_ENCRYPTION_KEY`` using AES-256-GCM, a fresh
   96-bit random nonce, and Associated Data (AAD) binding the wrapped DEK to
    both ``user_id`` and the master ``key_id``::

       manovia:dek:v1|user_id=<canonical_uuid>|key_id=<key_id>

   ``FIELD_ENCRYPTION_KEY`` supports key rotation via key IDs:
   - Single bare key: ``<base64url-32-bytes>`` (assigned ``FIELD_ENCRYPTION_KEY_ID``,
     default ``"v1"``).
   - Keyring format: ``"v2:<new-b64url>,v1:<old-b64url>"`` (or via
     ``FIELD_ENCRYPTION_PREVIOUS_KEYS="v1:<old-b64url>"``). The first key in
     the ring is the active key used for all new wraps and writes; retired keys
     remain available for unwrapping until ``scripts/rotate_encryption_keys.py``
     re-wraps and re-encrypts all records under the new key ID.
3. **Field Encryption with Associated Data (AAD)**:
   ``encrypt_field`` and ``decrypt_field`` encrypt UTF-8 plaintext with the
   user's 256-bit DEK using AES-256-GCM, a fresh 96-bit random nonce per write,
   and AAD binding the ciphertext to both the owner and the field name::

       manovia:field:v1|user_id=<canonical_uuid>|field=<field_name>

   Because the GCM authentication tag covers the AAD:
   - Flipping any byte in the header, wrapped DEK, nonce, ciphertext, or tag
     fails authentication and raises :class:`DecryptError`.
   - Swapping ciphertext between User A and User B fails authentication and
     raises :class:`DecryptError`.
   - Swapping ciphertext between two fields of the *same* user (for example,
     copying ``title_encrypted`` into ``body_encrypted``) also fails
     authentication and raises :class:`DecryptError`.

Threat Model — What Is and Is Not Protected
===========================================
**Protected against:**
- **Database compromise at rest** (stolen SQLite file, PostgreSQL disk snapshot,
  unencrypted backup, leaked SQL dump, or accidental log of raw row bytes):
  neither plaintext nor raw DEKs exist in the database.
- **Cross-user and cross-field tampering by a DB-only attacker**: AAD binds every
  wrapped DEK to ``(user_id, key_id)`` and every field ciphertext to
  ``(user_id, field_name)``. An attacker with read/write database access (but
  without the master key) cannot forge ciphertexts or swap a victim's ciphertext
  into their own row to read it through the API.
- **Master key retirement**: rotating ``FIELD_ENCRYPTION_KEY`` with
  ``scripts/rotate_encryption_keys.py`` re-wraps every user DEK and re-encrypts
  every stored field under the new ``key_id`` so a retired master key can be
  purged completely.
- **Per-user crypto-erasure**: destroying a user's row (and wrapped DEK)
  destroys the data key that encrypted their entries.

**NOT protected against (explicit non-goals of server-side envelope encryption):**
- **Live application server compromise with access to ``FIELD_ENCRYPTION_KEY``**:
  The backend holds the master key in process memory in order to unwrap DEKs,
  compute optional in-memory emotion/sentiment scores, and run the crisis safety
  engine before encrypting. An attacker with root/process-memory access on the
  API host (or who steals both the database *and* ``FIELD_ENCRYPTION_KEY``) can
  unwrap DEKs and decrypt entries. Protecting against a compromised server
  requires client-side end-to-end encryption (where the user's device holds the
  key), which would prevent server-side safety screening on write.
- **Metadata side channels**: entry timestamps (``created_at``, ``updated_at``),
  ``user_id``, ciphertext byte length, and (unless the user disables them)
  numeric ``sentiment`` / ``emotion_scores`` remain in the clear by design.
- **Compromised client device or XSS**: entries are decrypted for the
  authenticated owner over HTTPS and searched in browser memory.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import secrets
import struct
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings

DEK_BYTES = 32
MASTER_KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
DEFAULT_KEY_ID = "v1"

DEK_MAGIC = b"MDEK"
ENV_MAGIC = b"MENV"
FORMAT_VERSION = 1

_SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000000"
_DEFAULT_FIELD_NAME = "field"
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class CipherNotConfiguredError(RuntimeError):
    """Raised when encryption or decryption is attempted without a configured key."""


class DecryptError(ValueError):
    """Raised when ciphertext is tampered with, swapped, or encrypted under an unknown key."""


class KeyRotationError(ValueError):
    """Raised when key rotation cannot proceed safely."""


def _canonical_user_id(user_id: uuid.UUID | str) -> str:
    """Normalise a user identifier so UUID and string forms produce identical AAD."""
    if isinstance(user_id, uuid.UUID):
        return str(user_id)
    cleaned = user_id.strip()
    if not cleaned:
        raise ValueError("user_id must not be empty")
    try:
        return str(uuid.UUID(cleaned))
    except ValueError:
        return cleaned


def _canonical_field_name(field_name: str) -> str:
    """Validate and normalise a field name for AAD binding."""
    cleaned = field_name.strip()
    if not cleaned:
        raise ValueError("field_name must not be empty")
    if "|" in cleaned or "\x00" in cleaned:
        raise ValueError("field_name must not contain '|' or NUL bytes")
    return cleaned


def _validate_key_id(key_id: str) -> str:
    cleaned = key_id.strip()
    if not _KEY_ID_RE.match(cleaned):
        raise ValueError(
            f"Invalid encryption key_id {key_id!r}: must be 1..64 chars of [A-Za-z0-9._-]"
        )
    return cleaned


def _decode_raw_key(raw_b64: bytes | str) -> bytes:
    """Decode and validate a 32-byte URL-safe base64 master key."""
    text = (
        raw_b64.decode("ascii", errors="strict").strip()
        if isinstance(raw_b64, bytes)
        else raw_b64.strip()
    )
    if not text:
        raise ValueError("FIELD_ENCRYPTION_KEY must not be empty")
    # Reject non-base64url characters explicitly because urlsafe_b64decode
    # ignores non-alphabet characters unless validate=True (and validate=True
    # rejects '-' and '_' unless translated).
    normalized = text.replace("-", "+").replace("_", "/")
    try:
        decoded = base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("FIELD_ENCRYPTION_KEY is not valid base64url") from exc
    if len(decoded) != MASTER_KEY_BYTES:
        raise ValueError("FIELD_ENCRYPTION_KEY must be 32 bytes, base64url encoded")
    return decoded


def generate_master_key() -> str:
    """Generate a fresh 32-byte URL-safe base64 master key (never log or commit one)."""
    return base64.urlsafe_b64encode(os.urandom(MASTER_KEY_BYTES)).decode("ascii")


def generate_data_key() -> bytes:
    """Generate a fresh 256-bit (32-byte) random Data Encryption Key (DEK)."""
    return os.urandom(DEK_BYTES)


def _dek_aad(user_id: uuid.UUID | str, key_id: str) -> bytes:
    """Associated data binding a wrapped DEK to its owner and wrapping master key ID."""
    uid = _canonical_user_id(user_id)
    kid = _validate_key_id(key_id)
    return f"manovia:dek:v1|user_id={uid}|key_id={kid}".encode()


def _field_aad(user_id: uuid.UUID | str, field_name: str) -> bytes:
    """Associated data binding a field ciphertext to its owner and column/field name."""
    uid = _canonical_user_id(user_id)
    fname = _canonical_field_name(field_name)
    return f"manovia:field:v1|user_id={uid}|field={fname}".encode()


class MasterKeyRing:
    """Holds the active master key (KEK) and optional retired master keys for rotation.

    Supported specification formats in ``FIELD_ENCRYPTION_KEY``:
    - A bare 32-byte base64url key (uses ``default_key_id``, default ``"v1"``).
    - ``"<key_id>:<base64url_key>"`` for a single named key.
    - ``"<active_id>:<active_b64>,<old_id>:<old_b64>,..."`` for key rotation, where
      the first entry is the active wrapping key and subsequent entries are retained
      for unwrapping existing user DEKs.
    """

    def __init__(self, keys: Mapping[str, bytes], *, active_key_id: str) -> None:
        if not keys:
            raise ValueError("MasterKeyRing requires at least one master key")
        validated: dict[str, bytes] = {}
        for kid, raw in keys.items():
            clean_kid = _validate_key_id(kid)
            if len(raw) != MASTER_KEY_BYTES:
                raise ValueError(f"Master key {clean_kid!r} must be {MASTER_KEY_BYTES} bytes")
            validated[clean_kid] = bytes(raw)
        clean_active = _validate_key_id(active_key_id)
        if clean_active not in validated:
            raise ValueError(f"Active key_id {clean_active!r} is not present in the keyring")
        self._keys = validated
        self._active_key_id = clean_active

    @property
    def active_key_id(self) -> str:
        """The key ID used for all new DEK wraps and field writes."""
        return self._active_key_id

    @property
    def key_ids(self) -> tuple[str, ...]:
        """All key IDs currently loaded in the ring (active first)."""
        rest = tuple(k for k in self._keys if k != self._active_key_id)
        return (self._active_key_id, *rest)

    def active_key(self) -> tuple[str, bytes]:
        """Return ``(active_key_id, active_key_bytes)``."""
        return self._active_key_id, self._keys[self._active_key_id]

    def key_for(self, key_id: str) -> bytes:
        """Return the 32-byte master key for ``key_id``, or raise :class:`DecryptError`."""
        raw = self._keys.get(key_id)
        if raw is None:
            raise DecryptError(f"Ciphertext requires master key id {key_id!r}, which is not loaded")
        return raw

    def has_key(self, key_id: str) -> bool:
        return key_id in self._keys

    def legacy_fernet_candidates(self) -> list[Fernet]:
        """Build Fernet instances from all keys in the ring for Day-3 data migration."""
        return [
            Fernet(base64.urlsafe_b64encode(raw))
            for raw in (self._keys[kid] for kid in self.key_ids)
        ]

    @classmethod
    def from_key_material(
        cls,
        key_spec: bytes | str,
        *,
        default_key_id: str = DEFAULT_KEY_ID,
        previous_keys: str | None = None,
    ) -> MasterKeyRing:
        """Parse ``FIELD_ENCRYPTION_KEY`` (and optional ``FIELD_ENCRYPTION_PREVIOUS_KEYS``)."""
        text = (
            key_spec.decode("ascii", errors="strict").strip()
            if isinstance(key_spec, bytes)
            else key_spec.strip()
        )
        if not text:
            raise ValueError("FIELD_ENCRYPTION_KEY must not be empty")

        entries = [part.strip() for part in text.split(",") if part.strip()]
        if not entries:
            raise ValueError("FIELD_ENCRYPTION_KEY must not be empty")

        keys: dict[str, bytes] = {}
        active_kid: str | None = None

        for idx, item in enumerate(entries):
            if ":" in item:
                kid_part, _, raw_part = item.partition(":")
                kid = _validate_key_id(kid_part)
                decoded = _decode_raw_key(raw_part)
            else:
                if len(entries) > 1:
                    raise ValueError(
                        "Multiple master keys in FIELD_ENCRYPTION_KEY must each be prefixed "
                        "with '<key_id>:'"
                    )
                kid = _validate_key_id(default_key_id)
                decoded = _decode_raw_key(item)

            if kid in keys and not secrets.compare_digest(keys[kid], decoded):
                raise ValueError(
                    f"Duplicate encryption key_id {kid!r} with conflicting key material"
                )
            keys[kid] = decoded
            if idx == 0:
                active_kid = kid

        if previous_keys and previous_keys.strip():
            for item in (part.strip() for part in previous_keys.split(",") if part.strip()):
                if ":" not in item:
                    raise ValueError(
                        "Each entry in FIELD_ENCRYPTION_PREVIOUS_KEYS must have the form "
                        "'<key_id>:<base64url_key>'"
                    )
                kid_part, _, raw_part = item.partition(":")
                kid = _validate_key_id(kid_part)
                decoded = _decode_raw_key(raw_part)
                if kid in keys and not secrets.compare_digest(keys[kid], decoded):
                    raise ValueError(
                        f"Duplicate encryption key_id {kid!r} in FIELD_ENCRYPTION_PREVIOUS_KEYS"
                    )
                keys[kid] = decoded

        assert active_kid is not None
        return cls(keys, active_key_id=active_kid)


def wrap_data_key(
    data_key: bytes,
    *,
    user_id: uuid.UUID | str,
    keyring: MasterKeyRing | None = None,
    key_id: str | None = None,
) -> tuple[bytes, str]:
    """Wrap a 32-byte per-user DEK under a master key using AES-256-GCM.

    Returns ``(wrapped_dek_bytes, key_id)``. The wrapped blob is authenticated
    with ``user_id`` and ``key_id`` as AAD, so moving a wrapped DEK to another
    user or altering its ``key_id`` header causes :func:`unwrap_data_key` to
    fail with :class:`DecryptError`.
    """
    if len(data_key) != DEK_BYTES:
        raise ValueError(f"data_key must be {DEK_BYTES} bytes (got {len(data_key)})")
    ring = keyring or get_envelope_cipher().keyring
    target_kid = _validate_key_id(key_id) if key_id is not None else ring.active_key_id
    master_key = ring.key_for(target_kid)

    kid_bytes = target_kid.encode("utf-8")
    nonce = os.urandom(NONCE_BYTES)
    aad = _dek_aad(user_id, target_kid)
    ct_and_tag = AESGCM(master_key).encrypt(nonce, data_key, aad)
    blob = DEK_MAGIC + bytes([FORMAT_VERSION, len(kid_bytes)]) + kid_bytes + nonce + ct_and_tag
    return blob, target_kid


def unwrap_data_key(
    wrapped_dek: bytes,
    *,
    user_id: uuid.UUID | str,
    keyring: MasterKeyRing | None = None,
) -> tuple[bytes, str]:
    """Unwrap a per-user DEK and return ``(data_key_bytes, key_id)``."""
    ring = keyring or get_envelope_cipher().keyring
    if not isinstance(wrapped_dek, (bytes, bytearray, memoryview)):
        raise DecryptError("Wrapped data key must be bytes")
    raw = bytes(wrapped_dek)
    min_len = len(DEK_MAGIC) + 2 + 1 + NONCE_BYTES + DEK_BYTES + TAG_BYTES
    if len(raw) < min_len:
        raise DecryptError("Wrapped data key is truncated or malformed")
    if raw[:4] != DEK_MAGIC:
        raise DecryptError("Wrapped data key has an invalid magic header")
    version = raw[4]
    if version != FORMAT_VERSION:
        raise DecryptError(f"Unsupported wrapped data key version: {version}")
    kid_len = raw[5]
    if kid_len < 1 or kid_len > 64:
        raise DecryptError("Wrapped data key has an invalid key_id length")
    expected_len = 6 + kid_len + NONCE_BYTES + DEK_BYTES + TAG_BYTES
    if len(raw) != expected_len:
        raise DecryptError("Wrapped data key length does not match its header")
    try:
        key_id = raw[6 : 6 + kid_len].decode("utf-8", errors="strict")
        _validate_key_id(key_id)
    except (UnicodeDecodeError, ValueError) as exc:
        raise DecryptError("Wrapped data key has a corrupted key_id") from exc

    nonce_start = 6 + kid_len
    nonce = raw[nonce_start : nonce_start + NONCE_BYTES]
    ct_and_tag = raw[nonce_start + NONCE_BYTES :]
    master_key = ring.key_for(key_id)
    try:
        aad = _dek_aad(user_id, key_id)
        dek = AESGCM(master_key).decrypt(nonce, ct_and_tag, aad)
    except (InvalidTag, ValueError) as exc:
        raise DecryptError(
            "Wrapped data key failed authentication or belongs to another user"
        ) from exc

    if len(dek) != DEK_BYTES:
        raise DecryptError("Unwrapped data key has an invalid length")
    return dek, key_id


def inspect_envelope_key_id(blob: bytes) -> str:
    """Extract the master ``key_id`` header from an ``MENV`` or ``MDEK`` blob."""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise DecryptError("Ciphertext blob must be bytes")
    raw = bytes(blob)
    if len(raw) < 7 or raw[:4] not in (ENV_MAGIC, DEK_MAGIC):
        raise DecryptError("Blob is not a valid MENV/MDEK envelope")
    if raw[4] != FORMAT_VERSION:
        raise DecryptError(f"Unsupported envelope version: {raw[4]}")
    kid_len = raw[5]
    if kid_len < 1 or kid_len > 64 or len(raw) < 6 + kid_len:
        raise DecryptError("Corrupted key_id length in envelope")
    try:
        kid = raw[6 : 6 + kid_len].decode("utf-8", errors="strict")
        return _validate_key_id(kid)
    except (UnicodeDecodeError, ValueError) as exc:
        raise DecryptError("Corrupted key_id in envelope") from exc


def is_envelope_ciphertext(blob: bytes) -> bool:
    """Return True if ``blob`` begins with the ``MENV\\x01`` envelope header."""
    prefix = ENV_MAGIC + bytes([FORMAT_VERSION])
    return isinstance(blob, (bytes, bytearray, memoryview)) and bytes(blob[:5]) == prefix


def is_legacy_fernet_ciphertext(blob: bytes) -> bool:
    """Return True if ``blob`` looks like a Day-3 URL-safe base64 Fernet token."""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return False
    raw = bytes(blob)
    if is_envelope_ciphertext(raw) or len(raw) < 73:
        return False
    return raw.startswith(b"gAAAAA")


class EnvelopeCipher:
    """Envelope cipher managing master keys and per-user AES-256-GCM data keys."""

    def __init__(
        self,
        key_spec: bytes | str | MasterKeyRing,
        *,
        default_key_id: str = DEFAULT_KEY_ID,
        previous_keys: str | None = None,
    ) -> None:
        if isinstance(key_spec, MasterKeyRing):
            self._keyring = key_spec
        else:
            self._keyring = MasterKeyRing.from_key_material(
                key_spec, default_key_id=default_key_id, previous_keys=previous_keys
            )
        # Per-user wrapped DEK cache for callers that do not pass an explicit
        # DB-stored wrapped_dek (such as unit tests or repository static helpers).
        self._user_wrapped_deks: dict[str, bytes] = {}

    @property
    def keyring(self) -> MasterKeyRing:
        return self._keyring

    @property
    def active_key_id(self) -> str:
        return self._keyring.active_key_id

    @classmethod
    def generate_key(cls) -> str:
        """Create a fresh 32-byte base64url master key for dev or tests."""
        return generate_master_key()

    @classmethod
    def from_settings(cls, settings: Settings) -> EnvelopeCipher | None:
        """Build the cipher from :class:`~app.core.config.Settings`, or ``None`` if unset."""
        key = settings.field_encryption_key
        if not key:
            return None
        default_kid = getattr(settings, "field_encryption_key_id", DEFAULT_KEY_ID) or DEFAULT_KEY_ID
        prev_keys = getattr(settings, "field_encryption_previous_keys", None)
        return cls(key, default_key_id=default_kid, previous_keys=prev_keys)

    def register_user_key(self, user_id: uuid.UUID | str, wrapped_dek: bytes) -> None:
        """Cache a user's wrapped DEK (after verifying it unwraps for ``user_id``)."""
        uid = _canonical_user_id(user_id)
        unwrap_data_key(wrapped_dek, user_id=uid, keyring=self._keyring)
        self._user_wrapped_deks[uid] = bytes(wrapped_dek)

    def get_or_create_user_key(
        self,
        user_id: uuid.UUID | str,
        *,
        existing_wrapped_dek: bytes | None = None,
    ) -> tuple[bytes, bytes, str]:
        """Return ``(raw_dek, wrapped_dek, key_id)`` for ``user_id``.

        If ``existing_wrapped_dek`` is supplied (from ``users.data_key_encrypted``)
        or cached in memory, it is unwrapped and reused; if it was wrapped under
        a retired master key, it is transparently re-wrapped under
        ``active_key_id``. Otherwise a new random 256-bit DEK is generated and
        wrapped under ``active_key_id``.
        """
        uid = _canonical_user_id(user_id)
        candidate = existing_wrapped_dek or self._user_wrapped_deks.get(uid)
        if candidate is not None:
            dek, kid = unwrap_data_key(candidate, user_id=uid, keyring=self._keyring)
            if kid != self._keyring.active_key_id:
                wrapped, kid = wrap_data_key(
                    dek,
                    user_id=uid,
                    keyring=self._keyring,
                    key_id=self._keyring.active_key_id,
                )
            else:
                wrapped = bytes(candidate)
            self._user_wrapped_deks[uid] = wrapped
            return dek, wrapped, kid

        dek = generate_data_key()
        wrapped, kid = wrap_data_key(
            dek,
            user_id=uid,
            keyring=self._keyring,
            key_id=self._keyring.active_key_id,
        )
        self._user_wrapped_deks[uid] = wrapped
        return dek, wrapped, kid

    def rotate_wrapped_key(
        self,
        wrapped_dek: bytes,
        *,
        user_id: uuid.UUID | str,
        rekey_dek: bool = False,
    ) -> tuple[bytes, str]:
        """Re-wrap (or replace) a user's DEK under the active master key."""
        uid = _canonical_user_id(user_id)
        dek, _old_kid = unwrap_data_key(wrapped_dek, user_id=uid, keyring=self._keyring)
        target_dek = generate_data_key() if rekey_dek else dek
        new_wrapped, new_kid = wrap_data_key(
            target_dek,
            user_id=uid,
            keyring=self._keyring,
            key_id=self._keyring.active_key_id,
        )
        self._user_wrapped_deks[uid] = new_wrapped
        return new_wrapped, new_kid

    def encrypt_field(
        self,
        plaintext: str,
        user_id: uuid.UUID | str,
        field_name: str = "",
        *,
        field: str | None = None,
        wrapped_dek: bytes | None = None,
        data_key: bytes | None = None,
    ) -> bytes:
        """Encrypt ``plaintext`` with AES-256-GCM and AAD ``(user_id, field_name)``."""
        if not isinstance(plaintext, str):
            raise TypeError("plaintext must be a str")
        resolved_field = field if (field is not None and not field_name) else field_name
        uid = _canonical_user_id(user_id)
        fname = _canonical_field_name(resolved_field)

        if data_key is not None:
            if len(data_key) != DEK_BYTES:
                raise ValueError(f"data_key must be {DEK_BYTES} bytes")
            dek = bytes(data_key)
            if wrapped_dek is not None:
                unwrapped, kid = unwrap_data_key(wrapped_dek, user_id=uid, keyring=self._keyring)
                if not secrets.compare_digest(unwrapped, dek):
                    raise DecryptError("Provided wrapped_dek does not match provided data_key")
                w_dek = bytes(wrapped_dek)
            else:
                w_dek, kid = wrap_data_key(
                    dek, user_id=uid, keyring=self._keyring, key_id=self._keyring.active_key_id
                )
            self._user_wrapped_deks[uid] = w_dek
        else:
            dek, w_dek, kid = self.get_or_create_user_key(uid, existing_wrapped_dek=wrapped_dek)

        kid_bytes = kid.encode("utf-8")
        if len(w_dek) > 65535:
            raise ValueError("wrapped_dek exceeds maximum envelope header size")
        nonce = os.urandom(NONCE_BYTES)
        aad = _field_aad(uid, fname)
        field_ct = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), aad)

        header = (
            ENV_MAGIC
            + bytes([FORMAT_VERSION, len(kid_bytes)])
            + kid_bytes
            + struct.pack(">H", len(w_dek))
            + w_dek
            + nonce
        )
        return header + field_ct

    def decrypt_field(
        self,
        ciphertext: bytes,
        user_id: uuid.UUID | str,
        field_name: str = "",
        *,
        field: str | None = None,
        wrapped_dek: bytes | None = None,
    ) -> str:
        """Decrypt an ``MENV\\x01`` ciphertext, verifying ``(user_id, field_name)`` AAD."""
        resolved_field = field if (field is not None and not field_name) else field_name
        uid = _canonical_user_id(user_id)
        fname = _canonical_field_name(resolved_field)

        if not isinstance(ciphertext, (bytes, bytearray, memoryview)):
            raise DecryptError("Ciphertext must be bytes")
        raw = bytes(ciphertext)
        # Minimum size: 4 (magic) + 1 (ver) + 1 (kid_len) + 1 (min kid) + 2 (wdek_len)
        # + 67 (min MDEK) + 12 (nonce) + 0 (empty pt) + 16 (GCM tag) = 104 bytes
        if len(raw) < 104:
            raise DecryptError("Ciphertext envelope is truncated or malformed")
        if raw[:4] != ENV_MAGIC:
            raise DecryptError("Ciphertext does not have a valid MENV header")
        version = raw[4]
        if version != FORMAT_VERSION:
            raise DecryptError(f"Unsupported ciphertext envelope version: {version}")

        kid_len = raw[5]
        if kid_len < 1 or kid_len > 64:
            raise DecryptError("Ciphertext envelope has an invalid key_id length")
        pos = 6 + kid_len
        if len(raw) < pos + 2:
            raise DecryptError("Ciphertext envelope is truncated before wrapped_dek")
        try:
            envelope_kid = raw[6:pos].decode("utf-8", errors="strict")
            _validate_key_id(envelope_kid)
        except (UnicodeDecodeError, ValueError) as exc:
            raise DecryptError("Ciphertext envelope has a corrupted key_id") from exc

        (wdek_len,) = struct.unpack(">H", raw[pos : pos + 2])
        pos += 2
        if wdek_len < 67 or len(raw) < pos + wdek_len + NONCE_BYTES + TAG_BYTES:
            raise DecryptError("Ciphertext envelope has an invalid wrapped_dek length")
        envelope_wdek = raw[pos : pos + wdek_len]
        pos += wdek_len

        nonce = raw[pos : pos + NONCE_BYTES]
        pos += NONCE_BYTES
        field_ct_and_tag = raw[pos:]
        if len(field_ct_and_tag) < TAG_BYTES:
            raise DecryptError("Ciphertext envelope is missing its authentication tag")

        dek, unwrapped_kid = unwrap_data_key(envelope_wdek, user_id=uid, keyring=self._keyring)
        if unwrapped_kid != envelope_kid:
            raise DecryptError("Ciphertext envelope key_id does not match wrapped DEK key_id")

        if wrapped_dek is not None:
            expected_dek, _ = unwrap_data_key(wrapped_dek, user_id=uid, keyring=self._keyring)
            if not secrets.compare_digest(dek, expected_dek):
                raise DecryptError("Ciphertext DEK does not match the user's stored data key")

        aad = _field_aad(uid, fname)
        try:
            plaintext_bytes = AESGCM(dek).decrypt(nonce, field_ct_and_tag, aad)
            return plaintext_bytes.decode("utf-8", errors="strict")
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            raise DecryptError(
                "Ciphertext authentication failed (tampered data, wrong key, or swapped AAD)"
            ) from exc

    def decrypt_legacy_fernet(self, ciphertext: bytes) -> str:
        """Decrypt a Day-3 Fernet token using any master key in the keyring.

        Used by the Alembic ``0004`` data migration and the key rotation script
        to upgrade legacy Fernet blobs into AES-256-GCM envelope blobs.
        """
        if not isinstance(ciphertext, (bytes, bytearray, memoryview)):
            raise DecryptError("Ciphertext must be bytes")
        raw = bytes(ciphertext)
        for fernet in self._keyring.legacy_fernet_candidates():
            try:
                return fernet.decrypt(raw).decode("utf-8", errors="strict")
            except (InvalidToken, UnicodeDecodeError):
                continue
        raise DecryptError("Legacy Fernet ciphertext could not be decrypted by any configured key")

    # FieldCipher compatibility methods for non-user-scoped callers (Day 3 interface)
    def encrypt(self, plaintext: str) -> bytes:
        return self.encrypt_field(
            plaintext, user_id=_SYSTEM_USER_ID, field_name=_DEFAULT_FIELD_NAME
        )

    def decrypt(self, ciphertext: bytes) -> str:
        if is_legacy_fernet_ciphertext(ciphertext):
            return self.decrypt_legacy_fernet(ciphertext)
        return self.decrypt_field(
            ciphertext, user_id=_SYSTEM_USER_ID, field_name=_DEFAULT_FIELD_NAME
        )


def get_envelope_cipher() -> EnvelopeCipher:
    """Return the active :class:`EnvelopeCipher` from :mod:`app.core.crypto`."""
    from app.core.crypto import get_cipher

    cipher = get_cipher()
    if isinstance(cipher, EnvelopeCipher):
        return cipher
    # If a caller installed a custom FieldCipher with a keyring attribute, use it.
    keyring = getattr(cipher, "keyring", None)
    if isinstance(keyring, MasterKeyRing):
        return EnvelopeCipher(keyring)
    raise CipherNotConfiguredError(
        "Configured cipher does not support envelope encryption: set FIELD_ENCRYPTION_KEY"
    )


def encrypt_field(
    plaintext: str,
    user_id: uuid.UUID | str,
    field_name: str = "",
    *,
    field: str | None = None,
    wrapped_dek: bytes | None = None,
    data_key: bytes | None = None,
    cipher: EnvelopeCipher | None = None,
) -> bytes:
    """Encrypt a personal field with per-user DEK and ``(user_id, field_name)`` AAD."""
    active = cipher or get_envelope_cipher()
    return active.encrypt_field(
        plaintext,
        user_id,
        field_name,
        field=field,
        wrapped_dek=wrapped_dek,
        data_key=data_key,
    )


def decrypt_field(
    ciphertext: bytes,
    user_id: uuid.UUID | str,
    field_name: str = "",
    *,
    field: str | None = None,
    wrapped_dek: bytes | None = None,
    cipher: EnvelopeCipher | None = None,
) -> str:
    """Decrypt a personal field, verifying ``(user_id, field_name)`` AAD."""
    active = cipher or get_envelope_cipher()
    return active.decrypt_field(
        ciphertext,
        user_id,
        field_name,
        field=field,
        wrapped_dek=wrapped_dek,
    )


@dataclass(frozen=True)
class RotationReport:
    """Summary of a master-key rotation run (metadata only, never plaintext or keys)."""

    active_key_id: str
    users_rotated: int
    journal_entries_rotated: int
    mood_entries_rotated: int
    chat_sessions_rotated: int
    messages_rotated: int


async def rotate_database_encryption(
    session: AsyncSession,
    cipher: EnvelopeCipher | None = None,
    *,
    rekey_user_deks: bool = True,
) -> RotationReport:
    """Re-wrap every user DEK and re-encrypt all ciphertext columns under ``active_key_id``.

    1. For every :class:`~app.models.user.User`, unwraps ``data_key_encrypted``
       (or mints one if absent), optionally generates a fresh 256-bit DEK
       (``rekey_user_deks=True``), and wraps it under ``cipher.active_key_id``.
    2. For every :class:`~app.models.journal.JournalEntry`, decrypts
       ``title_encrypted`` and ``body_encrypted`` (supporting both envelope and
       legacy Day-3 Fernet blobs) and re-encrypts them with the user's new
       wrapped DEK, fresh GCM nonces, and ``(user_id, "title")`` /
       ``(user_id, "body")`` AAD, updating ``key_id = cipher.active_key_id``.
    3. Re-encrypts ``mood_entries.note_encrypted``,
       ``chat_sessions.memory_encrypted``, and ``messages.content_encrypted``
       under ``cipher.active_key_id`` so the old master key ID is no longer
       required anywhere in the database.
    """
    from sqlalchemy import select

    from app.models import ChatSession, JournalEntry, Message, MoodEntry, User

    active = cipher or get_envelope_cipher()
    target_kid = active.active_key_id

    users = (await session.execute(select(User))).scalars().all()
    old_wrapped_by_user: dict[uuid.UUID, bytes | None] = {}
    new_wrapped_by_user: dict[uuid.UUID, bytes] = {}
    new_dek_by_user: dict[uuid.UUID, bytes] = {}
    users_rotated = 0

    for user in users:
        old_wrapped_by_user[user.id] = user.data_key_encrypted
        if user.data_key_encrypted is not None:
            old_dek, _old_kid = unwrap_data_key(
                user.data_key_encrypted, user_id=user.id, keyring=active.keyring
            )
            next_dek = generate_data_key() if rekey_user_deks else old_dek
        else:
            next_dek = generate_data_key()

        new_wrapped, _ = wrap_data_key(
            next_dek, user_id=user.id, keyring=active.keyring, key_id=target_kid
        )
        user.data_key_encrypted = new_wrapped
        user.data_key_id = target_kid
        active.register_user_key(user.id, new_wrapped)
        new_wrapped_by_user[user.id] = new_wrapped
        new_dek_by_user[user.id] = next_dek
        users_rotated += 1

    journals = (await session.execute(select(JournalEntry))).scalars().all()
    journals_rotated = 0
    for entry in journals:
        old_wdek = old_wrapped_by_user.get(entry.user_id)
        if is_envelope_ciphertext(entry.title_encrypted):
            title_plain = active.decrypt_field(
                entry.title_encrypted,
                user_id=entry.user_id,
                field_name="title",
                wrapped_dek=old_wdek,
            )
        else:
            title_plain = active.decrypt_legacy_fernet(entry.title_encrypted)

        if is_envelope_ciphertext(entry.body_encrypted):
            body_plain = active.decrypt_field(
                entry.body_encrypted,
                user_id=entry.user_id,
                field_name="body",
                wrapped_dek=old_wdek,
            )
        else:
            body_plain = active.decrypt_legacy_fernet(entry.body_encrypted)

        user_new_wdek = new_wrapped_by_user.get(entry.user_id)
        user_new_dek = new_dek_by_user.get(entry.user_id)
        if user_new_wdek is None or user_new_dek is None:
            user_new_dek, user_new_wdek, _ = active.get_or_create_user_key(entry.user_id)
            new_wrapped_by_user[entry.user_id] = user_new_wdek
            new_dek_by_user[entry.user_id] = user_new_dek

        entry.title_encrypted = active.encrypt_field(
            title_plain,
            user_id=entry.user_id,
            field_name="title",
            wrapped_dek=user_new_wdek,
            data_key=user_new_dek,
        )
        entry.body_encrypted = active.encrypt_field(
            body_plain,
            user_id=entry.user_id,
            field_name="body",
            wrapped_dek=user_new_wdek,
            data_key=user_new_dek,
        )
        entry.key_id = target_kid
        journals_rotated += 1

    moods = (
        (await session.execute(select(MoodEntry).where(MoodEntry.note_encrypted.is_not(None))))
        .scalars()
        .all()
    )
    moods_rotated = 0
    for mood in moods:
        if mood.note_encrypted is None:
            continue
        note_plain = active.decrypt(mood.note_encrypted)
        mood.note_encrypted = active.encrypt(note_plain)
        moods_rotated += 1

    chat_sessions = (
        (
            await session.execute(
                select(ChatSession).where(ChatSession.memory_encrypted.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    chats_rotated = 0
    for chat in chat_sessions:
        if chat.memory_encrypted is None:
            continue
        mem_plain = active.decrypt(chat.memory_encrypted)
        chat.memory_encrypted = active.encrypt(mem_plain)
        chats_rotated += 1

    messages = (await session.execute(select(Message))).scalars().all()
    messages_rotated = 0
    for msg in messages:
        msg_plain = active.decrypt(msg.content_encrypted)
        msg.content_encrypted = active.encrypt(msg_plain)
        messages_rotated += 1

    await session.flush()
    return RotationReport(
        active_key_id=target_kid,
        users_rotated=users_rotated,
        journal_entries_rotated=journals_rotated,
        mood_entries_rotated=moods_rotated,
        chat_sessions_rotated=chats_rotated,
        messages_rotated=messages_rotated,
    )
