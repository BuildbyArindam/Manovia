"""Tests for the read-only directory MCP server.

Auth, rate limiting, the tool allow-list, and the
needs_verification exclusion — the four contracts
a client (or an attacker) can rely on.

Run with the backend virtualenv, from the
``mcp-server/`` directory::

    ../.venv/bin/python -m pytest test_server.py -q
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server import (
    ERROR_READ_ONLY,
    ERROR_UNAUTHORIZED,
    _configure,
    app,
)

CLIENT_KEY = "clinic-key-1"


@pytest.fixture()
def client() -> TestClient:
    """A configured server with one client key."""
    _configure(None, (CLIENT_KEY,), rate=1_000)
    with TestClient(app) as test_client:
        yield test_client


def _call(
    test_client: TestClient,
    method: str,
    *,
    token: str | None = CLIENT_KEY,
    params: dict[str, Any] | None = None,
    id_: int = 1,
) -> Any:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": id_,
        "method": method,
    }
    if params is not None:
        payload["params"] = params
    response = test_client.post(
        "/mcp", json=payload, headers=headers
    )
    return response


def test_initialize_reports_a_read_only_tools_server(
    client: TestClient,
) -> None:
    response = _call(client, "initialize")
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["serverInfo"]["name"] == (
        "manovia-support-directory"
    )
    assert "tools" in body["result"]["capabilities"]


def test_tools_list_is_exactly_the_two_read_tools(
    client: TestClient,
) -> None:
    response = _call(client, "tools/list")
    assert response.status_code == 200
    names = {
        tool["name"]
        for tool in response.json()["result"]["tools"]
    }
    assert names == {"search_directory", "list_regions"}


def test_search_returns_verified_entries_only(
    client: TestClient,
) -> None:
    response = _call(
        client,
        "tools/call",
        params={
            "name": "search_directory",
            "arguments": {
                "region": "IN-WB",
                "language": "hi",
                "budget": "low_cost",
            },
        },
        id_=3,
    )
    assert response.status_code == 200
    body = response.json()["result"]
    assert body["count"] >= 2
    for entry in body["results"]:
        # Unverified entries never leave the server.
        assert not entry["id"].startswith(
            "unverified-"
        )
        # Every served entry carries provenance.
        assert entry["source_url"]
        assert entry["last_verified"]


def test_list_regions_counts_and_exclusions(
    client: TestClient,
) -> None:
    response = _call(client, "tools/call", params={
        "name": "list_regions", "arguments": {},
    }, id_=4)
    assert response.status_code == 200
    result = response.json()["result"]
    assert "IN" in result["regions"]
    assert result["excluded_needs_verification"] >= 1


def test_missing_token_is_rejected(client: TestClient) -> None:
    response = _call(client, "tools/list", token=None)
    assert response.status_code == 401
    assert (
        response.json()["error"]["code"]
        == ERROR_UNAUTHORIZED
    )


def test_wrong_token_is_rejected(client: TestClient) -> None:
    response = _call(client, "tools/list", token="nope")
    assert response.status_code == 401


def test_an_unknown_tool_is_refused(client: TestClient) -> None:
    response = _call(
        client,
        "tools/call",
        params={
            "name": "send_email",
            "arguments": {"to": "x@example.com"},
        },
        id_=5,
    )
    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == ERROR_READ_ONLY


def test_an_unknown_method_is_refused(
    client: TestClient,
) -> None:
    response = _call(client, "resources/list", id_=6)
    error = response.json()["error"]
    assert error["code"] == -32601


def test_budget_must_be_from_the_closed_set(
    client: TestClient,
) -> None:
    response = _call(
        client,
        "tools/call",
        params={
            "name": "search_directory",
            "arguments": {"budget": "expensive"},
        },
        id_=7,
    )
    error = response.json()["error"]
    assert error["code"] == -32602


def test_the_rate_limiter_blocks_a_hot_key() -> None:
    _configure(None, (CLIENT_KEY,), rate=2)
    with TestClient(app) as test_client:
        first = _call(test_client, "ping")
        second = _call(test_client, "ping")
        third = _call(test_client, "ping")
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert "Retry-After" in third.headers
    assert third.json()["error"]["code"] == -32002


def test_the_directory_file_validates_against_the_schema() -> None:
    backend = (
        Path(__file__).resolve().parent.parent
        / "backend"
        / "app"
        / "content"
        / "resources"
    )
    payload = json.loads(
        (backend / "directory.json").read_text(
            encoding="utf-8"
        )
    )
    schema = json.loads(
        (backend / "directory.schema.json").read_text(
            encoding="utf-8"
        )
    )
    from jsonschema import Draft202012Validator

    errors = list(
        Draft202012Validator(schema).iter_errors(payload)
    )
    assert errors == []
