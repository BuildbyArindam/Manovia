"""Unit tests for the share cryptography: key derivation, AAD, and the wire format.

The property that matters most here is the one a reviewer cannot see in the API:
**there is one failure.** A wrong key, a wrong passcode, a changed byte, a wrong
nonce and a wrong AAD all raise the same exception, because a distinction
between them would be an oracle — a way to learn something about the plaintext or
the key by watching how a request fails.

The second property is that the derivation is deterministic *and* sensitive: the
same inputs give the same key (so a viewer can re-derive it from the URL
fragment), and changing any one input gives a different key (so a fragment leaked
from one link says nothing about another).
"""

from __future__ import annotations

import hashlib
import secrets

import pytest

from app.models.share import share_aad
from app.services.sharing import crypto
from app.services.sharing.crypto import ShareDecryptError, ShareKeyError

URL_SAFE = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def a_key(
    *, secret: bytes | None = None, passcode: str = "123456", token: str | None = None
) -> bytes:
    """A content key with fresh random inputs unless the test fixes one."""

    return crypto.derive_key(
        secret=secret or crypto.new_secret(),
        passcode=passcode,
        token=token or crypto.new_token(),
    )


def a_document(
    content: bytes, plaintext: bytes = b"the document", aad: bytes = b"aad"
) -> tuple[bytes, bytes]:
    """One encrypted document: ``(ciphertext, nonce)``."""

    return crypto.encrypt_document(content, plaintext, aad=aad)


# --------------------------------------------------------------------------- #
# key derivation
# --------------------------------------------------------------------------- #


def test_derivation_is_deterministic_and_256_bit() -> None:
    """The viewer re-derives the same key from the same fragment and passcode."""

    secret = crypto.new_secret()
    token = crypto.new_token()
    first = crypto.derive_key(secret=secret, passcode="hunter22", token=token)
    second = crypto.derive_key(secret=secret, passcode="hunter22", token=token)
    assert first == second
    assert len(first) == crypto.KEY_BYTES == 32


def test_every_input_changes_the_key() -> None:
    """No input is decorative: the secret, the passcode and the token all matter."""

    secret = crypto.new_secret()
    token = crypto.new_token()
    base = crypto.derive_key(secret=secret, passcode="hunter22", token=token)

    assert base != crypto.derive_key(secret=crypto.new_secret(), passcode="hunter22", token=token)
    assert base != crypto.derive_key(secret=secret, passcode="hunter23", token=token)
    assert base != crypto.derive_key(secret=secret, passcode="hunter22", token=crypto.new_token())
    assert base != crypto.derive_key(secret=secret, passcode="HUNTER22", token=token)
    assert base != crypto.derive_key(secret=secret, passcode=" hunter22", token=token)


def test_the_separator_keeps_secret_and_passcode_from_colliding() -> None:
    """``(secret, "bc")`` is not the same input as a naive ``secret + "bc"``."""

    token = crypto.new_token()
    secret = b"a" * crypto.SECRET_BYTES
    derived = crypto.derive_key(secret=secret, passcode="bc", token=token)
    assert derived != hashlib.sha256(secret + b"bc").digest()
    assert derived == crypto.derive_key(secret=secret, passcode="bc", token=token)


def test_derivation_refuses_bad_inputs() -> None:
    """A short secret or an empty passcode is a programming error, not a key."""

    token = crypto.new_token()
    with pytest.raises(ShareKeyError):
        crypto.derive_key(secret=b"too-short", passcode="123456", token=token)
    with pytest.raises(ShareKeyError):
        crypto.derive_key(secret=crypto.new_secret(), passcode="", token=token)
    with pytest.raises(ShareKeyError):
        crypto.derive_key(secret=crypto.new_secret(), passcode="123456", token="")


# --------------------------------------------------------------------------- #
# encryption
# --------------------------------------------------------------------------- #


def test_round_trip_uses_a_fresh_nonce_every_time() -> None:
    """Same plaintext, same key: different ciphertext, both readable."""

    content = a_key()
    plaintext = b'{"note": "I have been sleeping badly"}'
    first, first_nonce = a_document(content, plaintext)
    second, second_nonce = a_document(content, plaintext)

    assert first_nonce != second_nonce
    assert len(first_nonce) == crypto.NONCE_BYTES == 12
    assert first != second
    assert len(first) == len(plaintext) + 16, "AES-GCM appends a 16-byte tag"
    assert crypto.decrypt_document(content, first, first_nonce, aad=b"aad") == plaintext
    assert crypto.decrypt_document(content, second, second_nonce, aad=b"aad") == plaintext


