"""Helpers shared by the Day 22 privacy tests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx

#: Current consent document version used by every other API test suite.
CONSENT_VERSION = "2026-10-01"

PRIVACY = "/api/v1/privacy"


@dataclass(frozen=True)
class Person:
    """A signed-in caller with the consents a feature needs."""

    id: uuid.UUID
    headers: dict[str, str]
    access_token: str
    refresh_token: str
    password: str | None = None


async def _grant(
    client: httpx.AsyncClient, headers: dict[str, str], kinds: tuple[str, ...]
) -> None:
    if not kinds:
        return
    granted = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": CONSENT_VERSION, "granted": True} for kind in kinds
            ]
        },
    )
    assert granted.status_code == 201


async def guest(
    client: httpx.AsyncClient,
    *,
    kinds: tuple[str, ...] = ("terms", "privacy"),
) -> Person:
    """An anonymous account, with or without consents recorded."""
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    await _grant(client, headers, kinds)
    return Person(
        id=uuid.UUID(body["user"]["id"]),
        headers=headers,
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
    )


async def account(
    client: httpx.AsyncClient,
    *,
    email: str = "privacy@example.test",
    password: str = "correct-horse-battery",
    kinds: tuple[str, ...] = ("terms", "privacy"),
) -> Person:
    """An email/password account (the re-authentication path needs one)."""
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    await _grant(client, headers, kinds)
    return Person(
        id=uuid.UUID(body["user"]["id"]),
        headers=headers,
        access_token=body["access_token"],
        refresh_token=body["refresh_token"],
        password=password,
    )
