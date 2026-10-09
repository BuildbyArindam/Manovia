"""Integration tests for the API-wide error envelope."""

import httpx
from fastapi import FastAPI


async def test_unknown_route_returns_the_error_envelope(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"] == response.headers["x-request-id"]


async def test_validation_errors_return_the_envelope_without_echoing_input(
    app: FastAPI,
    client: httpx.AsyncClient,
) -> None:
    @app.get("/api/v1/typed")
    def typed_endpoint(q: int) -> dict[str, int]:
        return {"q": q}

    response = await client.get("/api/v1/typed", params={"q": "not-an-integer"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert "not-an-integer" not in body["error"]["message"]


async def test_unhandled_exceptions_return_a_generic_envelope(app: FastAPI) -> None:
    @app.get("/api/v1/boom")
    def boom() -> None:
        raise RuntimeError("sensitive internal detail")

    # The server error middleware sends the 500 response and then re-raises;
    # raise_app_exceptions=False lets us inspect the response the app sent.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/api/v1/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["message"] == "Internal server error"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert "sensitive internal detail" not in response.text
