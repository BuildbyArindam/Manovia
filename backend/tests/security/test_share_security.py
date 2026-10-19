"""Day 47 security tests for the clinician-share bridge.

The brief names six things that must be proven: an expired link, a
wrong-passcode lockout, a revoked link, tampered ciphertext, link enumeration
and rate limiting. Each has its own test below, plus the properties that make
those six meaningful:

* the key is never stored, so a database dump is not a document dump;
* the token is never stored either — only its SHA-256;
* one refusal looks exactly like every other refusal, so the public endpoint
  cannot be used to learn which tokens exist;
* the passcode is part of the key derivation, so guessing the passcode is
  guessing the key, and a lockout is a cryptographic control and not only a UI
  one;
* each document's ciphertext is bound to its own token and name (AAD), so a
  document cannot be replayed into another share;
* the view budget is claimed in one atomic statement, so it cannot be raced;
* nothing secret reaches a log line, an access-log path or a Sentry event.

These tests use the real HTTP surface wherever the property is about the
surface, and the service or crypto module directly where the property is about
arithmetic. Shared plumbing lives in ``tests/sharing_helpers.py``.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import redact_path
from app.core.sentry import scrub_sentry_event
from app.db.repos.shares import ShareRepository
from app.models.share import ClinicianShare, ShareAccessEvent, share_aad
from app.models.user import User
from app.services.sharing import crypto
from app.services.sharing.passcode import GENERATED_DIGITS, verify_passcode
from app.services.sharing.service import ShareService, ShareUnavailableError
from tests.sharing_helpers import (
    API,
    EMAIL,
    NOTE,
    PUBLIC,
    create_share,
    derive_key,
    flip_bit,
    payload_of,
    person,
    prepared_share,
    seed_entries,
    selection,
)

UNAVAILABLE_CODE = "share_unavailable"
UNAVAILABLE_MESSAGE = "This link is not available."


async def expire(session: AsyncSession, share_id: str) -> None:
    """Put a link's expiry in the past, the way time would."""

    await session.execute(
        update(ClinicianShare)
        .where(ClinicianShare.id == uuid.UUID(share_id))
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await session.commit()


async def unlock(session: AsyncSession, share_id: str) -> None:
    """End a passcode lockout, the way the clock would."""

    await session.execute(
        update(ClinicianShare)
        .where(ClinicianShare.id == uuid.UUID(share_id))
        .values(locked_until=datetime.now(UTC) - timedelta(seconds=1), passcode_failures=0)
    )
    await session.commit()


async def row_for(session: AsyncSession, share_id: str) -> ClinicianShare:
    """The stored row, fresh from the database."""

    session.expire_all()
    statement = select(ClinicianShare).where(ClinicianShare.id == uuid.UUID(share_id))
    return (await session.execute(statement)).scalars().one()


# --------------------------------------------------------------------------- #
# 1. expiry
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_an_expired_link_is_refused_and_says_nothing_about_itself(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Past the expiry the link answers exactly like a token that never existed."""

    headers, created = await prepared_share(client, session)
    token = created["token"]

    assert (await client.get(f"{PUBLIC}/{token}")).status_code == 200

    await expire(session, created["share_id"])

    status = await client.get(f"{PUBLIC}/{token}")
    assert status.status_code == 404
    assert status.json()["error"]["code"] == UNAVAILABLE_CODE
    assert status.json()["error"]["message"] == UNAVAILABLE_MESSAGE

    opened = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": created["passcode"]})
    assert opened.status_code == 404
    assert opened.json()["error"]["code"] == UNAVAILABLE_CODE
    # The correct passcode and the correct key change nothing, and the reason is
    # not disclosed: "expired" would tell an attacker the token is real.
    assert "expired" not in opened.text.lower()

    audit = await client.get(f"{API}/{created['share_id']}/audit", headers=headers)
    assert audit.status_code == 200
    assert audit.json()["share"]["status"] == "expired"
    # Both probes above (the status call and the open call) are audited: the
    # owner can see that somebody tried the dead link twice.
    assert audit.json()["counts"]["expired"] == 2
    assert "Expired" in audit.json()["summary"]


@pytest.mark.asyncio
async def test_expiry_is_enforced_at_read_time_not_by_a_job(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """No background job has run, the row is untouched, and the link is dead."""

    _, created = await prepared_share(client, session)
    await expire(session, created["share_id"])
    row = await row_for(session, created["share_id"])
    assert row.revoked_at is None, "nothing revoked it; it simply ran out of time"
    assert row.view_count == 0

    refused = await client.post(
        f"{PUBLIC}/{created['token']}/open", json={"passcode": created["passcode"]}
    )
    assert refused.status_code == 404


# --------------------------------------------------------------------------- #
# 2. wrong-passcode lockout
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_wrong_passcodes_lock_the_link_and_the_lock_beats_the_right_one(
    client: httpx.AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
    """Five misses lock the link; the correct passcode is refused while locked."""

    settings = app.state.settings
    headers, created = await prepared_share(client, session)
    token = created["token"]
    attempts = settings.share_passcode_max_failures

    for index in range(attempts - 1):
        response = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": "wrong-passcode"})
        assert response.status_code == 403, response.text
        body = response.json()["error"]
        assert body["code"] == "passcode_incorrect"
        remaining = attempts - index - 1
        assert f"{remaining} " in body["message"], body
        assert "Retry-After" not in response.headers

    locked = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": "wrong-passcode"})
    assert locked.status_code == 423, locked.text
    assert locked.json()["error"]["code"] == "passcode_locked"
    assert int(locked.headers["Retry-After"]) == settings.share_lockout_seconds

    # The correct passcode is refused while the lock is running.
    correct = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": created["passcode"]})
    assert correct.status_code == 423
    assert "documents" not in correct.text

    row = await row_for(session, created["share_id"])
    assert row.locked_until is not None
    assert row.passcode_failures == 0, "the counter resets: the punishment is the wait"
    assert row.view_count == 0, "failed attempts must not consume the view budget"

    counts = (await client.get(f"{API}/{created['share_id']}/audit", headers=headers)).json()[
        "counts"
    ]
    assert counts["passcode_failed"] == attempts - 1
    assert counts["locked"] == 1
    assert counts.get("opened", 0) == 0

    # Once the lock runs out the link works again, and the misses are forgotten.
    await unlock(session, created["share_id"])
    opened = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": created["passcode"]})
    assert opened.status_code == 200, opened.text
    assert opened.json()["views_remaining"] == 4


@pytest.mark.asyncio
async def test_a_wrong_passcode_never_decrypts_anything(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """The passcode is key material: with the wrong one there is no document."""

    _, created = await prepared_share(client, session)
    wrong_key = derive_key(created, passcode="not-the-passcode")
    plaintext = b'{"kind": "manovia.appointment-summary"}'
    ciphertext, nonce = crypto.encrypt_document(
        derive_key(created), plaintext, aad=share_aad(created["token"], "payload")
    )
    with pytest.raises(crypto.ShareDecryptError):
        crypto.decrypt_document(wrong_key, ciphertext, nonce, aad=share_aad(created["token"], "payload"))
    assert len(created["passcode"]) >= GENERATED_DIGITS


# --------------------------------------------------------------------------- #
# 3. revocation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_revoked_link_stops_immediately_and_the_attempt_is_audited(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Revocation is not a soft delete: the very next request is refused."""

    headers, created = await prepared_share(client, session)
    token = created["token"]
    share_id = created["share_id"]

    opened = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": created["passcode"]})
    assert opened.status_code == 200

    assert (await client.post(f"{API}/{share_id}/revoke", headers=headers)).status_code == 200

    for _ in range(2):
        status = await client.get(f"{PUBLIC}/{token}")
        assert status.status_code == 404
        assert status.json()["error"]["code"] == UNAVAILABLE_CODE
        refused = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": created["passcode"]})
        assert refused.status_code == 404
        assert refused.json()["error"]["message"] == UNAVAILABLE_MESSAGE

    audit = (await client.get(f"{API}/{share_id}/audit", headers=headers)).json()
    assert audit["counts"]["revoked"] == 1
    assert audit["counts"]["opened"] == 1
    # Two loop iterations x (status + open) = four recorded attempts.
    assert audit["counts"]["access_after_revoke"] == 4
    assert audit["share"]["status"] == "revoked"
    # The API has no un-revoke: the row stays revoked.
    row = await row_for(session, share_id)
    assert row.revoked_at is not None


# --------------------------------------------------------------------------- #
# 4. tampered ciphertext
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tampering_with_the_ciphertext_is_detected_and_gives_no_oracle(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """One bit changed, one byte dropped, one wrong key: one kind of failure."""

    _, created = await prepared_share(client, session)
    opened = await client.post(
        f"{PUBLIC}/{created['token']}/open", json={"passcode": created["passcode"]}
    )
    assert opened.status_code == 200
    item = {entry["name"]: entry for entry in opened.json()["documents"]}["payload"]
    key = derive_key(created)
    raw = crypto.decode_document(item["ciphertext"])
    nonce = crypto.decode_bytes(item["nonce"], expected_length=crypto.NONCE_BYTES, what="nonce")
    aad = share_aad(created["token"], "payload")

    assert payload_of(crypto.decrypt_document(key, raw, nonce, aad=aad)).note == NOTE

    tampered = [
        ("first bit flipped", flip_bit(raw)),
        ("last bit flipped (inside the tag)", flip_bit(raw, len(raw) - 1)),
        ("truncated by one byte", raw[:-1]),
        ("a byte appended", raw + b"\x00"),
        ("all zeroes of the same length", bytes(len(raw))),
    ]
    for label, altered in tampered:
        with pytest.raises(crypto.ShareDecryptError):
            crypto.decrypt_document(key, altered, nonce, aad=aad)

    # A fresh nonce for the same ciphertext fails the same way, with the same
    # single exception type: there is no "wrong nonce" vs "wrong key" vs
    # "tampered" distinction for an attacker to probe.
    _, other_nonce = crypto.encrypt_document(key, b"{}", aad=aad)
    with pytest.raises(crypto.ShareDecryptError):
        crypto.decrypt_document(key, raw, other_nonce, aad=aad)

    stranger_key = crypto.derive_key(
        secret=crypto.new_secret(), passcode=created["passcode"], token=created["token"]
    )
    with pytest.raises(crypto.ShareDecryptError):
        crypto.decrypt_document(stranger_key, raw, nonce, aad=aad)

    # The wire format is strict, so a malformed document is refused before it
    # reaches the cipher at all.
    for bad in ("", "not base64!!", "====", item["ciphertext"][:-2]):
        with pytest.raises(crypto.ShareKeyError):
            crypto.decode_bytes(bad, expected_length=len(raw), what="ciphertext")


@pytest.mark.asyncio
async def test_documents_are_bound_to_their_own_share_and_name(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """AAD binds each document to one token and one name: no replay, no swap."""

    _, first = await prepared_share(client, session)
    _, second = await prepared_share(client, session, email="second@example.test")

    opened = await client.post(
        f"{PUBLIC}/{first['token']}/open", json={"passcode": first["passcode"]}
    )
    item = {entry["name"]: entry for entry in opened.json()["documents"]}["payload"]
    raw = crypto.decode_document(item["ciphertext"])
    nonce = crypto.decode_bytes(item["nonce"], expected_length=crypto.NONCE_BYTES, what="nonce")
    key = derive_key(first)

    with pytest.raises(crypto.ShareDecryptError):
        crypto.decrypt_document(key, raw, nonce, aad=share_aad(second["token"], "payload"))
    with pytest.raises(crypto.ShareDecryptError):
        crypto.decrypt_document(key, raw, nonce, aad=share_aad(first["token"], "pdf"))
    assert crypto.decrypt_document(key, raw, nonce, aad=share_aad(first["token"], "payload"))

    # Two shares never share a key, even with the same passcode.
    assert derive_key(first, passcode="same") != derive_key(second, passcode="same")


# --------------------------------------------------------------------------- #
# 5. enumeration
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unknown_tokens_are_indistinguishable_from_dead_ones(
    client_factory: Callable[..., httpx.AsyncClient], session: AsyncSession
) -> None:
    """A guessed token learns nothing: same status, same code, same message.

    The per-address budget is raised for this test only, so that what is being
    measured is the shape of the refusal and not the throttle — the default
    budget, and the 429 it produces, are asserted in the rate-limit test below.
    """

    client = client_factory(share_rate_limit_per_minute=500)
    _, created = await prepared_share(client, session)
    await expire(session, created["share_id"])
    mutated = created["token"][:-1] + ("A" if created["token"][-1] != "A" else "B")

    well_formed = [crypto.new_token(), "a" * 22, "0" * 22, mutated, "short", "a" * 400, "%00"]
    messages: list[str] = []
    for token in well_formed:
        status = await client.get(f"{PUBLIC}/{token}")
        opened = await client.post(f"{PUBLIC}/{token}/open", json={"passcode": "whatever"})
        for response in (status, opened):
            assert response.status_code == 404, (token, response.text)
            error = response.json()["error"]
            assert error["code"] == UNAVAILABLE_CODE, (token, response.text)
            assert error["message"] == UNAVAILABLE_MESSAGE, token
            messages.append(error["message"])

    # Paths that never reach the route must still be a 404 and never a 500.
    for path in (f"{PUBLIC}/", f"{PUBLIC}/../../etc/passwd", "/api/v1/public/", "/api/v1/shares"):
        response = await client.get(path)
        assert response.status_code in {401, 404}, (path, response.status_code)

    assert len(set(messages)) == 1
    real = await client.get(f"{PUBLIC}/{created['token']}")
    assert real.json()["error"]["message"] == messages[0]

    # Guessing writes nothing: one share exists (the real one), and the only
    # audit lines are the creation and the one expired-token probe above.
    rows = (await session.execute(select(ClinicianShare))).scalars().all()
    assert len(rows) == 1
    events = (await session.execute(select(ShareAccessEvent))).scalars().all()
    assert [event.kind.value for event in events] == ["created", "expired"]


@pytest.mark.asyncio
async def test_a_token_is_never_stored_and_neither_is_a_key(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """A database dump yields hashes and ciphertext: nothing readable, no key."""

    _, created = await prepared_share(client, session)
    row = await row_for(session, created["share_id"])

    columns = {column.name for column in ClinicianShare.__table__.columns}
    assert not any(
        name.endswith("_key") or name in {"key", "secret", "content_key"} for name in columns
    ), sorted(columns)
    assert "token" not in columns
    assert "passcode" not in columns

    dumped = json.dumps(
        {name: getattr(row, name) for name in columns}, default=str, ensure_ascii=False
    )
    for secret in (created["token"], created["secret"], created["passcode"], NOTE):
        assert secret not in dumped, secret

    assert row.token_hash == crypto.hash_token(created["token"])
    assert len(row.token_hash) == 64
    assert verify_passcode(created["passcode"], row.passcode_salt, row.passcode_hash) is True
    assert verify_passcode(created["passcode"] + "x", row.passcode_salt, row.passcode_hash) is False

    # The token prefix is the only human-visible piece, and it is far too short
    # to identify a link on its own.
    assert row.token_prefix == created["token"][:8]
    assert len(created["token"]) - len(row.token_prefix) >= 14


# --------------------------------------------------------------------------- #
# 6. rate limiting
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_public_endpoints_are_rate_limited_per_address(
    client_factory: Callable[..., httpx.AsyncClient], session: AsyncSession
) -> None:
    """A scripted enumerator is throttled long before it learns anything."""

    strict = client_factory(share_rate_limit_per_minute=3)
    loose = client_factory(share_rate_limit_per_minute=500)

    headers, created = await prepared_share(loose, session)
    token = created["token"]

    codes: list[int] = []
    for _ in range(6):
        response = await strict.get(f"{PUBLIC}/{token}")
        codes.append(response.status_code)
        if response.status_code == 429:
            assert int(response.headers["Retry-After"]) >= 1
            assert response.json()["error"]["code"] == "rate_limited"
    assert codes == [200, 200, 200, 429, 429, 429], codes

    # Guessing tokens is throttled by the same per-address budget.
    guesses = [
        await strict.post(f"{PUBLIC}/{crypto.new_token()}/open", json={"passcode": "x"})
        for _ in range(3)
    ]
    assert [response.status_code for response in guesses] == [429, 429, 429]

    # The owner's own endpoints are not on the public budget.
    assert (await strict.get(API, headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_the_view_budget_cannot_be_overdrawn(
    client: httpx.AsyncClient, session: AsyncSession, database: object
) -> None:
    """One view allowed, five claims: exactly one succeeds.

    The claim is a single ``UPDATE ... WHERE view_count < view_limit`` whose
    rowcount is the answer, so two simultaneous opens cannot both pass — the
    database serialises them. The test calls the repository five times over five
    sessions rather than concurrently, because on the SQLite test database
    concurrency would test the driver's file locking rather than the statement.
    """

    from app.db.session import Database

    _, created = await prepared_share(client, session, view_limit=1)
    share_id = uuid.UUID(created["share_id"])
    opened = await client.post(
        f"{PUBLIC}/{created['token']}/open", json={"passcode": created["passcode"]}
    )
    assert opened.status_code == 200
    assert opened.json()["views_remaining"] == 0

    refused = await client.post(
        f"{PUBLIC}/{created['token']}/open", json={"passcode": created["passcode"]}
    )
    assert refused.status_code == 404
    assert refused.json()["error"]["code"] == UNAVAILABLE_CODE
    row = await row_for(session, created["share_id"])
    assert row.view_count == 1, "a refused open must not consume another view"

    # Give the link its view back, then let five separate sessions try to take
    # it. Each one commits, exactly as the endpoint does after a real open, so
    # what is being tested is the statement's WHERE clause and not an uncommitted
    # transaction that would roll back and hide the answer.
    await session.execute(
        update(ClinicianShare).where(ClinicianShare.id == share_id).values(view_count=0)
    )
    await session.commit()

    built = database if isinstance(database, Database) else None
    assert built is not None
    results: list[bool] = []
    for _ in range(5):
        async with built.session_factory() as other:
            claimed = await ShareRepository(other).claim_view(share_id)
            await other.commit()
            results.append(claimed)
    assert results == [True, False, False, False, False], results
    assert (await row_for(session, created["share_id"])).view_count == 1


# --------------------------------------------------------------------------- #
# logs and error reporting
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_secrets_never_reach_a_log_line(
    client: httpx.AsyncClient,
    session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Create, open, fail and audit: no token, key, passcode or document text."""

    caplog.set_level(logging.INFO)
    # The test client logs its own request lines, URL and all; that is httpx's
    # client-side log, not something this application writes. Quieting it here
    # keeps the assertion about *our* log output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    headers, created = await prepared_share(client, session)
    token = created["token"]
    await client.post(f"{PUBLIC}/{token}/open", json={"passcode": created["passcode"]})
    for _ in range(3):
        await client.post(f"{PUBLIC}/{token}/open", json={"passcode": "wrong-passcode"})
    await client.get(f"{API}/{created['share_id']}/audit", headers=headers)

    captured = capsys.readouterr()
    logged = "\n".join([caplog.text, captured.out, captured.err])
    assert logged.strip(), "the test must have something to look at"
    for secret in (
        token,
        created["secret"],
        created["passcode"],
        "wrong-passcode",
        NOTE,
        created["consent_sha256"],
    ):
        assert secret not in logged, secret
    # The access log shows the route with a placeholder, not the credential.
    assert "<share_token>" in logged
    assert f"{PUBLIC}/{token}" not in logged


def test_redact_path_replaces_the_share_token_and_leaves_other_paths_alone() -> None:
    """One rule, applied wherever a path is logged or reported."""

    token = crypto.new_token()
    assert redact_path(f"{PUBLIC}/{token}") == f"{PUBLIC}/<share_token>"
    assert redact_path(f"{PUBLIC}/{token}/open") == f"{PUBLIC}/<share_token>/open"
    assert redact_path(f"{API}/options") == f"{API}/options"
    assert redact_path(None) == ""
    assert redact_path("") == ""

    scrubbed = scrub_sentry_event(
        {
            "request": {"url": f"https://manovia.app{PUBLIC}/{token}/open?x=1", "data": NOTE},
            "event_id": "abc",
        },
        None,
    )
    assert scrubbed["request"]["url"] == f"https://manovia.app{PUBLIC}/<share_token>/open"
    assert token not in json.dumps(scrubbed)
    assert NOTE not in json.dumps(scrubbed)


@pytest.mark.asyncio
async def test_the_deployment_can_turn_the_whole_bridge_off(
    client_factory: Callable[..., httpx.AsyncClient]
) -> None:
    """With SHARE_ENABLED=false the routes do not exist at all: 404, not 403."""

    off = client_factory(share_enabled=False)

    assert (await off.get(API + "/options")).status_code == 404
    assert (await off.get(f"{PUBLIC}/{crypto.new_token()}")).status_code == 404

    schema = await off.get("/openapi.json")
    assert schema.status_code == 200
    paths = json.dumps(schema.json()["paths"])
    assert "/api/v1/public/shares" not in paths
    assert "/api/v1/shares" not in paths


# --------------------------------------------------------------------------- #
# service-level refusals
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_service_refuses_every_unusable_link_the_same_way(
    client: httpx.AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
    """Out of budget, expired, revoked, unknown: one exception, one 404."""

    _, created = await prepared_share(client, session, view_limit=1)
    service = ShareService(session, app.state.settings)
    token = created["token"]
    passcode = created["passcode"]

    assert (await client.post(f"{PUBLIC}/{token}/open", json={"passcode": passcode})).status_code == 200

    with pytest.raises(ShareUnavailableError):
        await service.open_public(token, passcode)
    with pytest.raises(ShareUnavailableError):
        await service.public_status(token)

    await expire(session, created["share_id"])
    with pytest.raises(ShareUnavailableError):
        await service.open_public(token, passcode)

    with pytest.raises(ShareUnavailableError):
        await service.open_public(crypto.new_token(), passcode)
    with pytest.raises(ShareUnavailableError):
        await service.open_public("", passcode)

    row = await row_for(session, created["share_id"])
    assert row.status == "expired"


@pytest.mark.asyncio
async def test_a_second_person_cannot_use_the_first_persons_share_id(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Ids are unguessable UUIDs and ownership is checked on every owner route."""

    _, created = await prepared_share(client, session)
    stranger, _ = await person(client, email="stranger@example.test")
    share_id = created["share_id"]

    for path in (f"{API}/{share_id}", f"{API}/{share_id}/audit"):
        assert (await client.get(path, headers=stranger)).status_code == 404
    assert (await client.post(f"{API}/{share_id}/revoke", headers=stranger)).status_code == 404
    assert (await client.get(f"{API}/{uuid.uuid4()}", headers=stranger)).status_code == 404

    # The stranger can turn on their own switch; it changes nobody else's data.
    preference = await client.post(API + "/preferences", headers=stranger, json={"enabled": True})
    assert preference.status_code == 200
    assert len((await session.execute(select(ClinicianShare))).scalars().all()) == 1
    assert (await client.get(f"{API}/{share_id}/audit", headers=stranger)).status_code == 404


@pytest.mark.asyncio
async def test_creating_a_share_twice_never_reuses_a_token_or_a_key(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Every link gets its own token, secret, passcode, nonce and derived key."""

    headers, first = await prepared_share(client, session)
    user = (await session.execute(select(User).where(User.email == EMAIL))).scalars().one()
    record_id = await seed_entries(session, user)
    second = await create_share(client, headers, selection(record_id))

    assert first["token"] != second["token"]
    assert first["secret"] != second["secret"]
    assert first["passcode"] != second["passcode"]
    assert derive_key(first) != derive_key(second)
    assert crypto.hash_token(first["token"]) != crypto.hash_token(second["token"])

    rows = (await session.execute(select(ClinicianShare))).scalars().all()
    assert len(rows) == 2
    assert len({row.token_hash for row in rows}) == 2
    assert len({bytes(row.payload_nonce) for row in rows}) == 2
    assert len({bytes(row.passcode_salt) for row in rows}) == 2
    assert len({crypto.encode_bytes(bytes(row.payload_ciphertext)) for row in rows}) == 2


def test_nonce_and_key_sizes_are_the_documented_ones() -> None:
    """The constants the viewer's WebCrypto call depends on cannot drift."""

    assert crypto.ALGORITHM == "A256GCM"
    assert crypto.KEY_BYTES == 32
    assert crypto.NONCE_BYTES == 12
    assert crypto.SECRET_BYTES == 32
    assert len(crypto.new_secret()) == crypto.SECRET_BYTES
    assert len(crypto.encode_bytes(crypto.new_secret())) == 43
    _, nonce = crypto.encrypt_document(
        secrets.token_bytes(32), b"{}", aad=share_aad(crypto.new_token(), "payload")
    )
    assert len(nonce) == crypto.NONCE_BYTES
