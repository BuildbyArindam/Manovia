"""Planner preferences: defaults, round-trip, and strict validation."""

from __future__ import annotations

import uuid

import httpx

ROOT = "/api/v1/settings"


async def guest(
    client: httpx.AsyncClient, *, consent: bool = True
) -> tuple[uuid.UUID, dict[str, str]]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    if consent:
        requirements = (await client.get("/api/v1/consent/requirements")).json()["documents"]
        grants = [
            {"kind": item["kind"], "version": item["version"], "granted": True}
            for item in requirements
            if item["kind"] in ("terms", "privacy")
        ]
        assert (
            await client.post("/api/v1/consent", headers=headers, json={"grants": grants})
        ).status_code == 201
    return uuid.UUID(body["user"]["id"]), headers


async def test_settings_require_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get(ROOT)).status_code == 401
    assert (await client.put(ROOT, json={})).status_code == 401


async def test_settings_require_consent(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client, consent=False)
    assert (await client.get(ROOT, headers=headers)).status_code == 403
    assert (await client.put(ROOT, headers=headers, json={})).status_code == 403


async def test_get_returns_defaults_and_the_tag_vocabulary(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    response = await client.get(ROOT, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["preferred_exercise_tags"] == []
    assert body["quiet_hours_enabled"] is False
    assert body["quiet_start"] == "22:00"
    assert body["quiet_end"] == "07:00"
    assert "breathing" in body["known_exercise_tags"]
    assert "grounding" in body["known_exercise_tags"]


async def test_put_round_trips(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    payload = {
        "preferred_exercise_tags": ["breathing", "mindfulness"],
        "quiet_hours_enabled": True,
        "quiet_start": "21:30",
        "quiet_end": "08:15",
    }
    response = await client.put(ROOT, headers=headers, json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["preferred_exercise_tags"] == ["breathing", "mindfulness"]
    assert body["quiet_hours_enabled"] is True
    assert body["quiet_start"] == "21:30"
    assert body["quiet_end"] == "08:15"

    again = (await client.get(ROOT, headers=headers)).json()
    assert again["preferred_exercise_tags"] == ["breathing", "mindfulness"]
    assert again["quiet_hours_enabled"] is True


async def test_put_dedupes_and_casefolds_tags(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    response = await client.put(
        ROOT,
        headers=headers,
        json={"preferred_exercise_tags": ["Breathing", " BREATHING ", "worry"]},
    )
    assert response.status_code == 200
    assert response.json()["preferred_exercise_tags"] == ["breathing", "worry"]


async def test_unknown_tags_are_rejected(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    response = await client.put(
        ROOT, headers=headers, json={"preferred_exercise_tags": ["levitation"]}
    )
    assert response.status_code == 422


async def test_bad_quiet_bounds_are_rejected(client: httpx.AsyncClient) -> None:
    _, headers = await guest(client)
    for bad_start, bad_end in [("25:00", "07:00"), ("22:00", "7:00"), ("bedtime", "07:00")]:
        response = await client.put(
            ROOT,
            headers=headers,
            json={"quiet_hours_enabled": True, "quiet_start": bad_start, "quiet_end": bad_end},
        )
        assert response.status_code == 422, (bad_start, bad_end)


async def test_put_is_scoped_to_the_caller(client: httpx.AsyncClient) -> None:
    _, headers_a = await guest(client)
    _, headers_b = await guest(client)
    await client.put(ROOT, headers=headers_a, json={"preferred_exercise_tags": ["worry"]})
    body_b = (await client.get(ROOT, headers=headers_b)).json()
    assert body_b["preferred_exercise_tags"] == []