def test_an_empty_document_and_a_four_megabyte_one_both_survive() -> None:
    """The cipher is not a place for surprises about length."""

    content = a_key()
    empty, nonce = a_document(content, b"")
    assert len(empty) == 16, "an empty document is still a tag, never an empty string"
    assert crypto.decrypt_document(content, empty, nonce, aad=b"aad") == b""

    big = b"x" * 4_000_000
    ciphertext, nonce = a_document(content, big)
    assert crypto.decrypt_document(content, ciphertext, nonce, aad=b"aad") == big


@pytest.mark.parametrize(
    "alter",
    [
        pytest.param(lambda raw, nonce: (raw[1:], nonce), id="dropped-first-byte"),
        pytest.param(lambda raw, nonce: (raw[:-1], nonce), id="truncated"),
        pytest.param(lambda raw, nonce: (raw + b"\x00", nonce), id="extended"),
        pytest.param(lambda raw, nonce: (bytes(len(raw)), nonce), id="zeroed"),
        pytest.param(lambda raw, nonce: (raw, secrets.token_bytes(12)), id="other-nonce"),
        pytest.param(lambda raw, nonce: (raw, nonce + b"\x00"), id="long-nonce"),
    ],
)
def test_any_change_to_the_ciphertext_or_nonce_is_refused(alter: object) -> None:
    """The tag catches all of these, including a nonce of the wrong length."""

    content = a_key()
    raw, nonce = a_document(content, b"appointment summary")
    altered, altered_nonce = alter(raw, nonce)  # type: ignore[operator]
    with pytest.raises((ShareDecryptError, ShareKeyError)):
        crypto.decrypt_document(content, altered, altered_nonce, aad=b"aad")


def test_there_is_one_failure_for_every_way_to_be_wrong() -> None:
    """Wrong key, wrong passcode, wrong token, wrong AAD, changed byte: one error.

    If these were distinguishable, an attacker holding a ciphertext could probe
    it: 'is my key right but the AAD wrong?' is a question that must not have an
    answer. So the message is checked to be identical across all of them.
    """

    secret = crypto.new_secret()
    token = crypto.new_token()
    passcode = "123456"
    right = crypto.derive_key(secret=secret, passcode=passcode, token=token)
    aad = share_aad(token, "payload")
    raw, nonce = a_document(right, b"the document", aad=aad)

    wrong = [
        (
            crypto.derive_key(secret=crypto.new_secret(), passcode=passcode, token=token),
            raw,
            nonce,
            aad,
        ),
        (crypto.derive_key(secret=secret, passcode="123457", token=token), raw, nonce, aad),
        (
            crypto.derive_key(secret=secret, passcode=passcode, token=crypto.new_token()),
            raw,
            nonce,
            aad,
        ),
        (right, raw, nonce, share_aad(token, "pdf")),
        (right, raw, nonce, share_aad(crypto.new_token(), "payload")),
        (right, raw, nonce, b""),
        (right, bytes(len(raw)), nonce, aad),
        (right, flip(raw), nonce, aad),
    ]

    messages: set[str] = set()
    for content, ciphertext, document_nonce, document_aad in wrong:
        with pytest.raises(ShareDecryptError) as excinfo:
            crypto.decrypt_document(content, ciphertext, document_nonce, aad=document_aad)
        messages.add(str(excinfo.value))
    assert len(messages) == 1, messages


def flip(raw: bytes, index: int = 0) -> bytes:
    """One byte of ``raw``, changed."""

    altered = bytearray(raw)
    altered[index] ^= 0x01
    return bytes(altered)


def test_aad_binds_a_document_to_its_share_and_its_name() -> None:
    """The AAD says which token and which document this ciphertext belongs to."""

    token = crypto.new_token()
    assert share_aad(token, "payload") == f"manovia.share.v1.{token}.payload".encode()
    assert share_aad(token, "payload") != share_aad(token, "pdf")
    assert share_aad(token, "payload") != share_aad(crypto.new_token(), "payload")

    content = a_key()
    raw, nonce = a_document(content, b"document", aad=share_aad(token, "payload"))
    assert crypto.decrypt_document(content, raw, nonce, aad=share_aad(token, "payload"))
    with pytest.raises(ShareDecryptError):
        crypto.decrypt_document(content, raw, nonce, aad=share_aad(token, "pdf"))


def test_an_aad_that_is_a_str_is_a_programming_error_not_a_silent_no_op() -> None:
    """The cipher takes bytes; a string would raise rather than skip the binding."""

    content = a_key()
    with pytest.raises(TypeError):
        crypto.encrypt_document(content, b"x", aad="aad")  # type: ignore[arg-type]


def test_a_ciphertext_shorter_than_the_tag_is_refused() -> None:
    """GCM cannot verify a 3-byte ciphertext, and must not pretend to."""

    content = a_key()
    with pytest.raises((ShareDecryptError, ShareKeyError)):
        crypto.decrypt_document(content, b"abc", secrets.token_bytes(12), aad=b"aad")


