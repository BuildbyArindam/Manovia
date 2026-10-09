"""Consent: requirements, recording decisions, and the require_consent gate."""

from __future__ import annotations

from typing import Any

import httpx

from app.models.enums import ConsentKind

GATE = "/api/v1/chat/sessions"  # the first consent-gated endpoint
VERSION = "2026-10-01"  # current version of every shipped document


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _guest(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body: dict[str, Any] = response.json()
    return body


async def _consent(
    client: httpx.AsyncClient, token: str, kinds: tuple[ConsentKind, ...], granted: bool = True
) -> httpx.Response:
    return await client.post(
        "/api/v1/consent",
        headers=_auth(token),
        json={
            "grants": [
                {"kind": kind.value, "version": VERSION, "granted": granted} for kind in kinds
            ]
        },
    )


# --------------------------------------------------------------------------- #
# Requirements                                                                  #
# --------------------------------------------------------------------------- #


async def test_requirements_are_public_and_name_every_document(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/consent/requirements")
    assert response.status_code == 200
    documents = response.json()["documents"]
    assert [document["kind"] for document in documents] == [kind.value for kind in ConsentKind]
    for document in documents:
        assert document["version"] == VERSION
        assert document["title"] and document["summary"]


async def test_requirements_carry_the_ai_disclosure_text(client: httpx.AsyncClient) -> None:
    documents = (await client.get("/api/v1/consent/requirements")).json()["documents"]
    disclosure = next(d for d in documents if d["kind"] == "ai_disclosure")
    assert "text" in disclosure
    lowered = disclosure["text"].lower()
    assert "ai-powered" in lowered
    assert "not written by a human" in lowered
    assert "crisis" in lowered


# --------------------------------------------------------------------------- #
# Recording decisions                                                           #
# --------------------------------------------------------------------------- #


async def test_recording_consent_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/consent",
        json={"grants": [{"kind": "terms", "version": VERSION, "granted": True}]},
    )
    assert response.status_code == 401


async def test_consent_is_recorded_for_the_signed_in_user(client: httpx.AsyncClient) -> None:
    session = await _guest(client)
    response = await _consent(
        client, session["access_token"], (ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS)
    )
    assert response.status_code == 201
    recorded = response.json()["recorded"]
    assert [entry["kind"] for entry in recorded] == ["ai_disclosure", "terms"]
    assert all(entry["granted"] for entry in recorded)
    assert all(entry["version"] == VERSION for entry in recorded)


async def test_grants_for_unknown_versions_are_rejected(client: httpx.AsyncClient) -> None:
    session = await _guest(client)
    response = await client.post(
        "/api/v1/consent",
        headers=_auth(session["access_token"]),
        json={"grants": [{"kind": "terms", "version": "1999-01-01", "granted": True}]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "consent_version_mismatch"


async def test_unknown_consent_kinds_are_rejected(client: httpx.AsyncClient) -> None:
    session = await _guest(client)
    response = await client.post(
        "/api/v1/consent",
        headers=_auth(session["access_token"]),
        json={"grants": [{"kind": "marketing", "version": VERSION, "granted": True}]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_empty_grant_lists_are_rejected(client: httpx.AsyncClient) -> None:
    session = await _guest(client)
    response = await client.post(
        "/api/v1/consent", headers=_auth(session["access_token"]), json={"grants": []}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# The gate                                                                      #
# --------------------------------------------------------------------------- #


async def test_gate_returns_403_consent_required_before_consenting(
    client: httpx.AsyncClient,
) -> None:
    session = await _guest(client)
    response = await client.post(GATE, headers=_auth(session["access_token"]))
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "consent_required"
    assert "ai_disclosure" in error["message"] and "terms" in error["message"]


async def test_gate_opens_after_posting_consent(client: httpx.AsyncClient) -> None:
    session = await _guest(client)
    headers = _auth(session["access_token"])

    blocked = await client.post(GATE, headers=headers)
    assert blocked.status_code == 403

    consented = await _consent(
        client, session["access_token"], (ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS)
    )
    assert consented.status_code == 201

    allowed = await client.post(GATE, headers=headers)
    assert allowed.status_code == 201
    assert allowed.json()["id"]


async def test_partial_consent_names_what_is_missing(client: httpx.AsyncClient) -> None:
    session = await _guest(client)
    headers = _auth(session["access_token"])

    await _consent(client, session["access_token"], (ConsentKind.AI_DISCLOSURE,))
    blocked = await client.post(GATE, headers=headers)
    assert blocked.status_code == 403
    error = blocked.json()["error"]
    assert error["code"] == "consent_required"
    assert error["message"] == "Consent required: terms."  # ai_disclosure is granted


async def test_withdrawal_flips_the_gate_again(client: httpx.AsyncClient) -> None:
    """Consent is append-only history: the newest row for a kind wins."""
    session = await _guest(client)
    headers = _auth(session["access_token"])

    await _consent(client, session["access_token"], (ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS))
    assert (await client.post(GATE, headers=headers)).status_code == 201

    withdrawn = await _consent(
        client, session["access_token"], (ConsentKind.AI_DISCLOSURE,), granted=False
    )
    assert withdrawn.status_code == 201

    blocked = await client.post(GATE, headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "consent_required"


async def test_grants_are_per_user(client: httpx.AsyncClient) -> None:
    first = await _guest(client)
    second = await _guest(client)
    await _consent(client, first["access_token"], (ConsentKind.AI_DISCLOSURE, ConsentKind.TERMS))

    # The consenting user passes the gate; the other guest does not.
    assert (await client.post(GATE, headers=_auth(first["access_token"]))).status_code == 201
    blocked = await client.post(GATE, headers=_auth(second["access_token"]))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "consent_required"
