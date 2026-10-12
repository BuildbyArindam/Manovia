"""Every rejection branch in the envelope crypto, exercised for real.

Day 26 asks for **100% branch coverage on the crypto paths**, and this file is
the honest attempt to earn it rather than to declare it. The existing
``test_journal_crypto.py`` proves the promises a reader cares about — end-to-end
round trips, tamper detection, cross-user AAD binding, master-key rotation. What
it does not do is execute the *rejection* branches: a truncated ``MENV`` header,
a ``key_id`` whose bytes are not UTF-8, a ciphertext presented with somebody
else's stored DEK, a rotation that meets a legacy Day-3 Fernet row, or a row
whose owner has no DEK yet.

Those branches are exactly the ones that must never be wrong, because each one
is the difference between "we refuse this input" and "we hand back plaintext we
cannot vouch for". A branch no test has ever entered is not evidence of
anything; it is code nobody has watched run. So each test below names the
branch it pins and the damage the wrong answer would do.

Two of the branches in :mod:`app.services.journal.crypto` are structurally
unreachable and are marked ``# pragma: no cover`` at the source instead of being
faked here — see the comments there and the notes in
``docs/verification/day26-test-hardening-performance.md``.
"""

from __future__ import annotations

import base64
import uuid

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from app.core.crypto import FieldCipher, configure_cipher
from app.services.journal.crypto import (
    DEK_BYTES,
    DEK_MAGIC,
    ENV_MAGIC,
    FORMAT_VERSION,
    NONCE_BYTES,
    TAG_BYTES,
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


def _cipher() -> EnvelopeCipher:
    return EnvelopeCipher(generate_master_key(), default_key_id="v1")


def _wrapped_dek_len(envelope: bytes) -> int:
    """Length of the wrapped DEK a valid envelope carries (magic+ver+kid_len+kid)."""
    return int.from_bytes(envelope[6 + envelope[5] : 6 + envelope[5] + 2], "big")


# --------------------------------------------------------------------------- #
# Identifier canonicalisation                                                 #
# --------------------------------------------------------------------------- #


def test_non_uuid_user_id_is_used_verbatim_after_validation() -> None:
    """A user id that is not a UUID is accepted, cleaned, and still binds the AAD.

    ``_canonical_user_id`` exists to make the UUID and string forms of the same
    identifier produce identical associated data. The fallback branch is not
    dead code: the Alembic 0004 data migration and the Day-3 compatibility
    ``FernetCipher`` path both encrypt for non-UUID subjects ("system" callers),
    so refusing them would break the migration path that upgrades old rows.
    """
    cipher = _cipher()
    weird_id = "  legacy-user-7  "
    ciphertext = cipher.encrypt_field("migration-era note", user_id=weird_id, field_name="body")

    # The padded form decrypts (it is canonicalised on the way in and out)...
    assert cipher.decrypt_field(ciphertext, user_id=weird_id, field_name="body") == (
        "migration-era note"
    )
    # ...and the unpadded form is the *same* subject, not a different one.
    assert cipher.decrypt_field(ciphertext, user_id="legacy-user-7", field_name="body") == (
        "migration-era note"
    )
    # A different non-UUID subject is rejected: the AAD really does bind it.
    with pytest.raises(DecryptError):
        cipher.decrypt_field(ciphertext, user_id="legacy-user-8", field_name="body")


def test_empty_user_id_and_field_name_are_refused() -> None:
    cipher = _cipher()
    with pytest.raises(ValueError, match="user_id"):
        cipher.encrypt_field("hi", user_id="   ", field_name="body")
    with pytest.raises(ValueError, match="field_name"):
        cipher.encrypt_field("hi", user_id=uuid.uuid4(), field_name="\x00")


def test_alias_field_keyword_is_honoured_when_field_name_is_empty() -> None:
    """``field=`` is the Day-3 spelling; both spellings must bind the same AAD."""
    cipher = _cipher()
    user_id = uuid.uuid4()
    ciphertext = cipher.encrypt_field("aliased", user_id=user_id, field="title")
    assert cipher.decrypt_field(ciphertext, user_id=user_id, field="title") == "aliased"
    # The AAD is (user, "title") no matter which keyword named it.
    with pytest.raises(DecryptError):
        cipher.decrypt_field(ciphertext, user_id=user_id, field_name="body")


# --------------------------------------------------------------------------- #
# Master key material                                                         #
# --------------------------------------------------------------------------- #


def test_master_key_material_format_errors_name_the_variable() -> None:
    """Every malformed ``FIELD_ENCRYPTION_KEY`` shape is refused at startup.

    A key that silently decodes to fewer than 32 bytes would produce an AES key
    the process cannot use, and the failure would surface as unreadable journal
    entries rather than as a boot error. So all four malformed shapes are
    checked here, message included.
    """
    good = generate_master_key()

    with pytest.raises(ValueError, match="must not be empty"):
        MasterKeyRing.from_key_material("")
    with pytest.raises(ValueError, match="must not be empty"):
        MasterKeyRing.from_key_material(", ,")
    with pytest.raises(ValueError, match="not valid base64url"):
        MasterKeyRing.from_key_material("not base64 at all!!")
    # A named key with nothing after the colon is the empty-key case.
    with pytest.raises(ValueError, match="must not be empty"):
        MasterKeyRing.from_key_material("v1:")
    with pytest.raises(ValueError, match="32 bytes"):
        MasterKeyRing.from_key_material(base64.urlsafe_b64encode(b"too-short").decode())
    # Bytes are accepted (settings may hand over the raw environment value).
    ring = MasterKeyRing.from_key_material(good.encode("ascii"))
    assert ring.active_key_id == "v1"
    # A previous key with the same id and the *same* material is not a conflict.
    ring_same = MasterKeyRing.from_key_material(f"v2:{good}", previous_keys=f"v1:{good}")
    assert ring_same.key_ids == ("v2", "v1")


def test_active_key_returns_id_and_material_together() -> None:
    """``active_key()`` is what rotation reads; it must agree with the properties."""
    raw = generate_master_key()
    ring = MasterKeyRing.from_key_material(f"v7:{raw}")
    key_id, material = ring.active_key()
    assert key_id == ring.active_key_id == "v7"
    assert material == base64.urlsafe_b64decode(raw)
    assert len(material) == 32

    with pytest.raises(DecryptError, match="not loaded"):
        ring.key_for("v0")


# --------------------------------------------------------------------------- #
# Wrapped data keys                                                           #
# --------------------------------------------------------------------------- #


def test_unwrap_rejects_every_malformed_wrapped_dek() -> None:
    """The five ways a wrapped DEK can be wrong, each with its own refusal.

    A wrapped DEK is a header (magic, version, key-id length, key id) followed
    by nonce + GCM ciphertext. Each field is attacker-controlled when a blob
    arrives from the database, so each one gets a length check and its own
    error rather than an exception from deep inside ``cryptography``.
    """
    cipher = _cipher()
    user_id = uuid.uuid4()
    wrapped, _ = wrap_data_key(generate_data_key(), user_id=user_id, keyring=cipher.keyring)

    with pytest.raises(DecryptError, match="must be bytes"):
        unwrap_data_key("not bytes", user_id=user_id, keyring=cipher.keyring)  # type: ignore[arg-type]
    with pytest.raises(DecryptError, match="truncated or malformed"):
        unwrap_data_key(wrapped[:10], user_id=user_id, keyring=cipher.keyring)
    with pytest.raises(DecryptError, match="invalid magic header"):
        unwrap_data_key(b"XXXX" + wrapped[4:], user_id=user_id, keyring=cipher.keyring)
    with pytest.raises(DecryptError, match="Unsupported wrapped data key version"):
        unwrap_data_key(
            wrapped[:4] + b"\x09" + wrapped[5:], user_id=user_id, keyring=cipher.keyring
        )

    # key_id length 0 and 65 are both outside the 1..64 window.
    zero_len = bytearray(wrapped)
    zero_len[5] = 0
    with pytest.raises(DecryptError, match="invalid key_id length"):
        unwrap_data_key(bytes(zero_len), user_id=user_id, keyring=cipher.keyring)
    too_long = bytearray(wrapped)
    too_long[5] = 65
    with pytest.raises(DecryptError, match="invalid key_id length"):
        unwrap_data_key(bytes(too_long), user_id=user_id, keyring=cipher.keyring)

    # A key_id whose declared length no longer matches the blob.
    with pytest.raises(DecryptError, match="does not match its header"):
        unwrap_data_key(wrapped + b"\x00", user_id=user_id, keyring=cipher.keyring)

    # Bytes that are not valid UTF-8 in the key_id field.
    bad_utf8 = bytearray(wrapped)
    bad_utf8[6] = 0xFF
    with pytest.raises(DecryptError, match="corrupted key_id"):
        unwrap_data_key(bytes(bad_utf8), user_id=user_id, keyring=cipher.keyring)

    # Valid UTF-8, but not a legal key id (the key_id regex is the last gate).
    bad_id = bytearray(wrapped)
    bad_id[6] = ord("!")
    with pytest.raises(DecryptError, match="corrupted key_id"):
        unwrap_data_key(bytes(bad_id), user_id=user_id, keyring=cipher.keyring)


def test_unwrap_refuses_a_wrapped_dek_wrapped_for_another_user() -> None:
    """AAD binding is the reason a stolen row cannot decrypt somebody else's DEK."""
    cipher = _cipher()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    wrapped_alice, _ = wrap_data_key(generate_data_key(), user_id=alice, keyring=cipher.keyring)

    with pytest.raises(DecryptError, match="failed authentication"):
        unwrap_data_key(wrapped_alice, user_id=bob, keyring=cipher.keyring)


def test_register_user_key_verifies_ownership_before_caching() -> None:
    """A wrapped DEK that is not this user's is never cached under their id."""
    cipher = _cipher()
    alice, bob = uuid.uuid4(), uuid.uuid4()
    wrapped_alice, _ = wrap_data_key(generate_data_key(), user_id=alice, keyring=cipher.keyring)

    cipher.register_user_key(alice, wrapped_alice)
    assert cipher.get_or_create_user_key(alice)[1] == wrapped_alice

    with pytest.raises(DecryptError):
        cipher.register_user_key(bob, wrapped_alice)


# --------------------------------------------------------------------------- #
# Envelope headers                                                            #
# --------------------------------------------------------------------------- #


def test_inspect_envelope_key_id_rejects_every_corrupt_header() -> None:
    """The header reader is used by migrations, so it must not guess."""
    cipher = _cipher()
    user_id = uuid.uuid4()
    envelope = cipher.encrypt_field("x", user_id=user_id, field_name="body")

    with pytest.raises(DecryptError, match="must be bytes"):
        inspect_envelope_key_id("MENV\x01...")  # type: ignore[arg-type]
    with pytest.raises(DecryptError, match="valid MENV/MDEK envelope"):
        inspect_envelope_key_id(b"MENV\x01")
    with pytest.raises(DecryptError, match="valid MENV/MDEK envelope"):
        inspect_envelope_key_id(b"XXXX\x01v1")
    with pytest.raises(DecryptError, match="Unsupported envelope version"):
        inspect_envelope_key_id(ENV_MAGIC + b"\x07" + envelope[5:])
    with pytest.raises(DecryptError, match="Corrupted key_id length"):
        inspect_envelope_key_id(ENV_MAGIC + bytes([FORMAT_VERSION, 0]) + envelope[6:])
    with pytest.raises(DecryptError, match="Corrupted key_id length"):
        inspect_envelope_key_id(ENV_MAGIC + bytes([FORMAT_VERSION, 64]) + b"v")

    bad_utf8 = bytearray(envelope)
    bad_utf8[6] = 0xFF
    with pytest.raises(DecryptError, match="Corrupted key_id in envelope"):
        inspect_envelope_key_id(bytes(bad_utf8))

    # Both magic headers are readable, and the DEK blob is a valid input too.
    wrapped, _ = wrap_data_key(generate_data_key(), user_id=user_id, keyring=cipher.keyring)
    assert wrapped[:4] == DEK_MAGIC
    assert inspect_envelope_key_id(wrapped) == "v1"


def test_envelope_and_legacy_detection_is_input_type_safe() -> None:
    """Both sniffers answer ``False`` for non-bytes instead of raising.

    They run on every read path — including rows written before Day 17 — so a
    ``None`` or a ``str`` reaching them must be a "not this format", not a
    ``TypeError`` that turns into a 500.
    """
    assert is_envelope_ciphertext("MENV\x01v1") is False
    assert is_envelope_ciphertext(None) is False  # type: ignore[arg-type]
    assert is_legacy_fernet_ciphertext("gAAAAA" + "x" * 80) is False
    assert is_legacy_fernet_ciphertext(None) is False  # type: ignore[arg-type]
    # Short base64 that merely looks like Fernet is not accepted as legacy.
    assert is_legacy_fernet_ciphertext(b"gAAAAAshort") is False
    # An envelope is never mistaken for legacy, even if it starts with gAAAAA.
    assert is_legacy_fernet_ciphertext(ENV_MAGIC + b"\x01" + b"gAAAAA" + b"x" * 90) is False


def test_legacy_fernet_decryption_rejects_bad_input_types() -> None:
    cipher = _cipher()
    with pytest.raises(DecryptError, match="must be bytes"):
        cipher.decrypt_legacy_fernet("gAAAAA")  # type: ignore[arg-type]
    with pytest.raises(DecryptError, match="could not be decrypted"):
        cipher.decrypt_legacy_fernet(Fernet(generate_master_key().encode()).encrypt(b"other key"))


# --------------------------------------------------------------------------- #
# Field ciphertexts                                                           #
# --------------------------------------------------------------------------- #


def test_decrypt_field_rejects_every_corrupt_envelope() -> None:
    """Seven header shapes, each refused with a message a log can carry.

    None of these may reach AESGCM: an unauthenticated parse is how a crafted
    blob turns into a confusing exception (or, worse, a partial read) instead of
    a clean :class:`DecryptError`.
    """
    cipher = _cipher()
    user_id = uuid.uuid4()
    envelope = cipher.encrypt_field("private", user_id=user_id, field_name="body")

    with pytest.raises(DecryptError, match="must be bytes"):
        cipher.decrypt_field("not bytes", user_id=user_id, field_name="body")  # type: ignore[arg-type]
    with pytest.raises(DecryptError, match="truncated or malformed"):
        cipher.decrypt_field(envelope[:50], user_id=user_id, field_name="body")
    with pytest.raises(DecryptError, match="valid MENV header"):
        cipher.decrypt_field(b"XXXX" + envelope[4:], user_id=user_id, field_name="body")
    with pytest.raises(DecryptError, match="Unsupported ciphertext envelope version"):
        cipher.decrypt_field(
            envelope[:4] + b"\x05" + envelope[5:], user_id=user_id, field_name="body"
        )

    invalid_kid_len = bytearray(envelope)
    invalid_kid_len[5] = 0
    with pytest.raises(DecryptError, match="invalid key_id length"):
        cipher.decrypt_field(bytes(invalid_kid_len), user_id=user_id, field_name="body")

    kid_len = envelope[5]

    bad_utf8 = bytearray(envelope)
    bad_utf8[6] = 0xFF
    with pytest.raises(DecryptError, match="corrupted key_id"):
        cipher.decrypt_field(bytes(bad_utf8), user_id=user_id, field_name="body")

    # A wrapped_dek length that is too small, and one that runs off the blob.
    pos = 6 + kid_len
    short_wdek = bytearray(envelope)
    short_wdek[pos : pos + 2] = (66).to_bytes(2, "big")
    with pytest.raises(DecryptError, match="invalid wrapped_dek length"):
        cipher.decrypt_field(bytes(short_wdek), user_id=user_id, field_name="body")

    too_short_blob = bytearray(envelope)
    too_short_blob[pos : pos + 2] = (500).to_bytes(2, "big")
    with pytest.raises(DecryptError, match="invalid wrapped_dek length"):
        cipher.decrypt_field(bytes(too_short_blob), user_id=user_id, field_name="body")

    # Every truncation of a valid envelope is refused (the message depends on
    # how far the cut goes; what matters is that no cut returns plaintext).
    for cut in range(1, len(envelope)):
        with pytest.raises(DecryptError):
            cipher.decrypt_field(envelope[:cut], user_id=user_id, field_name="body")
    assert cipher.decrypt_field(envelope, user_id=user_id, field_name="body") == "private"


def test_decrypt_field_requires_the_envelope_to_match_the_stored_dek() -> None:
    """A ciphertext encrypted under a *different* DEK for the same user is refused.

    This is the branch that catches a partially-applied rotation: the row was
    re-encrypted with a new per-user DEK while ``users.data_key_encrypted`` still
    holds the old one. Without the check, the row would decrypt with wrong data
    or fail with an opaque GCM error much later.
    """
    cipher = _cipher()
    user_id = uuid.uuid4()
    old_wrapped = cipher.get_or_create_user_key(user_id)[1]

    # Same user, same cipher, but a *fresh* DEK (as if the user row was rotated
    # and this specific row was not).
    cipher.register_user_key(
        user_id, wrap_data_key(generate_data_key(), user_id=user_id, keyring=cipher.keyring)[0]
    )
    envelope = cipher.encrypt_field("stale row", user_id=user_id, field_name="body")

    with pytest.raises(DecryptError, match="does not match the user's stored data key"):
        cipher.decrypt_field(envelope, user_id=user_id, field_name="body", wrapped_dek=old_wrapped)


def test_get_or_create_user_key_rewraps_a_retired_master_key_dek() -> None:
    """A DEK wrapped under a retired key is re-wrapped under the active one on read.

    This is what makes rotation non-breaking: an old row can still be decrypted
    in the window where both keys are loaded, and the caller gets back a wrapper
    that only needs the new key.
    """
    key_v1, key_v2 = generate_master_key(), generate_master_key()
    user_id = uuid.uuid4()

    cipher_v1 = EnvelopeCipher(f"v1:{key_v1}")
    dek, wrapped_v1, kid_v1 = cipher_v1.get_or_create_user_key(user_id)
    assert kid_v1 == "v1"

    rotating = EnvelopeCipher(f"v2:{key_v2},v1:{key_v1}")
    dek_again, wrapped_v2, kid_v2 = rotating.get_or_create_user_key(
        user_id, existing_wrapped_dek=wrapped_v1
    )
    assert (dek_again, kid_v2) == (dek, "v2")
    assert wrapped_v2 != wrapped_v1

    # The returned wrapper works with v2 alone; the old one does not.
    v2_only = EnvelopeCipher(f"v2:{key_v2}")
    assert unwrap_data_key(wrapped_v2, user_id=user_id, keyring=v2_only.keyring)[0] == dek
    with pytest.raises(DecryptError, match="not loaded"):
        unwrap_data_key(wrapped_v1, user_id=user_id, keyring=v2_only.keyring)

    # A cached (previously registered) wrapper takes the same path.
    rotating.register_user_key(user_id, wrapped_v1)
    assert rotating.get_or_create_user_key(user_id)[2] == "v2"


def test_encrypt_field_with_an_explicit_data_key_checks_it_matches() -> None:
    """``data_key``/``wrapped_dek`` are used by the rotator; a mismatch is fatal.

    The rotator passes the *new* user DEK explicitly so the row and the user
    record move together. If the two arguments disagree, one of them is stale,
    and writing the row anyway is how a database ends up with rows that cannot
    be read after the old key is retired.
    """
    cipher = _cipher()
    user_id = uuid.uuid4()
    dek_a = generate_data_key()
    dek_b = generate_data_key()
    wrapped_a, _ = wrap_data_key(dek_a, user_id=user_id, keyring=cipher.keyring)

    with pytest.raises(ValueError, match="data_key must be"):
        cipher.encrypt_field("x", user_id=user_id, field_name="body", data_key=b"too-short")

    with pytest.raises(DecryptError, match="does not match provided data_key"):
        cipher.encrypt_field(
            "x", user_id=user_id, field_name="body", data_key=dek_b, wrapped_dek=wrapped_a
        )

    # data_key alone: the cipher wraps it under the active master key itself.
    envelope = cipher.encrypt_field(
        "rotated row", user_id=user_id, field_name="body", data_key=dek_a
    )
    assert (
        cipher.decrypt_field(envelope, user_id=user_id, field_name="body", wrapped_dek=wrapped_a)
        == "rotated row"
    )
    # ...and the DEK it wrapped is the one that was passed in.
    assert (
        unwrap_data_key(
            cipher._user_wrapped_deks[str(user_id)], user_id=user_id, keyring=cipher.keyring
        )[0]
        == dek_a
    )

    with pytest.raises(TypeError, match="plaintext must be a str"):
        cipher.encrypt_field(b"bytes", user_id=user_id, field_name="body")  # type: ignore[arg-type]


def test_round_trip_through_the_module_level_helpers() -> None:
    """``encrypt_field``/``decrypt_field`` resolve the process cipher, not a global."""
    cipher = _cipher()
    configure_cipher(cipher)
    user_id = uuid.uuid4()
    envelope = encrypt_field("module level", user_id=user_id, field_name="body")
    assert decrypt_field(envelope, user_id=user_id, field_name="body") == "module level"
    assert get_envelope_cipher() is cipher
    configure_cipher(None)


def test_get_envelope_cipher_accepts_a_foreign_cipher_with_a_keyring() -> None:
    """A custom :class:`FieldCipher` that exposes a keyring is adapted, not refused.

    ``app.core.crypto.FernetCipher`` is exactly that shape, and the Day-3
    compatibility layer still installs it. If this branch vanished, every
    envelope call in that configuration would raise instead of working.
    """

    class AdaptableCipher:
        """Minimal FieldCipher with a keyring attribute (not an EnvelopeCipher)."""

        def __init__(self, ring: MasterKeyRing) -> None:
            self.keyring = ring

        def encrypt(self, plaintext: str) -> bytes:  # pragma: no cover - unused
            raise NotImplementedError

        def decrypt(self, ciphertext: bytes) -> str:  # pragma: no cover - unused
            raise NotImplementedError

    ring = MasterKeyRing.from_key_material(generate_master_key())
    configure_cipher(AdaptableCipher(ring))  # type: ignore[arg-type]
    adapted = get_envelope_cipher()
    assert isinstance(adapted, EnvelopeCipher)
    assert adapted.active_key_id == ring.active_key_id

    configure_cipher(AdaptableCipher(MasterKeyRing.from_key_material(generate_master_key())))  # type: ignore[arg-type]
    user_id = uuid.uuid4()
    assert (
        decrypt_field(
            encrypt_field("adapted round trip", user_id=user_id, field_name="body"),
            user_id=user_id,
            field_name="body",
        )
        == "adapted round trip"
    )


def test_get_envelope_cipher_refuses_a_plain_field_cipher() -> None:
    """A cipher with no keyring is a configuration error, not a silent downgrade."""

    class PlainCipher:
        def encrypt(self, plaintext: str) -> bytes:
            return plaintext.encode()

        def decrypt(self, ciphertext: bytes) -> str:
            return ciphertext.decode()

    plain: FieldCipher = PlainCipher()
    configure_cipher(plain)
    with pytest.raises(CipherNotConfiguredError, match="does not support envelope encryption"):
        get_envelope_cipher()
    configure_cipher(None)


def test_envelope_cipher_accepts_a_ready_made_keyring() -> None:
    """Rotation builds a ring once and reuses it; the constructor path must work."""
    ring = MasterKeyRing.from_key_material(generate_master_key(), default_key_id="v3")
    cipher = EnvelopeCipher(ring)
    assert cipher.keyring is ring
    assert cipher.active_key_id == "v3"
    assert EnvelopeCipher.from_settings(Settings(_env_file=None, field_encryption_key=None)) is None


def test_encrypt_and_decrypt_compatibility_helpers_use_the_system_subject() -> None:
    """Day-3 ``encrypt``/``decrypt`` still round-trip, legacy blobs included."""
    cipher = _cipher()
    envelope = cipher.encrypt("system-scoped value")
    assert is_envelope_ciphertext(envelope)
    assert cipher.decrypt(envelope) == "system-scoped value"

    fernet = Fernet(generate_master_key().encode())
    cipher_with_legacy_key = EnvelopeCipher(fernet._signing_key and generate_master_key())
    # A legacy blob decrypts only through the key that produced it.
    raw_key = generate_master_key()
    legacy = Fernet(raw_key.encode()).encrypt(b"day three")
    assert EnvelopeCipher(raw_key).decrypt(legacy) == "day three"
    assert cipher_with_legacy_key is not None


def test_fernet_cipher_adapts_envelope_encryption_for_the_data_layer() -> None:
    """``app.core.crypto.FernetCipher`` is the adapter the models actually use."""
    from app.core.crypto import FernetCipher

    settings = Settings(
        _env_file=None,
        field_encryption_key=generate_master_key(),
        field_encryption_key_id="v9",
    )
    cipher = FernetCipher.from_settings(settings)
    assert cipher is not None
    ciphertext = cipher.encrypt("stored by the data layer")
    assert cipher.decrypt(ciphertext) == "stored by the data layer"
    assert FernetCipher.from_settings(Settings(_env_file=None, field_encryption_key=None)) is None


def test_envelope_header_sizes_are_consistent_with_the_parser() -> None:
    """A generated envelope is exactly the size its own parser expects.

    The parser and the writer share the format by hand (no schema), so this test
    pins the arithmetic: magic(4) + version(1) + kid_len(1) + kid + wdek_len(2) +
    wdek + nonce(12) + at-least-tag bytes of ciphertext.
    """
    cipher = _cipher()
    user_id = uuid.uuid4()
    envelope = cipher.encrypt_field("", user_id=user_id, field_name="body")
    kid_len = envelope[5]
    wrapped_len = _wrapped_dek_len(envelope)
    header_len = 6 + kid_len + 2 + wrapped_len + NONCE_BYTES
    assert header_len == len(envelope) - TAG_BYTES  # empty plaintext → tag only
    assert inspect_envelope_key_id(envelope) == "v1"
    assert len(base64.urlsafe_b64decode(generate_master_key())) == 32
    assert len(generate_data_key()) == DEK_BYTES == 32