# --------------------------------------------------------------------------- #
# tokens, secrets and the wire format
# --------------------------------------------------------------------------- #


def test_tokens_and_secrets_are_random_and_url_safe() -> None:
    """128 bits of token, 256 bits of secret: not guessable, not enumerable."""

    tokens = [crypto.new_token() for _ in range(200)]
    keys = [crypto.new_secret() for _ in range(200)]
    assert len(set(tokens)) == 200
    assert len(set(keys)) == 200
    assert all(len(secret) == crypto.SECRET_BYTES for secret in keys)
    assert all(len(token) >= 20 for token in tokens)
    assert all(set(token) <= URL_SAFE for token in tokens)


def test_hash_token_is_a_one_way_sha256() -> None:
    """The database stores the hash, so a dump cannot be replayed as a token."""

    token = crypto.new_token()
    digest = crypto.hash_token(token)
    assert digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert len(digest) == 64
    assert token not in digest
    assert crypto.hash_token(token) == digest
    assert crypto.hash_token(token + "x") != digest


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b"\x00", id="one-byte"),
        pytest.param(secrets.token_bytes(12), id="nonce-length"),
        pytest.param(secrets.token_bytes(32), id="key-length"),
        pytest.param(b"\xff\xfe\x01\x02", id="high-bytes"),
    ],
)
def test_the_base64url_helpers_round_trip(raw: bytes) -> None:
    """Unpadded base64url, both ways, for every length the feature uses."""

    encoded = crypto.encode_bytes(raw)
    assert "=" not in encoded
    assert set(encoded) <= URL_SAFE
    assert crypto.decode_bytes(encoded, expected_length=len(raw), what="value") == raw


@pytest.mark.parametrize(
    ("encoded", "length"),
    [
        pytest.param("", 32, id="empty"),
        pytest.param("   ", 32, id="blank"),
        pytest.param("not base64!!", 32, id="bad-alphabet"),
        pytest.param("====", 32, id="only-padding"),
        pytest.param(crypto.encode_bytes(secrets.token_bytes(31)), 32, id="one-byte-short"),
        pytest.param(crypto.encode_bytes(secrets.token_bytes(33)), 32, id="one-byte-long"),
        pytest.param(crypto.encode_bytes(b""), 0, id="empty-is-refused-even-at-zero"),
    ],
)
def test_a_key_of_the_wrong_shape_is_refused_before_it_is_used(encoded: str, length: int) -> None:
    """A truncated link says 'that is not a key', not 'decryption failed'."""

    with pytest.raises(ShareKeyError):
        crypto.decode_bytes(encoded, expected_length=length, what="key")


def test_decode_document_accepts_any_length_but_still_checks_the_alphabet() -> None:
    """Documents vary in size; the alphabet they travel in does not.

    Zero is not a size a document can have: an AES-GCM ciphertext is at least the
    16-byte tag, so an empty string on the wire is refused rather than decoded
    into an empty document.
    """

    for size in (1, 16, 17, 4096):
        raw = secrets.token_bytes(size)
        assert crypto.decode_document(crypto.encode_bytes(raw)) == raw
    for bad in ("", "   ", "!!!", "a===b", "not base64"):
        with pytest.raises(ShareKeyError):
            crypto.decode_document(bad)


def test_encoding_is_the_standard_url_safe_alphabet() -> None:
    """The browser decodes this with ``atob`` after swapping two characters."""

    raw = bytes(range(256))
    encoded = crypto.encode_bytes(raw)
    standard = base64_urlsafe(raw)
    assert encoded == standard
    assert crypto.decode_document(encoded) == raw


def base64_urlsafe(raw: bytes) -> str:
    """Unpadded base64url, spelled out so the helper is tested against something."""

    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_fingerprint_is_a_stable_sha256_of_bytes() -> None:
    """Used for the payload hash the owner confirms."""

    assert crypto.fingerprint(b"abc") == hashlib.sha256(b"abc").hexdigest()
    assert crypto.fingerprint(b"abc") == crypto.fingerprint(b"abc")
    assert crypto.fingerprint(b"abd") != crypto.fingerprint(b"abc")
    assert len(crypto.fingerprint(b"")) == 64


def test_the_constants_the_browser_depends_on_are_the_documented_ones() -> None:
    """The viewer page's WebCrypto call is written against these exact numbers."""

    assert crypto.ALGORITHM == "A256GCM"
    assert crypto.KEY_BYTES == 32
    assert crypto.NONCE_BYTES == 12
    assert crypto.SECRET_BYTES == 32
    assert crypto.TOKEN_BYTES == 16
    assert crypto.HKDF_INFO == b"manovia.share.v1.key"
    # 32 bytes of key material is 43 unpadded base64url characters — the length
    # the viewer checks before it tries to import a fragment.
    assert len(crypto.encode_bytes(crypto.new_secret())) == 43
