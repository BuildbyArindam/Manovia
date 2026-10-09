"""Consistent JSON error envelope: {"error": {"code", "message", "request_id"}}."""

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import REQUEST_ID_HEADER


class ErrorBody(BaseModel):
    """The body of every error response."""

    code: str
    message: str
    request_id: str


def get_request_id(request: Request) -> str:
    """Return the request ID assigned by RequestIDMiddleware, if any."""
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        return str(request_id)
    bound = structlog.contextvars.get_contextvars().get("request_id")
    return str(bound) if bound else "unknown"


def error_response(*, status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    """Build an error response in the standard envelope.

    The request ID is also set as a response header so it is present even on
    the unhandled-exception path, which bypasses the middleware stack.
    """
    body = ErrorBody(code=code, message=message, request_id=request_id)
    response = JSONResponse(status_code=status_code, content={"error": body.model_dump()})
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


_HTTP_ERROR_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
}

# Curated messages only: exception details can contain user-controlled text
# and are never echoed back to clients or written to logs.
_HTTP_ERROR_MESSAGES: dict[int, str] = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
}


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Translate Starlette HTTP exceptions into the standard envelope."""
    if not isinstance(exc, StarletteHTTPException):
        # Defensive: this handler is registered for HTTPException only.
        return error_response(
            status_code=500,
            code="internal_error",
            message="Internal server error",
            request_id=get_request_id(request),
        )
    status_code = exc.status_code
    return error_response(
        status_code=status_code,
        code=_HTTP_ERROR_CODES.get(status_code, f"http_{status_code}"),
        message=_HTTP_ERROR_MESSAGES.get(status_code, "Request failed"),
        request_id=get_request_id(request),
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Translate request validation failures into the standard envelope.

    ``exc.errors()`` can echo raw user input, so it is deliberately excluded.
    """
    return error_response(
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        request_id=get_request_id(request),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: log the failure, return a generic envelope."""
    structlog.get_logger().exception("unhandled_exception", path=request.url.path)
    return error_response(
        status_code=500,
        code="internal_error",
        message="Internal server error",
        request_id=get_request_id(request),
    )
