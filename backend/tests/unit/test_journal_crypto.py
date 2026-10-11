"""Unit tests for Day 17 envelope encryption (:mod:`app.services.journal.crypto`).

Covers:
- Round-trip encryption/decryption (ASCII, Unicode en/hi/bn, empty strings)
- Per-user 256-bit DEK wrapping and unwrapping with master key IDs
- Byte-by-byte tamper detection across the entire envelope and wrapped DEK
- Cross-user ciphertext swap rejection via Associated Data (AAD)
- Same-user cross-field ciphertext swap rejection via Associated Data (AAD)
- MasterKeyRing rotation and retirement of old master key IDs
"""

from __future__ import annotations

import base64
import uuid

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.crypto import configure_cipher
from app.services.journal.crypto import (
    DEK_BYTES,
    ENV_MAGIC,
    CipherNotConfiguredError,
    DecryptError,
    EnvelopeCipher,
    MasterKeyRing,
    decrypt_field,
    encrypt_field,
    generate_data_key,
    generate_master_key,
    get_envelope_cipher,
    inspect_envelope_key_id,
    is_envelope_ciphertext,
    is_legacy_fernet_ciphertext,
    unwrap_data_key,
    wrap_data_key,
)


def test_round_trip_encrypt_and_decrypt_field() -> None:
    cipher = EnvelopeCipher(generate_master_key(), default_key_id="v1")
    configure_cipher(cipher)
    user_id = uuid.uuid4()

    samples = [
        ("title", "Quiet Sunday evening"),
        ("body", "Walked by the river for twenty minutes without my phone."),
        ("body", "आज मन थोड़ा शांत है और नींद बेहतर आई।"),
        ("body", "আজকে মনটা একটু হালকা লাগছে, ডায়েরিতে লিখে রাখলাম।"),
        ("title", ""),
    ]
    for field_name, text in samples:
        ct = encrypt_field(text, user_id=user_id, field_name=field_name)
        assert is_envelope_ciphertext(ct)
        assert ct.startswith(ENV_MAGIC)
        assert inspect_envelope_key_id(ct) == "v1"
        if text:
            assert text.encode("utf-8") not in ct
        assert decrypt_field(ct, user_id=user_id, field_name=field_name) == text


def test_same_plaintext_and_aad_produce_distinct_ciphertexts() -> None:
    cipher = EnvelopeCipher(generate_master_key())
    user_id = uuid.uuid4()
    c1 = cipher.encrypt_field("same words", user_id=user_id, field_name="body")
    c2 = cipher.encrypt_field("same words", user_id=user_id, field_name="body")
    assert c1 != c2, "each encryption must use a fresh 96-bit GCM nonce"
    assert cipher.decrypt_field(c1, user_id=user_id, field_name="body") == "same words"
    assert cipher.decrypt_field(c2, user_id=user_id, field_name="body") == "same words"


def test_each_user_gets_a_distinct_random_data_key() -> None:
    cipher = EnvelopeCipher(generate_master_key(), default_key_id="k1")
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    dek_a, wrapped_a, kid_a = cipher.get_or_create_user_key(user_a)
    dek_b, wrapped_b, kid_b = cipher.get_or_create_user_key(user_b)

    assert len(dek_a) == DEK_BYTES == 32
    assert len(dek_b) == DEK_BYTES == 32
    assert dek_a != dek_b
    assert wrapped_a != wrapped_b
    assert kid_a == kid_b == "k1"
    # Raw DEK bytes must not appear inside the wrapped DEK blob.
    assert dek_a not in wrapped_a
    assert dek_b not in wrapped_b

    # Repeated calls for the same user reuse their DEK.
    dek_a_again, wrapped_a_again, _ = cipher.get_or_create_user_key(user_a)
    assert dek_a_again == dek_a
    assert wrapped_a_again == wrapped_a


def test_tamper_detection_flipping_any_byte_in_ciphertext_fails() -> None:
    """Flipping any single byte anywhere in the envelope must fail authentication."""
    cipher = EnvelopeCipher(generate_master_key(), default_key_id="v1")
    user_id = uuid.uuid4()
    ciphertext = cipher.encrypt_field(
        "My private journal reflection that must detect tampering.",
        user_id=user_id,
        field_name="body",
    )

    for index in range(len(ciphertext)):
        tampered = bytearray(ciphertext)
        tampered[index] ^= 0x01
        with pytest.raises(DecryptError):
            cipher.decrypt_field(bytes(tampered), user_id=user_id, field_name="body")


