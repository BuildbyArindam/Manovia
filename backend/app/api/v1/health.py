"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import error_response, get_request_id

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe: the process is up and serving requests."""
    return {"status": "ok"}


@router.get("/ready")
def readiness(request: Request) -> JSONResponse:
    """Readiness probe: the application configuration loads successfully.

    The database check is added in a later milestone.
    """
    try:
        settings = Settings()
    except ValidationError:
        return error_response(
            status_code=503,
            code="not_ready",
            message="Application configuration is not valid",
            request_id=get_request_id(request),
        )
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "checks": {"config": "ok"}, "app_env": settings.app_env},
    )
