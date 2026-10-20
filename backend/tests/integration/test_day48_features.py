"""Day 48 integration: kill switches + drift monitoring (no restart)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _auth_headers(client: AsyncClient) -> dict[str, str]:
    resp = await client.post("/api/v1/auth/guest")
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": "terms", "version": "2026-10-01", "granted": True},
                {"kind": "privacy", "version": "2026-10-01", "granted": True},
                {"kind": "ai_disclosure", "version": "2026-10-01", "granted": True},
            ]
        },
    )
    return headers


@pytest.mark.asyncio
async def test_feature_flags_list_and_toggle_without_restart(client: AsyncClient):
    headers = await _auth_headers(client)
    # list
    r = await client.get("/api/v1/feature-flags", headers=headers)
    assert r.status_code == 200
    flags = r.json()["flags"]
    assert "voice" in flags
    assert "agent" in flags
    assert "forecasting" in flags
    assert "bandit" in flags
    assert "private_mode_models" in flags
    assert "xr" in flags
    assert "safety_ml" in flags
    assert "emotion" in flags
    assert "retrieval" in flags

    # flip voice off and prove it stays off without restart
    r = await client.post("/api/v1/feature-flags/voice", headers=headers, json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert "fallback" in r.json()

    r = await client.get("/api/v1/feature-flags", headers=headers)
    assert r.json()["flags"]["voice"]["enabled"] is False

    # reset
    r = await client.post("/api/v1/feature-flags/voice/reset", headers=headers)
    assert r.status_code == 200
    assert r.json()["enabled"] is True


@pytest.mark.asyncio
async def test_each_kill_switch_has_safe_fallback_description(client: AsyncClient):
    headers = await _auth_headers(client)
    r = await client.get("/api/v1/feature-flags", headers=headers)
    flags = r.json()["flags"]
    for name in ["voice", "agent", "forecasting", "bandit", "private_mode_models", "xr", "meditation", "biofeedback", "safety_ml", "emotion", "retrieval"]:  # noqa: E501
        assert name in flags, f"missing flag {name}"
        assert flags[name]["fallback"], f"{name} has empty fallback"
        assert flags[name]["enabled"] is True  # defaults are enabled

    # Now toggle each off/on to prove no restart needed
    for name in ["voice", "agent", "forecasting", "bandit", "private_mode_models", "xr"]:
        off = await client.post(f"/api/v1/feature-flags/{name}", headers=headers, json={"enabled": False})  # noqa: E501
        assert off.status_code == 200
        assert off.json()["enabled"] is False
        # verify via list
        listed = await client.get("/api/v1/feature-flags", headers=headers)
        assert listed.json()["flags"][name]["enabled"] is False
        # turn back on
        on = await client.post(f"/api/v1/feature-flags/{name}", headers=headers, json={"enabled": True})  # noqa: E501
        assert on.json()["enabled"] is True


@pytest.mark.asyncio
async def test_drift_monitor_records_and_fires_alerts(client: AsyncClient):
    headers = await _auth_headers(client)
    # baseline window
    for _ in range(50):
        await client.post(
            "/api/v1/drift/record",
            headers=headers,
            json={"window": "baseline-w1", "text_length": 40, "language": "en", "confidence": 0.85, "safety_level": "none"},  # noqa: E501
        )
    # current window shifted: longer, more hi
    for _ in range(50):
        await client.post(
            "/api/v1/drift/record",
            headers=headers,
            json={"window": "current-w2", "text_length": 320, "language": "hi", "confidence": 0.45, "safety_level": "medium", "abstained": True},  # noqa: E501
        )
    # alerts should fire
    r = await client.get("/api/v1/drift/alerts", headers=headers, params={"window": "current-w2", "baseline": "baseline-w1"})  # noqa: E501
    assert r.status_code == 200
    data = r.json()
    assert data["count"] >= 1
    # at least one metric should be language_mix or input_length
    metrics = [a["metric"] for a in data["alerts"]]
    assert any(m in metrics for m in ["input_length", "language_mix", "confidence", "abstention_rate"])  # noqa: E501

    # weekly report contains both snapshots
    r2 = await client.get("/api/v1/drift/report", headers=headers, params={"window": "current-w2", "baseline": "baseline-w1"})  # noqa: E501
    assert r2.status_code == 200
    rep = r2.json()
    assert rep["baseline_window"] == "baseline-w1"
    assert rep["current_window"] == "current-w2"
    assert rep["baseline"] is not None
    assert rep["current"] is not None
    assert "alerts" in rep


@pytest.mark.asyncio
async def test_drift_snapshot_privacy_no_text(client: AsyncClient):
    headers = await _auth_headers(client)
    await client.post(
        "/api/v1/drift/record",
        headers=headers,
        json={"window": "test-w", "text_length": 123, "language": "en", "confidence": 0.9, "safety_level": "none", "embedding": [0.1, 0.2, 0.3, 0.4]},  # noqa: E501
    )
    r = await client.get("/api/v1/drift/snapshot/test-w", headers=headers)
    snap = r.json()["snapshot"]
    # must not contain text or user ids
    assert "text" not in snap
    assert "user_id" not in snap
    assert "embedding_raw" not in snap
    assert snap["embedding_centroid"] is not None
    assert snap["total_inputs"] == 1


@pytest.mark.asyncio
async def test_kill_switch_unknown_rejected(client: AsyncClient):
    headers = await _auth_headers(client)
    r = await client.post("/api/v1/feature-flags/unknown_flag", headers=headers, json={"enabled": False})  # noqa: E501
    assert r.status_code in (400, 404, 422, 500) or "unknown" in r.text.lower()
