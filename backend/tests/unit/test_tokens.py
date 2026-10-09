"""JWT access/refresh tokens and the hash-at-rest helper (Day 4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from app.core.tokens import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    TokenExpiredError,
    TokenInvalidError,
    TokenService,
    hash_token,
)

SECRET = "unit-test-signing-secret-0123456789abcdef"


@pytest.fixture
def service() -> TokenService:
    return TokenService(SECRET)


def test_access_token_round_trip(service: TokenService) -> None:
    user_id = uuid.uuid4()
    token, claims = service.issue_access(user_id)
    assert claims.subject == user_id
    assert claims.token_type == ACCESS_TOKEN_TYPE
    assert claims.family_id is None

    verified = service.verify(token, expected_type=ACCESS_TOKEN_TYPE)
    assert verified.subject == user_id
    assert verified.token_id == claims.token_id
    # exp is whole seconds on the wire, so compare at that resolution.
    assert verified.expires_at.timestamp() == int(claims.expires_at.timestamp())


def test_refresh_token_carries_its_family(service: TokenService) -> None:
    user_id, family_id = uuid.uuid4(), uuid.uuid4()
    token, claims = service.issue_refresh(user_id, family_id=family_id)
    assert claims.family_id == family_id

    verified = service.verify(token, expected_type=REFRESH_TOKEN_TYPE)
    assert verified.family_id == family_id
    assert verified.token_type == REFRESH_TOKEN_TYPE


def test_refresh_token_ids_can_be_pinned(service: TokenService) -> None:
    """The jti of a refresh token is chosen up front so its row can be stored."""
    token_id = uuid.uuid4()
    token, claims = service.issue_refresh(uuid.uuid4(), family_id=uuid.uuid4(), token_id=token_id)
    assert claims.token_id == token_id
    assert service.verify(token, expected_type=REFRESH_TOKEN_TYPE).token_id == token_id


def test_expired_token_raises_the_expired_error(service: TokenService) -> None:
    user_id = uuid.uuid4()
    past = datetime.now(UTC) - timedelta(hours=1)
    token, _ = service.issue_access(user_id, now=past)
    with pytest.raises(TokenExpiredError):
        service.verify(token, expected_type=ACCESS_TOKEN_TYPE)


def test_tampered_token_raises_invalid(service: TokenService) -> None:
    token, _ = service.issue_access(uuid.uuid4())
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(TokenInvalidError):
        service.verify(tampered, expected_type=ACCESS_TOKEN_TYPE)


def test_token_signed_with_another_secret_is_invalid() -> None:
    other = TokenService("a-completely-different-secret-0123456789")
    token, _ = other.issue_access(uuid.uuid4())
    with pytest.raises(TokenInvalidError):
        TokenService(SECRET).verify(token, expected_type=ACCESS_TOKEN_TYPE)


def test_wrong_type_is_rejected(service: TokenService) -> None:
    """An access token is never a refresh token and vice versa."""
    access, _ = service.issue_access(uuid.uuid4())
    with pytest.raises(TokenInvalidError, match="type"):
        service.verify(access, expected_type=REFRESH_TOKEN_TYPE)

    refresh, _ = service.issue_refresh(uuid.uuid4(), family_id=uuid.uuid4())
    with pytest.raises(TokenInvalidError, match="type"):
        service.verify(refresh, expected_type=ACCESS_TOKEN_TYPE)


def test_missing_claims_are_invalid(service: TokenService) -> None:
    bare = pyjwt.encode({"sub": str(uuid.uuid4())}, SECRET, algorithm="HS256")
    with pytest.raises(TokenInvalidError):
        service.verify(bare, expected_type=ACCESS_TOKEN_TYPE)


def test_garbage_strings_are_invalid_not_crashes(service: TokenService) -> None:
    for garbage in ("", "not.a.jwt", "a" * 500, ".."):
        with pytest.raises(TokenInvalidError):
            service.verify(garbage, expected_type=ACCESS_TOKEN_TYPE)


def test_alg_confusion_is_not_possible(service: TokenService) -> None:
    """A token with ``alg: none`` must never verify."""
    user_id = uuid.uuid4()
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": str(user_id),
        "jti": str(uuid.uuid4()),
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + 60,
    }
    none_token = pyjwt.encode(payload, key="", algorithm="none")
    with pytest.raises(TokenInvalidError):
        service.verify(none_token, expected_type=ACCESS_TOKEN_TYPE)


def test_service_requires_a_secret() -> None:
    with pytest.raises(ValueError, match="secret"):
        TokenService("")


def test_ttl_properties() -> None:
    service = TokenService(SECRET, access_ttl=timedelta(minutes=5), refresh_ttl=timedelta(days=2))
    assert service.access_ttl == timedelta(minutes=5)
    assert service.refresh_ttl == timedelta(days=2)


def test_hash_token_is_sha256_hex_of_the_token() -> None:
    token = "some.refresh.token"
    digest = hash_token(token)
    assert digest == hash_token(token), "hashing is deterministic"
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    assert token not in digest
    assert hash_token(token + "!") != digest


def test_unparseable_subject_or_jti_are_invalid(service: TokenService) -> None:
    """A correctly signed token with junk ids is still not a token."""
    now = int(datetime.now(UTC).timestamp())
    for bad_sub, bad_jti in (("not-a-uuid", str(uuid.uuid4())), (str(uuid.uuid4()), "nope")):
        payload = {
            "sub": bad_sub,
            "jti": bad_jti,
            "type": ACCESS_TOKEN_TYPE,
            "iat": now,
            "exp": now + 60,
        }
        token = pyjwt.encode(payload, SECRET, algorithm="HS256")
        with pytest.raises(TokenInvalidError, match="claims"):
            service.verify(token, expected_type=ACCESS_TOKEN_TYPE)


def test_refresh_token_without_a_family_is_invalid(service: TokenService) -> None:
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "sub": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "type": REFRESH_TOKEN_TYPE,
        "iat": now,
        "exp": now + 60,
    }
    token = pyjwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(TokenInvalidError, match="family"):
        service.verify(token, expected_type=REFRESH_TOKEN_TYPE)