def test_tamper_detection_flipping_any_byte_in_wrapped_dek_fails() -> None:
    """Flipping any single byte in a wrapped DEK blob must fail unwrap."""
    cipher = EnvelopeCipher(generate_master_key(), default_key_id="v1")
    user_id = uuid.uuid4()
    dek = generate_data_key()
    wrapped, _ = wrap_data_key(dek, user_id=user_id, keyring=cipher.keyring)

    for index in range(len(wrapped)):
        tampered = bytearray(wrapped)
        tampered[index] ^= 0x01
        with pytest.raises(DecryptError):
            unwrap_data_key(bytes(tampered), user_id=user_id, keyring=cipher.keyring)


def test_swapped_ciphertext_between_users_fails() -> None:
    """Ciphertext from User A cannot be decrypted as belonging to User B."""
    cipher = EnvelopeCipher(generate_master_key(), default_key_id="v1")
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    ct_a = cipher.encrypt_field("User A's private journal entry", user_id=user_a, field_name="body")
    # Normal read for user A succeeds.
    assert (
        cipher.decrypt_field(ct_a, user_id=user_a, field_name="body")
        == "User A's private journal entry"
    )

    # Reading User A's ciphertext under User B's user_id fails AAD verification.
    with pytest.raises(DecryptError):
        cipher.decrypt_field(ct_a, user_id=user_b, field_name="body")

    # Even if User B's wrapped DEK is passed explicitly, decrypting User A's
    # ciphertext fails.
    _, wrapped_b, _ = cipher.get_or_create_user_key(user_b)
    with pytest.raises(DecryptError):
        cipher.decrypt_field(ct_a, user_id=user_b, field_name="body", wrapped_dek=wrapped_b)


def test_swapped_wrapped_dek_between_users_fails() -> None:
    """A wrapped DEK for User A cannot be unwrapped for User B."""
    cipher = EnvelopeCipher(generate_master_key(), default_key_id="v1")
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    dek_a = generate_data_key()
    wrapped_a, _ = wrap_data_key(dek_a, user_id=user_a, keyring=cipher.keyring)

    with pytest.raises(DecryptError):
        unwrap_data_key(wrapped_a, user_id=user_b, keyring=cipher.keyring)


def test_swapped_ciphertext_between_fields_of_same_user_fails() -> None:
    """Swapping title_encrypted and body_encrypted on the same user row fails AAD."""
    cipher = EnvelopeCipher(generate_master_key(), default_key_id="v1")
    user_id = uuid.uuid4()

    title_ct = cipher.encrypt_field("Wednesday morning", user_id=user_id, field_name="title")
    body_ct = cipher.encrypt_field(
        "Wrote three pages before work.", user_id=user_id, field_name="body"
    )

    with pytest.raises(DecryptError):
        cipher.decrypt_field(title_ct, user_id=user_id, field_name="body")
    with pytest.raises(DecryptError):
        cipher.decrypt_field(body_ct, user_id=user_id, field_name="title")


def test_master_key_rotation_in_memory_and_retiring_old_key() -> None:
    """Rotating from v1 to v2 re-wraps DEKs so v1 is no longer required."""
    key_v1 = generate_master_key()
    key_v2 = generate_master_key()
    user_id = uuid.uuid4()

    cipher_v1 = EnvelopeCipher(f"v1:{key_v1}")
    _, wrapped_v1, kid_v1 = cipher_v1.get_or_create_user_key(user_id)
    assert kid_v1 == "v1"
    ct_v1 = cipher_v1.encrypt_field(
        "Written under master key v1",
        user_id=user_id,
        field_name="body",
        wrapped_dek=wrapped_v1,
    )
    assert inspect_envelope_key_id(ct_v1) == "v1"

    # During rotation, both v2 (active) and v1 (retired) are in the keyring.
    cipher_rotating = EnvelopeCipher(f"v2:{key_v2},v1:{key_v1}")
    assert cipher_rotating.active_key_id == "v2"
    assert cipher_rotating.keyring.key_ids == ("v2", "v1")
    assert (
        cipher_rotating.decrypt_field(ct_v1, user_id=user_id, field_name="body")
        == "Written under master key v1"
    )

    # Rotate the wrapped DEK and re-encrypt the field under v2.
    wrapped_v2, kid_v2 = cipher_rotating.rotate_wrapped_key(wrapped_v1, user_id=user_id)
    assert kid_v2 == "v2"
    ct_v2 = cipher_rotating.encrypt_field(
        "Written under master key v1",
        user_id=user_id,
        field_name="body",
        wrapped_dek=wrapped_v2,
    )
    assert inspect_envelope_key_id(ct_v2) == "v2"

    # After rotation, remove v1 completely; only v2 is configured.
    cipher_v2_only = EnvelopeCipher(f"v2:{key_v2}")
    assert (
        cipher_v2_only.decrypt_field(
            ct_v2, user_id=user_id, field_name="body", wrapped_dek=wrapped_v2
        )
        == "Written under master key v1"
    )

    # And v1 alone cannot decrypt the rotated ciphertext.
    with pytest.raises(DecryptError):
        cipher_v1.decrypt_field(ct_v2, user_id=user_id, field_name="body")


