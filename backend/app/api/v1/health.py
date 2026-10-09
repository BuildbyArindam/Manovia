"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import error_response, get_request_id
from app.db.session import check_database

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe: the process is up and serving requests."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(request: Request) -> JSONResponse:
    """Readiness probe: configuration loads and the database answers.

    Every check is reported in ``checks``; the probe fails closed with 503 when
    any of them fails, so an orchestrator never routes traffic to a process that
    cannot persist data. Only the outcome is exposed — never a DSN, a driver
    error, or any other detail that could describe the deployment.
    """
    request_id = get_request_id(request)
    try:
        settings = Settings()
    except ValidationError:
        return error_response(
            status_code=503,
            code="not_ready",
            message="Application configuration is not valid",
            request_id=request_id,
        )

    database = getattr(request.app.state, "db", None)
    if database is None or not await check_database(database):
        return error_response(
            status_code=503,
            code="not_ready",
            message="Database is not reachable",
            request_id=request_id,
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "checks": {"config": "ok", "database": "ok"},
            "app_env": settings.app_env,
        },
    )
