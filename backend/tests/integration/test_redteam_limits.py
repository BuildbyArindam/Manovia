"""Day 29 red team: limiter memory, health under flood, export budget.

RT-D-15, RT-D-14, RT-P-EX-02.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.core.config import Settings
from app.core.ratelimit import InMemoryRateLimiter


def test_rt_d_15_stale_keys_are_swept_after_a_window() -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(5, window_seconds=60, clock=lambda: now[0])
    for i in range(5000):
        limiter.hit(f"ip-{i}")
    assert len(limiter) == 5000
    now[0] += 3600
    limiter.hit("fresh")
    assert len(limiter) == 1


def test_the_sweep_keeps_keys_still_inside_the_window() -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(2, window_seconds=60, clock=lambda: now[0])
    limiter.hit("old")
    now[0] = 50.0
    limiter.hit("recent")
    limiter.hit("recent")
    now[0] = 70.0  # a sweep runs: "old" (t=0) is stale, "recent" (t=50) is not
    assert not limiter.hit("recent").allowed
    assert len(limiter) == 1


def test_privacy_export_per_hour_must_be_positive() -> None:
    with pytest.raises(ValueError, match="privacy_export_per_hour"):
        Settings(privacy_export_per_hour=0)


Factory = Callable[..., httpx.AsyncClient]


async def test_rt_d_14_health_answers_while_the_global_limit_is_exhausted(
    client_factory: Factory,
) -> None:
    client = client_factory(rate_limit_global_per_minute=2)
    statuses = [(await client.get("/api/v1/ready")).status_code for _ in range(4)]
    assert 429 in statuses
    for _ in range(5):
        assert (await client.get("/api/v1/health")).status_code == 200


async def test_rt_p_ex_02_exports_are_limited_per_user(client_factory: Factory) -> None:
    client = client_factory(privacy_export_per_hour=2)
    guest = (await client.post("/api/v1/auth/guest")).json()
    other = (await client.post("/api/v1/auth/guest")).json()
    headers = {"Authorization": f"Bearer {guest['access_token']}"}
    url = "/api/v1/privacy/export?format=json"
    codes = [(await client.get(url, headers=headers)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    limited = await client.get(url, headers=headers)
    assert limited.json()["error"]["code"] == "export_rate_limited"
    assert int(limited.headers["Retry-After"]) > 0
    # Another person's budget is untouched.
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    assert (await client.get(url, headers=other_headers)).status_code == 200


async def test_the_export_budget_is_off_when_rate_limiting_is_off(
    client_factory: Factory,
) -> None:
    client = client_factory(privacy_export_per_hour=1, rate_limit_enabled=False)
    guest = (await client.post("/api/v1/auth/guest")).json()
    headers = {"Authorization": f"Bearer {guest['access_token']}"}
    for _ in range(3):
        response = await client.get("/api/v1/privacy/export?format=json", headers=headers)
        assert response.status_code == 200
