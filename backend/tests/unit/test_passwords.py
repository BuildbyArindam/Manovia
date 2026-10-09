"""Password hashing and the password policy (Day 4)."""

from __future__ import annotations

import pytest

from app.core.passwords import (
    DEFAULT_MAX_PASSWORD_LENGTH,
    DEFAULT_MIN_PASSWORD_LENGTH,
    Argon2Hasher,
    FakePasswordHasher,
    PasswordTooLongError,
    PasswordTooShortError,
    dummy_verify,
    get_password_hasher,
    is_plausible_email,
    normalise_email,
    validate_password,
)


class TestValidatePassword:
    def test_accepts_a_ten_character_password(self) -> None:
        validate_password("ten chars!")  # exactly the minimum

    def test_rejects_short_passwords_with_a_clear_message(self) -> None:
        with pytest.raises(PasswordTooShortError, match="at least 10 characters"):
            validate_password("short")

    def test_no_composition_theatre(self) -> None:
        # NIST SP 800-63B: length only. All-lowercase, no digits, no symbols.
        validate_password("correcthorsebatterystaple")

    def test_rejects_oversized_passwords(self) -> None:
        with pytest.raises(PasswordTooLongError, match="at most 256 characters"):
            validate_password("x" * (DEFAULT_MAX_PASSWORD_LENGTH + 1))

    def test_bounds_are_configurable(self) -> None:
        with pytest.raises(PasswordTooShortError):
            validate_password("abc", min_length=5)
        validate_password("abc", min_length=3, max_length=3)
        with pytest.raises(PasswordTooLongError):
            validate_password("abcd", min_length=3, max_length=3)

    def test_default_bounds_match_the_day_4_rules(self) -> None:
        assert DEFAULT_MIN_PASSWORD_LENGTH == 10
        assert DEFAULT_MAX_PASSWORD_LENGTH == 256


class TestArgon2Hasher:
    def test_hash_is_self_describing_and_verifies(self) -> None:
        hasher = Argon2Hasher()
        digest = hasher.hash("a-perfectly-fine-passphrase")
        assert digest.startswith("$argon2id$")
        assert hasher.verify(digest, "a-perfectly-fine-passphrase")

    def test_wrong_password_never_matches(self) -> None:
        hasher = Argon2Hasher()
        digest = hasher.hash("a-perfectly-fine-passphrase")
        assert not hasher.verify(digest, "a-perfectly-fine-passphras")
        assert not hasher.verify(digest, "")

    def test_malformed_hash_returns_false_not_an_exception(self) -> None:
        hasher = Argon2Hasher()
        assert not hasher.verify("scrypt$deadbeef$cafe", "anything")
        assert not hasher.verify("", "anything")
        assert not hasher.verify("$argon2id$broken", "anything")

    def test_hashes_are_salted(self) -> None:
        hasher = Argon2Hasher()
        assert hasher.hash("same") != hasher.hash("same")


class TestFakePasswordHasher:
    def test_round_trip(self) -> None:
        hasher = FakePasswordHasher()
        digest = hasher.hash("fake-passphrase")
        assert digest.startswith("fake$")
        assert hasher.verify(digest, "fake-passphrase")
        assert not hasher.verify(digest, "other")

    def test_is_recognised_by_the_protocol(self) -> None:
        from app.core.passwords import PasswordHasher

        assert isinstance(FakePasswordHasher(), PasswordHasher)
        assert isinstance(Argon2Hasher(), PasswordHasher)


class TestTimingEqualiser:
    def test_dummy_verify_always_fails_but_burns_the_work(self) -> None:
        assert dummy_verify("whatever-the-attacker-sent") is False
        assert dummy_verify("") is False

    def test_get_password_hasher_is_a_process_singleton(self) -> None:
        assert get_password_hasher() is get_password_hasher()


class TestEmailNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  Person@Example.Test ", "person@example.test"),
            ("PERSON@EXAMPLE.TEST", "person@example.test"),
            ("person@example.test", "person@example.test"),
        ],
    )
    def test_normalise(self, raw: str, expected: str) -> None:
        assert normalise_email(raw) == expected

    @pytest.mark.parametrize(
        "email",
        [
            "person@example.test",
            "person+tag@sub.example.co.uk",
        ],
    )
    def test_plausible_addresses(self, email: str) -> None:
        assert is_plausible_email(email)

    @pytest.mark.parametrize(
        "email",
        [
            "",
            "not-an-email",
            "no-at-sign.example.test",
            "spaces in@example.test",
            "double@@example.test",
            "nodot@example",
            "x" * 311 + "@example.test",  # over 320 chars
        ],
    )
    def test_implausible_addresses(self, email: str) -> None:
        assert not is_plausible_email(email)
