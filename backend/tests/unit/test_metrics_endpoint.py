"""Day 27: the /metrics endpoint on the real app.

Asserts the endpoint serves the Prometheus exposition on the running app,
reflects real requests with route-template labels, and — the property that
matters most — never contains anything a person wrote.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.core.metrics import reset_metrics


@pytest.fixture
async def metrics_app(client: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    """The standard app client with a fresh metrics singleton installed."""
    reset_metrics()
    yield client
    reset_metrics()


async def test_metrics_endpoint_serves_prometheus_text(
    metrics_app: httpx.AsyncClient,
) -> None:
    response = await metrics_app.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    # The required series exist as descriptors even before traffic.
    assert "manovia_http_requests_total" in body
    assert "manovia_http_request_seconds" in body
    assert "manovia_safety_events_total" in body
    assert "manovia_llm_fallbacks_total" in body
    assert "manovia_output_guard_total" in body


async def test_metrics_reflects_real_requests_with_template_paths(
    metrics_app: httpx.AsyncClient,
) -> None:
    health = await metrics_app.get("/api/v1/health")
    assert health.status_code == 200
    body = (await metrics_app.get("/metrics")).text
    assert (
        'manovia_http_requests_total{method="GET",path="/api/v1/health",status="200"} 1.0' in body
    )


async def test_metrics_never_contains_user_text(
    metrics_app: httpx.AsyncClient,
) -> None:
    """A sentinel in a request body and in a 404 path both stay out of /metrics."""
    sentinel = "SENTINEL-METRICS-USER-TEXT-6b1f"
    # A request whose body carries the sentinel (422 validation failure is fine —
    # what matters is that the request happened).
    await metrics_app.post("/api/v1/auth/register", json={"email": sentinel, "password": "x" * 12})
    # A request whose *path* carries the sentinel.
    await metrics_app.get(f"/api/v1/nowhere/{sentinel}")
    body = (await metrics_app.get("/metrics")).text
    assert sentinel not in body
    # The unmatched paths collapsed into the fixed label.
    assert 'path=":unmatched"' in body


async def test_metrics_endpoint_can_be_disabled(
    client_factory: Callable[..., httpx.AsyncClient],
) -> None:
    """METRICS_ENABLED=false removes the route entirely (a plain 404)."""
    client = client_factory(metrics_enabled=False)
    assert (await client.get("/metrics")).status_code == 404
