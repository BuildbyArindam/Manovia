"""Unit tests for the share passcode: generation, hashing, verification.

The passcode is not a login credential and it is not only a UI gate — it is an
input to the key derivation, so its strength is part of the encryption. Two
properties are tested here beyond the obvious round trip:

* a generated passcode is six digits from a cryptographic source, so guessing one
  is 10^6 work *per link*, on top of the 128-bit token and the 256-bit secret;
* verification is constant-time and salt-dependent: the same passcode hashes
  differently for every share, and a wrong-length or wrong-type stored hash
  refuses rather than raising.
"""

from __future__ import annotations

import hashlib

import pytest

from app.services.sharing.passcode import (
    GENERATED_DIGITS,
    ITERATIONS,
    KEY_BYTES,
    PASSCODE_MAX_LENGTH,
    PASSCODE_MIN_LENGTH,
    SALT_BYTES,
    PasscodeError,
    generate_passcode,
    hash_passcode,
    new_salt,
    validate,
    verify_passcode,
)


def test_generated_passcodes_are_digits_of_the_documented_length() -> None:
    """Six digits, from ``secrets``, and never the same twice in 500 draws."""

    drawn = [generate_passcode() for _ in range(500)]
    assert all(len(code) == GENERATED_DIGITS == 6 for code in drawn)
    assert all(code.isdigit() for code in drawn)
    assert len(set(drawn)) > 480, "a repeated passcode this often is not random"


def test_the_work_factor_and_sizes_are_the_documented_ones() -> None:
    """PBKDF2-HMAC-SHA256, 210k iterations, 32-byte output, 16-byte salt."""

    assert ITERATIONS == 210_000
    assert KEY_BYTES == 32
    assert SALT_BYTES == 16
    assert PASSCODE_MIN_LENGTH == 6
    assert PASSCODE_MAX_LENGTH == 64
    assert len(new_salt()) == SALT_BYTES
    assert len({new_salt() for _ in range(50)}) == 50


def test_hash_and_verify_round_trip() -> None:
    """The stored pair answers the question it exists to answer."""

    salt = new_salt()
    digest = hash_passcode("483920", salt)
    assert isinstance(digest, bytes)
    assert len(digest) == KEY_BYTES
    assert verify_passcode("483920", salt, digest) is True
    assert verify_passcode("483921", salt, digest) is False
    assert verify_passcode("", salt, digest) is False
    assert verify_passcode(" 483920 ", salt, digest) is False


def test_the_salt_is_what_makes_two_hashes_differ() -> None:
    """The same passcode on two shares is two unrelated hashes."""

    first_salt, second_salt = new_salt(), new_salt()
    assert hash_passcode("123456", first_salt) != hash_passcode("123456", second_salt)
    assert hash_passcode("123456", first_salt) == hash_passcode("123456", first_salt)
    # And a hash cannot be moved between salts.
    digest = hash_passcode("123456", first_salt)
    assert verify_passcode("123456", second_salt, digest) is False


def test_the_hash_is_not_a_bare_sha256_of_the_passcode() -> None:
    """A dump of the column must not be crackable by a rainbow table."""

    salt = new_salt()
    digest = hash_passcode("483920", salt)
    assert digest != hashlib.sha256(b"483920").digest()
    assert digest != hashlib.sha256(b"483920" + salt).digest()
    assert digest != hashlib.pbkdf2_hmac("sha256", b"483920", salt, 1)
    assert b"483920" not in digest


def test_a_malformed_stored_hash_refuses_instead_of_raising() -> None:
    """Verification is a comparison, not a place for an exception to escape."""

    salt = new_salt()
    for bad in (b"", b"short", b"\x00" * KEY_BYTES, None):
        assert verify_passcode("123456", salt, bad) is False  # type: ignore[arg-type]
    for bad_salt in (b"", None, b"x" * 4):
        assert verify_passcode("123456", bad_salt, hash_passcode("123456", salt)) is False  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "candidate",
    [
        pytest.param("12345", id="too-short"),
        pytest.param("", id="empty"),
        pytest.param("      ", id="only-spaces"),
        pytest.param("123456" + "x" * PASSCODE_MAX_LENGTH, id="too-long"),
    ],
)
def test_a_typed_passcode_is_checked_before_it_is_used(candidate: str) -> None:
    """The floor is a cryptographic setting, so it is enforced here too."""

    with pytest.raises(PasscodeError):
        validate(candidate)


@pytest.mark.parametrize(
    "candidate",
    [
        pytest.param("123456", id="six-digits"),
        pytest.param("correct-horse-battery", id="words"),
        pytest.param("  483920  ", id="surrounding-space-is-trimmed"),
        pytest.param("pässwörd-✓", id="unicode"),
        pytest.param("a" * PASSCODE_MAX_LENGTH, id="at-the-ceiling"),
    ],
)
def test_acceptable_passcodes_survive_validation_unchanged(candidate: str) -> None:
    """Trimmed, and otherwise exactly what the person typed."""

    assert validate(candidate) == candidate.strip()


def test_validation_rejects_a_non_string() -> None:
    """A JSON number is not a passcode."""

    with pytest.raises(PasscodeError):
        validate(123456)  # type: ignore[arg-type]
