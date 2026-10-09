"""Consistent JSON error envelope: {"error": {"code", "message", "request_id"}}."""

from collections.abc import Mapping

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


class ApiError(Exception):
    """A curated, machine-readable failure raised by endpoints and dependencies.

    Unlike ``HTTPException``, the code and message survive all the way into the
    error envelope (``http_exception_handler`` maps plain HTTP exceptions to
    generic per-status codes). Messages must be curated strings — never echo
    exception details or user input.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = dict(headers) if headers is not None else None


def get_request_id(request: Request) -> str:
    """Return the request ID assigned by RequestIDMiddleware, if any."""
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        return str(request_id)
    bound = structlog.contextvars.get_contextvars().get("request_id")
    return str(bound) if bound else "unknown"


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build an error response in the standard envelope.

    The request ID is also set as a response header so it is present even on
    the unhandled-exception path, which bypasses the middleware stack.
    ``headers`` adds curated extras (e.g. ``Retry-After`` on a 429).
    """
    body = ErrorBody(code=code, message=message, request_id=request_id)
    response = JSONResponse(status_code=status_code, content={"error": body.model_dump()})
    response.headers[REQUEST_ID_HEADER] = request_id
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


_HTTP_ERROR_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "rate_limited",
}

# Curated messages only: exception details can contain user-controlled text
# and are never echoed back to clients or written to logs.
_HTTP_ERROR_MESSAGES: dict[int, str] = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    429: "Too many requests",
}


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render :class:`ApiError` in the standard envelope, with its own code."""
    if not isinstance(exc, ApiError):
        # Defensive: this handler is registered for ApiError only.
        return error_response(
            status_code=500,
            code="internal_error",
            message="Internal server error",
            request_id=get_request_id(request),
        )
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=get_request_id(request),
        headers=exc.headers,
    )


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
