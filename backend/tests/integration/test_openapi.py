"""Integration tests for the auto-generated API schema and docs."""

import httpx


async def test_openapi_schema_is_served(client: httpx.AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Manovia API"
    assert "/api/v1/health" in schema["paths"]
    assert "/api/v1/ready" in schema["paths"]


async def test_docs_page_is_served(client: httpx.AsyncClient) -> None:
    response = await client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()