def test_legacy_fernet_detection_and_upgrade() -> None:
    raw_key = generate_master_key()
    fernet = Fernet(raw_key.encode("ascii"))
    legacy_blob = fernet.encrypt(b"Day 3 Fernet entry")

    assert is_legacy_fernet_ciphertext(legacy_blob)
    assert not is_envelope_ciphertext(legacy_blob)

    cipher = EnvelopeCipher(raw_key)
    assert cipher.decrypt_legacy_fernet(legacy_blob) == "Day 3 Fernet entry"
    assert cipher.decrypt(legacy_blob) == "Day 3 Fernet entry"

    other_cipher = EnvelopeCipher(generate_master_key())
    with pytest.raises(DecryptError):
        other_cipher.decrypt_legacy_fernet(legacy_blob)


def test_keyring_validation_and_error_edges() -> None:
    k1 = generate_master_key()
    k2 = generate_master_key()

    with pytest.raises(ValueError, match="empty"):
        MasterKeyRing.from_key_material("   ")
    with pytest.raises(ValueError, match="prefixed"):
        MasterKeyRing.from_key_material(f"{k1},{k2}")
    with pytest.raises(ValueError, match="Duplicate"):
        MasterKeyRing.from_key_material(f"v1:{k1},v1:{k2}")
    with pytest.raises(ValueError, match="Invalid encryption key_id"):
        MasterKeyRing.from_key_material(f"bad id!:{k1}")
    with pytest.raises(ValueError, match="FIELD_ENCRYPTION_PREVIOUS_KEYS"):
        MasterKeyRing.from_key_material(f"v2:{k2}", previous_keys=k1)
    with pytest.raises(ValueError, match="Duplicate"):
        MasterKeyRing.from_key_material(f"v1:{k1}", previous_keys=f"v1:{k2}")
    with pytest.raises(ValueError, match="at least one"):
        MasterKeyRing({}, active_key_id="v1")
    with pytest.raises(ValueError, match="32 bytes"):
        MasterKeyRing({"v1": b"short"}, active_key_id="v1")
    with pytest.raises(ValueError, match="not present"):
        MasterKeyRing({"v1": base64.urlsafe_b64decode(k1)}, active_key_id="v2")

    ring = MasterKeyRing.from_key_material(f"v2:{k2}", previous_keys=f"v1:{k1}")
    assert ring.has_key("v1") and ring.has_key("v2") and not ring.has_key("v9")


def test_validation_guards_on_inputs_and_unconfigured_cipher() -> None:
    cipher = EnvelopeCipher(generate_master_key())
    uid = uuid.uuid4()

    with pytest.raises(ValueError, match="user_id"):
        cipher.encrypt_field("hi", user_id="   ", field_name="body")
    with pytest.raises(ValueError, match="field_name"):
        cipher.encrypt_field("hi", user_id=uid, field_name="   ")
    with pytest.raises(ValueError, match="field_name"):
        cipher.encrypt_field("hi", user_id=uid, field_name="bad|name")
    with pytest.raises(ValueError, match="data_key"):
        wrap_data_key(b"short", user_id=uid, keyring=cipher.keyring)
    with pytest.raises(DecryptError, match="truncated"):
        cipher.decrypt_field(b"MENV\x01short", user_id=uid, field_name="body")
    with pytest.raises(DecryptError, match="MENV"):
        inspect_envelope_key_id(b"NOPE0000")

    configure_cipher(None)
    with pytest.raises(CipherNotConfiguredError):
        get_envelope_cipher()


def test_envelope_cipher_from_settings_supports_previous_keys() -> None:
    k1 = generate_master_key()
    k2 = generate_master_key()
    settings = Settings(
        _env_file=None,
        field_encryption_key=k2,
        field_encryption_key_id="v2",
        field_encryption_previous_keys=f"v1:{k1}",
    )
    cipher = EnvelopeCipher.from_settings(settings)
    assert cipher is not None
    assert cipher.active_key_id == "v2"
    assert cipher.keyring.key_ids == ("v2", "v1")
