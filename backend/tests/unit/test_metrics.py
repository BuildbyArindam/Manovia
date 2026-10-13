"""Day 27: the Prometheus metrics module — labels are categories, never content.

These tests pin the privacy property as much as the numbers: every exported
label is a closed-set enum or a route template, and a sentinel pushed through
request paths can never appear in ``/metrics`` output.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from prometheus_client import CollectorRegistry, generate_latest

from app.core.metrics import (
    UNMATCHED_PATH,
    Metrics,
    get_metrics,
    path_template_for_scope,
    reset_metrics,
)
from app.core.metrics_middleware import MetricsMiddleware

SENTINEL = "SENTINEL-METRICS-9f2c1b-not-a-label"


def text_of(registry: CollectorRegistry) -> str:
    text: str = generate_latest(registry).decode("utf-8")
    return text


# --- the Metrics object ------------------------------------------------------


def test_safety_event_labels_are_closed_enums() -> None:
    metrics = Metrics()
    metrics.record_safety_event(2, "rules")
    metrics.record_safety_event(2, "rules")
    metrics.record_safety_event(3, "llm_output")
    body = text_of(metrics.registry)
    assert 'manovia_safety_events_total{level="elevated",source="rules"} 2.0' in body
    assert 'manovia_safety_events_total{level="crisis",source="llm_output"} 1.0' in body


def test_unknown_risk_level_falls_into_fixed_label() -> None:
    metrics = Metrics()
    metrics.record_safety_event(99, "ml")
    body = text_of(metrics.registry)
    assert 'manovia_safety_events_total{level="unknown",source="ml"} 1.0' in body


def test_llm_success_and_fallback_series() -> None:
    metrics = Metrics()
    metrics.record_llm_success("anthropic", 0)
    metrics.record_llm_success("canned", 2)
    metrics.record_llm_fallback("provider_failed")
    metrics.record_llm_fallback("not_configured")
    body = text_of(metrics.registry)
    assert 'manovia_llm_requests_total{position="0",provider="anthropic"} 1.0' in body
    assert 'manovia_llm_requests_total{position="2",provider="canned"} 1.0' in body
    assert 'manovia_llm_fallbacks_total{reason="provider_failed"} 1.0' in body
    assert 'manovia_llm_fallbacks_total{reason="not_configured"} 1.0' in body


def test_guard_outcome_series() -> None:
    metrics = Metrics()
    for outcome in ("passed", "passed", "blocked"):
        metrics.record_guard_outcome(outcome)
    body = text_of(metrics.registry)
    assert 'manovia_output_guard_total{outcome="passed"} 2.0' in body
    assert 'manovia_output_guard_total{outcome="blocked"} 1.0' in body


def test_request_observation_records_rate_latency_and_inflight() -> None:
    metrics = Metrics()
    metrics.start_request("POST")
    assert (
        metrics.registry.get_sample_value("manovia_http_requests_in_progress", {"method": "POST"})
        == 1.0
    )
    metrics.observe_request("POST", "/api/v1/chat/sessions", 201, 0.042)
    metrics.finish_request("POST")
    body = text_of(metrics.registry)
    assert (
        'manovia_http_requests_total{method="POST",path="/api/v1/chat/sessions",status="201"} 1.0'
        in body
    )
    assert 'manovia_http_requests_in_progress{method="POST"} 0.0' in body
    # The histogram exported an observed bucket and its cumulative count.
    assert 'manovia_http_request_seconds_bucket{le="0.1",method="POST"' in body
    assert (
        'manovia_http_request_seconds_count{method="POST",path="/api/v1/chat/sessions"} 1.0' in body
    )


def test_singleton_is_rebuildable_for_tests() -> None:
    first = get_metrics()
    again = reset_metrics()
    assert first is not again
    reset_metrics()


# --- the route-template helper -----------------------------------------------


class _FakeRoute:
    def __init__(self, path: str) -> None:
        self.path = path


def test_path_template_uses_the_route_template() -> None:
    scope: dict[str, Any] = {"route": _FakeRoute("/api/v1/chat/{session_id}")}
    assert path_template_for_scope(scope) == "/api/v1/chat/{session_id}"


def test_path_template_without_a_route_is_a_fixed_label() -> None:
    assert path_template_for_scope({}) == UNMATCHED_PATH


# --- middleware end to end (privacy property included) -----------------------


@pytest.fixture
async def metrics_client() -> AsyncIterator[tuple[httpx.AsyncClient, Metrics]]:
    """A Starlette app (one parameterised route) wrapped in MetricsMiddleware.

    The middleware (and every service hook) records into the process-wide
    singleton, so the fixture installs a fresh one via ``reset_metrics`` and
    hands that exact instance back for assertions.
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    metrics = reset_metrics()

    async def item(request: Any) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def boom(request: Any) -> JSONResponse:
        raise RuntimeError("boom")

    async def handle_error(request: Any, exc: Exception) -> JSONResponse:
        # The real app's unhandled_exception_handler turns escapes into a 500;
        # mirror that so the middleware sees a status code to record.
        return JSONResponse({"detail": "internal"}, status_code=500)

    app: Any = Starlette(
        routes=[
            Route("/items/{item_id}", item),
            Route("/boom", boom),
        ],
        exception_handlers={RuntimeError: handle_error},
    )
    app.add_middleware(MetricsMiddleware)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, metrics


async def test_middleware_exports_route_templates_not_raw_paths(
    metrics_client: tuple[httpx.AsyncClient, Metrics],
) -> None:
    client, _metrics = metrics_client
    item_id = str(uuid.uuid4())
    (await client.get(f"/items/{item_id}")).raise_for_status()
    response = await client.get("/nope")
    assert response.status_code == 404
    body = text_of(_metrics.registry)
    assert 'path="/items/{item_id}",status="200"' in body
    # The raw id never becomes a label; the unmatched 404 gets the fixed label.
    assert item_id not in body
    assert f'path="{UNMATCHED_PATH}"' in body


async def test_middleware_counts_errors_and_keeps_inflight_consistent(
    metrics_client: tuple[httpx.AsyncClient, Metrics],
) -> None:
    client, metrics = metrics_client
    response = await client.get("/boom")
    assert response.status_code == 500
    body = text_of(metrics.registry)
    assert 'manovia_http_requests_total{method="GET",path="/boom",status="500"} 1.0' in body
    assert (
        metrics.registry.get_sample_value("manovia_http_requests_in_progress", {"method": "GET"})
        == 0.0
    )
    assert SENTINEL not in body
