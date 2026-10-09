"""Field encryption: the interface, the Fernet implementation, and configuration."""

import pytest

from app.core.config import Settings
from app.core.crypto import (
    CipherNotConfiguredError,
    DecryptError,
    FernetCipher,
    configure_cipher,
    decrypt,
    decrypt_optional,
    encrypt,
    encrypt_optional,
    get_cipher,
)

PLAINTEXT = "I cannot stop shaking since the accident."


def test_encrypt_then_decrypt_round_trips() -> None:
    cipher = FernetCipher(FernetCipher.generate_key())
    assert cipher.encrypt(PLAINTEXT) != PLAINTEXT.encode()
    assert cipher.decrypt(cipher.encrypt(PLAINTEXT)) == PLAINTEXT


def test_same_plaintext_encrypts_differently_each_time() -> None:
    cipher = FernetCipher(FernetCipher.generate_key())
    first, second = cipher.encrypt(PLAINTEXT), cipher.encrypt(PLAINTEXT)
    assert first != second, "a fixed IV would leak equality of messages"
    assert cipher.decrypt(first) == cipher.decrypt(second) == PLAINTEXT


def test_ciphertext_is_not_readable_as_ascii() -> None:
    cipher = FernetCipher(FernetCipher.generate_key())
    blob = cipher.encrypt(PLAINTEXT)
    assert isinstance(blob, bytes)
    assert PLAINTEXT not in blob.decode("latin-1")


def test_a_different_key_cannot_read_the_blob() -> None:
    writer = FernetCipher(FernetCipher.generate_key())
    reader = FernetCipher(FernetCipher.generate_key())
    blob = writer.encrypt(PLAINTEXT)
    with pytest.raises(DecryptError):
        reader.decrypt(blob)


def test_generated_keys_are_valid_but_not_reusable() -> None:
    first, second = FernetCipher.generate_key(), FernetCipher.generate_key()
    assert first != second
    assert len(first) == 44


@pytest.mark.parametrize("key", ["", "short", "not-base64!!", "bm90MzJieXRlcw=="])
def test_invalid_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError):
        FernetCipher(key)


def test_from_settings_is_none_without_a_key() -> None:
    settings = Settings(_env_file=None, field_encryption_key=None)
    assert FernetCipher.from_settings(settings) is None


def test_from_settings_builds_the_configured_cipher() -> None:
    settings = Settings(_env_file=None, field_encryption_key=FernetCipher.generate_key())
    cipher = FernetCipher.from_settings(settings)
    assert cipher is not None
    assert cipher.decrypt(cipher.encrypt("hello")) == "hello"


def test_module_level_helpers_use_the_configured_cipher() -> None:
    configure_cipher(FernetCipher(FernetCipher.generate_key()))
    assert decrypt(encrypt(PLAINTEXT)) == PLAINTEXT
    assert get_cipher() is not None


def test_using_the_helpers_without_a_cipher_fails_loudly() -> None:
    configure_cipher(None)
    with pytest.raises(CipherNotConfiguredError) as excinfo:
        encrypt(PLAINTEXT)
    # The message must say how to fix it, without containing any secret.
    assert "FIELD_ENCRYPTION_KEY" in str(excinfo.value)
    with pytest.raises(CipherNotConfiguredError):
        decrypt(b"whatever")


def test_optional_helpers_pass_none_through() -> None:
    configure_cipher(FernetCipher(FernetCipher.generate_key()))
    assert encrypt_optional(None) is None
    assert decrypt_optional(None) is None
    assert decrypt_optional(encrypt_optional(PLAINTEXT)) == PLAINTEXT
