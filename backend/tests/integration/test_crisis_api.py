"""Crisis resources endpoint: public, complete, and matching the shipped file."""

from __future__ import annotations

import httpx

from app.content.crisis import load_helplines

ENDPOINT = "/api/v1/crisis/resources"


async def test_resources_are_public(client: httpx.AsyncClient) -> None:
    """No token, no consent, no account: the endpoint answers anyway.

    This is the safety property of the endpoint — an auth wall in front of a
    helpline list is a bug, not a feature.
    """
    response = await client.get(ENDPOINT)

    assert response.status_code == 200
    body = response.json()
    assert body["last_verified"] == "2026-10-09"
    assert len(body["resources"]) >= 1


async def test_resources_match_the_shipped_file(client: httpx.AsyncClient) -> None:
    content = load_helplines()

    body = (await client.get(ENDPOINT)).json()

    assert body["disclaimer"] == content.disclaimer
    assert [resource["id"] for resource in body["resources"]] == [
        resource.id for resource in content.resources
    ]
    assert [resource["priority"] for resource in body["resources"]] == [
        resource.priority for resource in content.resources
    ]


async def test_every_resource_is_reachable(client: httpx.AsyncClient) -> None:
    body = (await client.get(ENDPOINT)).json()

    for resource in body["resources"]:
        assert resource["phone"] is not None or resource["sms"] is not None or resource["url"]
        assert resource["hours"] != ""
        assert resource["description"] != ""


async def test_a_signed_in_user_gets_the_same_list(client: httpx.AsyncClient) -> None:
    """Signing in must not change the answer."""
    guest = (await client.post("/api/v1/auth/guest")).json()
    headers = {"Authorization": f"Bearer {guest['access_token']}"}

    anonymous = (await client.get(ENDPOINT)).json()
    authenticated = (await client.get(ENDPOINT, headers=headers)).json()

    assert authenticated == anonymous


async def test_crisis_is_not_rate_limited_like_auth_endpoint(
    client_factory: object,
) -> None:
    """The global limiter applies, not the strict auth budget."""
    factory = client_factory
    assert callable(factory)
    strict = factory(rate_limit_auth_per_minute=1, rate_limit_global_per_minute=100)
    try:
        for _ in range(5):
            response = await strict.get(ENDPOINT)
            assert response.status_code == 200
    finally:
        await strict.aclose()
