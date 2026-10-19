"""The share cipher: a key that is handed over once and never stored.

The design in one paragraph. When a person creates a share, the API generates a
256-bit **secret** (:func:`new_secret`), derives the content key from that secret
*plus* the link's passcode, encrypts each document with AES-256-GCM under its own
nonce and its own AAD, stores the ciphertext, and returns the secret to the owner
exactly once — base64url-encoded, to live in the URL **fragment**. The fragment
is never sent to a server by a browser, so the secret never appears in an access
log, a referrer header or this database. The recipient's browser derives the same
content key from the fragment and the passcode they type, and decrypts locally.

What that buys, and what it does not:

* **Ciphertext at rest.** A database dump, a backup or a replica contains no
  readable summary and no key material (:mod:`app.models.share`).
* **The passcode is part of the key, not just a gate.** Even a server that handed
  out ciphertext to anyone who asked would not be handing out plaintext: the
  content key needs the passcode too. The server-side passcode check exists so a
  wrong passcode is refused *and counted* (lockout), not because the crypto
  depends on it.
* **Tampering is loud.** GCM authenticates the ciphertext and the AAD, so a
  modified byte, a document swapped between shares, or a wrong key all fail with
  :class:`ShareDecryptError` instead of rendering something plausible.
* **Not forward secrecy, not a Signal-style E2E channel.** The API process
  assembles the plaintext at creation time from data it already holds for this
  person; the key is generated server-side and delivered once. See
  ``docs/adr/0047-clinician-share-bridge.md`` §4 for exactly where the trust
  boundary sits and what would move it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets as pysecrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: Cipher label stored on the row (see :class:`app.models.enums.ShareCipher`).
ALGORITHM = "A256GCM"

#: 256-bit content key, 96-bit nonce (the only size NIST SP 800-38D recommends
#: for random nonces: at 96 bits the birthday bound is not reached in this
#: application's lifetime of a few encryptions per key).
KEY_BYTES = 32
NONCE_BYTES = 12

#: Length of the URL secret in bytes — 256 bits, 43 characters base64url.
SECRET_BYTES = 32

#: Bytes of randomness in a share token (128 bits → 22 base64url characters).
#: Guessing one is ~2^128 work, which is why "link enumeration" is a rate-limit
#: and a uniform-404 problem rather than a keyspace problem.
TOKEN_BYTES = 16

#: HKDF info string: binds every derived key to this application's share format.
HKDF_INFO = b"manovia.share.v1.key"


class ShareKeyError(ValueError):
    """A key or token string that is not one of ours (bad encoding, bad length)."""


class ShareDecryptError(ValueError):
    """Ciphertext that will not authenticate: wrong key, wrong share, or tampered.

    Deliberately one class for all three. Telling a caller *which* of the three
    happened would be an oracle; the browser shows "This link does not work" and
    the audit log stays silent, because the server cannot see this failure at all.
    """


def new_secret() -> bytes:
    """A fresh 256-bit secret for one share. Used once, stored never."""

    return pysecrets.token_bytes(SECRET_BYTES)


def new_token() -> str:
    """A fresh URL-safe public token (128 bits of randomness)."""

    return pysecrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The form the token is stored in: SHA-256 hex, never the token itself."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encode_bytes(value: bytes) -> str:
    """Unpadded base64url — the alphabet that survives a URL fragment."""

    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


#: Unpadded base64url and nothing else. ``base64.urlsafe_b64decode`` silently
#: discards characters outside the alphabet, so without this a key fragment
#: containing a space or a stray character would decode to *something* instead of
#: being refused. A malformed link should say so, not hand back surprise bytes.
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")


def _decode(value: str, what: str) -> bytes:
    """Unpadded base64url to bytes, or an honest :class:`ShareKeyError`."""

    text = value.strip()
    if not text:
        raise ShareKeyError(f"{what} is empty")
    if _BASE64URL.fullmatch(text) is None:
        raise ShareKeyError(f"{what} is not valid base64url")
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise ShareKeyError(f"{what} is not valid base64url") from exc


def decode_bytes(value: str, *, expected_length: int, what: str) -> bytes:
    """Strict inverse of :func:`encode_bytes` for fixed-size values.

    Rejects anything that is not base64url of exactly ``expected_length`` bytes.
    A permissive decoder here would turn a truncated link into a confusing
    decryption failure instead of an immediate, honest "that is not a key".
    """

    raw = _decode(value, what)
    if len(raw) != expected_length:
        raise ShareKeyError(f"{what} must decode to {expected_length} bytes, got {len(raw)}")
    return raw


def decode_document(value: str, *, what: str = "document") -> bytes:
    """Decode a variable-length ciphertext or nonce from the wire.

    A document's length is whatever the document is, so there is nothing to
    check here beyond the alphabet: the tag check inside
    :func:`decrypt_document` is what catches a truncated or altered value.
    """

    return _decode(value, what)


def derive_key(*, secret: bytes, passcode: str, token: str) -> bytes:
    """The 256-bit content key for one share.

    ``HKDF-SHA256(secret || 0x00 || passcode, salt = SHA-256(token), info)``.
    The token is the salt, so two shares created from the same secret and
    passcode — which never happens, but which a bug could cause — still derive
    different keys. The NUL separator keeps ``("ab", "c")`` and ``("a", "bc")``
    from colliding into one input.
    """

    if len(secret) != SECRET_BYTES:
        raise ShareKeyError(f"secret must be {SECRET_BYTES} bytes")
    if not passcode:
        raise ShareKeyError("passcode is required to derive the content key")
    if not token:
        raise ShareKeyError("token is required to derive the content key")
    material = secret + b"\x00" + passcode.encode("utf-8")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=hashlib.sha256(token.encode("utf-8")).digest(),
        info=HKDF_INFO,
    ).derive(material)


def encrypt_document(key: bytes, plaintext: bytes, *, aad: bytes) -> tuple[bytes, bytes]:
    """AES-256-GCM. Returns ``(ciphertext, nonce)``; the nonce is random per call."""

    if len(key) != KEY_BYTES:
        raise ShareKeyError(f"key must be {KEY_BYTES} bytes")
    nonce = pysecrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return ciphertext, nonce


def decrypt_document(key: bytes, ciphertext: bytes, nonce: bytes, *, aad: bytes) -> bytes:
    """The inverse, failing closed with :class:`ShareDecryptError`."""

    if len(key) != KEY_BYTES:
        raise ShareKeyError(f"key must be {KEY_BYTES} bytes")
    if len(nonce) != NONCE_BYTES:
        raise ShareDecryptError("nonce has the wrong length")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except Exception as exc:  # cryptography raises InvalidTag; be broad on purpose
        raise ShareDecryptError("ciphertext did not authenticate") from exc


def fingerprint(value: bytes) -> str:
    """SHA-256 hex of a document — used in previews and consent summaries.

    A hash of what *will* be shared is what the owner confirms; the row stores
    that hash (``consent_summary_sha256``) rather than a copy of the consent text,
    and the client can re-derive it from the preview it was shown.
    """

    return hashlib.sha256(value).hexdigest()


__all__ = [
    "ALGORITHM",
    "HKDF_INFO",
    "KEY_BYTES",
    "NONCE_BYTES",
    "SECRET_BYTES",
    "TOKEN_BYTES",
    "ShareDecryptError",
    "ShareKeyError",
    "decode_bytes",
    "decode_document",
    "decrypt_document",
    "derive_key",
    "encode_bytes",
    "encrypt_document",
    "fingerprint",
    "hash_token",
    "new_secret",
    "new_token",
]
