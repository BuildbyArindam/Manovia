"""Unit tests for the error envelope and its handlers."""

import json

import structlog
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app.core.errors import (
    error_response,
    get_request_id,
    http_exception_handler,
    unhandled_exception_handler,
)


def _request_with_id(request_id: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/health",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "state": {"request_id": request_id},
    }
    return Request(scope)


def _bare_request() -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_error_response_uses_the_standard_envelope() -> None:
    response = error_response(
        status_code=404, code="not_found", message="Not found", request_id="req-1"
    )
    assert response.status_code == 404
    assert response.headers["x-request-id"] == "req-1"
    assert json.loads(bytes(response.body)) == {
        "error": {"code": "not_found", "message": "Not found", "request_id": "req-1"}
    }


async def test_http_exception_handler_maps_404_to_envelope() -> None:
    request = _request_with_id("req-404")
    response = await http_exception_handler(request, StarletteHTTPException(status_code=404))
    assert response.status_code == 404
    body = json.loads(bytes(response.body))
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "Not found"
    assert body["error"]["request_id"] == "req-404"


async def test_http_exception_handler_treats_unexpected_errors_as_internal() -> None:
    request = _request_with_id("req-weird")
    response = await http_exception_handler(request, ValueError("not an http error"))
    assert response.status_code == 500
    body = json.loads(bytes(response.body))
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["request_id"] == "req-weird"


async def test_unhandled_exception_handler_never_leaks_details() -> None:
    request = _request_with_id("req-500")
    response = await unhandled_exception_handler(request, RuntimeError("internal detail"))
    assert response.status_code == 500
    body = json.loads(bytes(response.body))
    assert body == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "request_id": "req-500",
        }
    }


def test_get_request_id_prefers_request_state() -> None:
    assert get_request_id(_request_with_id("req-state")) == "req-state"


def test_get_request_id_falls_back_to_contextvars() -> None:
    structlog.contextvars.bind_contextvars(request_id="ctx-req-7")
    try:
        assert get_request_id(_bare_request()) == "ctx-req-7"
    finally:
        structlog.contextvars.clear_contextvars()


def test_get_request_id_returns_unknown_without_any_id() -> None:
    assert get_request_id(_bare_request()) == "unknown"
