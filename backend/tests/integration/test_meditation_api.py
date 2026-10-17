"""Day 45: the meditation surface over the real app.

Real API, real SQLite: themes (public, like the exercise list), script
composition, local audio render with the content-hash cache headers,
captions, and the tamper check (a script whose transcript no longer
matches its hash is a 422, never a render).
"""

from __future__ import annotations

import httpx
import pytest

ROOT = "/api/v1/meditation"

_SCRIPT_PAYLOAD = {
    "theme": "calm",
    "duration_seconds": 120,
    "language": "en",
    "context": "seated",
    "hour_of_day": 12,
    "seed": 5,
    "eyes_open": True,
}


async def guest(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    requirements = (await client.get("/api/v1/consent/requirements")).json()["documents"]
    grants = [
        {"kind": item["kind"], "version": item["version"], "granted": True}
        for item in requirements
        if item["kind"] in ("terms", "privacy")
    ]
    assert (
        await client.post("/api/v1/consent", headers=headers, json={"grants": grants})
    ).status_code == 201
    return headers


async def test_themes_are_public_and_the_reviewed_catalogue(client: httpx.AsyncClient) -> None:
    response = await client.get(ROOT + "/themes")
    assert response.status_code == 200
    body = response.json()
    assert [t["id"] for t in body["themes"]] == [
        "morning",
        "focus",
        "stress-release",
        "calm",
        "sleep",
        "gratitude",
    ]
    for theme in body["themes"]:
        assert theme["label"] and theme["hint"] and theme["best_time"]
    assert "quiet" in body["preference_tags"]
    assert "gentle" not in body["preference_tags"]


async def test_script_endpoint_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post(ROOT + "/scripts", json=_SCRIPT_PAYLOAD)
    assert response.status_code == 401


async def test_script_composition_endpoint(client: httpx.AsyncClient) -> None:
    headers = await guest(client)
    response = await client.post(
        ROOT + "/scripts",
        json={**_SCRIPT_PAYLOAD, "duration_seconds": 180, "hour_of_day": 21},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    script = response.json()
    assert script["resolved_theme"] == "calm"
    assert script["content_hash"]
    assert script["word_count"] == len(script["transcript"].split())
    assert script["safety"]["safety_preface"] is True
    assert script["safety"]["stop_cue"] is True
    roles = [s["role"] for s in script["sections"]]
    assert roles[0] == "preface"
    assert roles[-1] == "closing"
    for section in script["sections"]:
        assert section["end_s"] > section["start_s"]
        assert section["pause_before_ms"] >= 0


async def test_script_endpoint_rejects_a_closed_eye_request_while_walking(
    client: httpx.AsyncClient,
) -> None:
    headers = await guest(client)
    response = await client.post(
        ROOT + "/scripts",
        json={
            "theme": "calm",
            "duration_seconds": 180,
            "language": "en",
            "context": "walking",
            "seed": 5,
            "eyes_open": False,
        },
        headers=headers,
    )
    assert response.status_code == 422


async def test_script_endpoint_rejects_an_unknown_theme(client: httpx.AsyncClient) -> None:
    headers = await guest(client)
    response = await client.post(
        ROOT + "/scripts",
        json={"theme": "relax", "duration_seconds": 180, "seed": 5},
        headers=headers,
    )
    assert response.status_code == 422


async def test_script_endpoint_rejects_extra_fields(client: httpx.AsyncClient) -> None:
    headers = await guest(client)
    response = await client.post(
        ROOT + "/scripts",
        json={"theme": "calm", "mood": "tired", "seed": 5},
        headers=headers,
    )
    assert response.status_code == 422


async def test_audio_render_and_cache_headers(client: httpx.AsyncClient) -> None:
    headers = await guest(client)
    created = await client.post(ROOT + "/scripts", json=_SCRIPT_PAYLOAD, headers=headers)
    assert created.status_code == 200, created.text
    script = created.json()

    first = await client.post(ROOT + "/audio", json={"script": script}, headers=headers)
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "audio/wav"
    assert first.content[:4] == b"RIFF"
    assert first.headers["x-content-sha256"]
    assert first.headers["x-render-cache"] == "miss"
    assert first.headers["x-render-provider"] in {"fake", "piper"}

    second = await client.post(ROOT + "/audio", json={"script": script}, headers=headers)
    assert second.status_code == 200
    assert second.headers["x-render-cache"] == "hit"
    assert second.content == first.content


async def test_audio_rejects_a_tampered_transcript(client: httpx.AsyncClient) -> None:
    headers = await guest(client)
    created = await client.post(
        ROOT + "/scripts", json={**_SCRIPT_PAYLOAD, "seed": 7}, headers=headers
    )
    script = created.json()
    script["sections"][3]["text"] = "Something entirely different was spoken here."
    script["transcript"] = "Something entirely different was spoken here."
    response = await client.post(ROOT + "/audio", json={"script": script}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "script_hash_mismatch"


async def test_captions_match_the_script_sections(client: httpx.AsyncClient) -> None:
    headers = await guest(client)
    created = await client.post(
        ROOT + "/scripts", json={**_SCRIPT_PAYLOAD, "seed": 6}, headers=headers
    )
    script = created.json()
    response = await client.post(ROOT + "/audio/captions", json={"script": script}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timing_basis"] == "estimated"
    assert body["transcript"] == script["transcript"]
    assert [c["section_id"] for c in body["captions"]] == [s["id"] for s in script["sections"]]
    for caption, section in zip(body["captions"], script["sections"], strict=True):
        assert caption["start_s"] == section["start_s"]
        assert caption["end_s"] == section["end_s"]
        assert caption["text"] == section["text"]


async def test_composition_is_deterministic_over_http(client: httpx.AsyncClient) -> None:
    headers = await guest(client)
    payload = {
        "theme": "auto",
        "duration_seconds": 180,
        "language": "en",
        "context": "unknown",
        "hour_of_day": 21,
        "seed": 11,
        "eyes_open": True,
    }
    first = await client.post(ROOT + "/scripts", json=payload, headers=headers)
    second = await client.post(ROOT + "/scripts", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json()["content_hash"] == second.json()["content_hash"]


async def test_driving_request_carries_the_safety_frame(client: httpx.AsyncClient) -> None:
    headers = await guest(client)
    response = await client.post(
        ROOT + "/scripts",
        json={**_SCRIPT_PAYLOAD, "context": "driving"},
        headers=headers,
    )
    assert response.status_code == 200
    script = response.json()
    assert script["safety"]["driving_warning"] is True
    assert script["safety"]["eyes_open"] is True
    assert "driving" in script["transcript"].lower()
    assert "you can stop at any time" in script["transcript"].lower()


async def test_auto_theme_resolution_over_http(client: httpx.AsyncClient) -> None:
    headers = await guest(client)
    response = await client.post(
        ROOT + "/scripts",
        json={
            "theme": "auto",
            "duration_seconds": 180,
            "language": "en",
            "context": "seated",
            "hour_of_day": 23,
            "seed": 1,
            "eyes_open": False,
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["resolved_theme"] in {"sleep", "gratitude"}
    assert body["theme"] == "auto"
    assert body["safety"]["eyes_open"] is False
    assert body["safety"]["eyes_closed_allowed"] is True


async def test_catalog_unavailable_is_a_503_not_a_500(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken catalogue is an honest 503 on the public endpoint."""
    import app.api.v1.meditation as api
    from app.services.meditation.content import ContentError

    def broken() -> tuple:
        raise ContentError("meditation content file missing: themes.yaml")

    monkeypatch.setattr(api, "load_themes", broken)
    response = await client.get(ROOT + "/themes")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "meditation_catalog_unavailable"
