"""Integration tests for request ID, security headers, and CORS middleware."""

import httpx


async def test_security_headers_are_set_on_responses(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"


async def test_every_response_carries_a_unique_request_id(client: httpx.AsyncClient) -> None:
    first = await client.get("/api/v1/health")
    second = await client.get("/api/v1/health")
    assert first.headers["x-request-id"]
    assert second.headers["x-request-id"]
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


async def test_incoming_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "client-supplied-id"})
    assert response.headers["x-request-id"] == "client-supplied-id"


async def test_cors_allows_configured_origins(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"Origin": "http://testserver"})
    assert response.headers["access-control-allow-origin"] == "http://testserver"


async def test_cors_rejects_unknown_origins(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


async def test_cors_preflight_is_answered(client: httpx.AsyncClient) -> None:
    response = await client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://testserver",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://testserver"
