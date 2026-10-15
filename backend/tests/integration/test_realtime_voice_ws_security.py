"""Rejection boundaries for the Day 40 authenticated voice WebSocket."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient
from starlette.websockets import WebSocketDisconnect

from tests.integration.test_realtime_voice_ws import auth_headers


def close_code(app: FastAPI, path: str, *, subprotocols: list[str] | None = None) -> int:
    """Open until the route rejects the handshake, then return its close code."""
    with (
        TestClient(app) as test_client,
        pytest.raises(WebSocketDisconnect) as closed,
        test_client.websocket_connect(path, subprotocols=subprotocols),
    ):
        pass
    return closed.value.code


async def test_realtime_socket_rejects_missing_or_url_only_credentials(app: FastAPI) -> None:
    session_id = "00000000-0000-0000-0000-000000000000"

    assert close_code(app, f"/api/v1/voice/sessions/{session_id}/stream") == 4401
    assert (
        close_code(
            app,
            f"/api/v1/voice/sessions/{session_id}/stream?access_token=not-accepted",
            subprotocols=["manovia.v1"],
        )
        == 4401
    )


async def test_realtime_socket_rejects_invalid_session_before_auth(app: FastAPI) -> None:
    assert close_code(app, "/api/v1/voice/sessions/not-a-uuid/stream") == 4400


async def test_realtime_socket_hides_another_users_session(
    app: FastAPI, client: AsyncClient
) -> None:
    owner_headers = await auth_headers(client)
    session = await client.post("/api/v1/chat/sessions", headers=owner_headers)
    assert session.status_code == 201
    session_id = session.json()["id"]

    other_headers = await auth_headers(client)
    other_token = other_headers["Authorization"].removeprefix("Bearer ")
    assert (
        close_code(
            app,
            f"/api/v1/voice/sessions/{session_id}/stream",
            subprotocols=["manovia.v1", f"bearer.{other_token}"],
        )
        == 4404
    )
