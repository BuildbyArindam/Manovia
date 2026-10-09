"""Manovia API application factory."""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.consent import router as consent_router
from app.api.v1.crisis import router as crisis_router
from app.api.v1.health import router as health_router
from app.core.config import Settings, get_settings
from app.core.crypto import FernetCipher, configure_cipher
from app.core.errors import (
    ApiError,
    api_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.lockout import LoginLockout
from app.core.logging import RequestIDMiddleware, configure_logging
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.core.ratelimit import InMemoryRateLimiter
from app.core.tokens import TokenService
from app.db.session import Database, build_database

API_TITLE = "Manovia API"
API_VERSION = "0.1.0"


def create_app(settings: Settings | None = None, *, database: Database | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    ``database`` can be injected (tests, scripts); by default one is built from
    ``settings.database_url``.
    """
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)

    # Field encryption is process-wide configuration, decided once at startup.
    # Without a key the data layer refuses to write plaintext (see
    # app.core.crypto), and every *_encrypted read fails loudly.
    configure_cipher(FernetCipher.from_settings(app_settings))
    if app_settings.field_encryption_key is None:
        structlog.get_logger().warning("field_encryption_key_missing")

    # JWT signing. SECRET_KEY is required in production (Settings enforces it);
    # without one in development/tests an ephemeral key is generated so a
    # restart invalidates sessions instead of the process refusing to start.
    token_secret = app_settings.secret_key
    if token_secret is None:
        structlog.get_logger().warning("secret_key_missing_ephemeral_token_signing")
        token_secret = secrets.token_urlsafe(64)

    @asynccontextmanager
    async def lifespan(created: FastAPI) -> AsyncIterator[None]:
        db: Database = created.state.db
        yield
        await db.dispose()

    app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=lifespan)

    app.state.settings = app_settings
    app.state.db = database or build_database(app_settings)
    app.state.token_service = TokenService(
        token_secret,
        access_ttl=timedelta(minutes=app_settings.jwt_access_minutes),
        refresh_ttl=timedelta(days=app_settings.jwt_refresh_days),
    )
    app.state.login_lockout = LoginLockout(
        max_failures=app_settings.login_max_failures,
        lock_seconds=app_settings.login_lockout_seconds,
        max_lock_seconds=app_settings.login_lockout_max_seconds,
    )
    app.state.rate_limit_auth = InMemoryRateLimiter(app_settings.rate_limit_auth_per_minute)
    app.state.rate_limit_global = InMemoryRateLimiter(app_settings.rate_limit_global_per_minute)

    # Middleware added last runs first (outermost), so the request ID wraps
    # every response, including error responses. Rate limiting sits just inside
    # the security headers so even a 429 is fully dressed; it is skipped when
    # disabled (load tests only).
    app.add_middleware(
        RateLimitMiddleware,
        auth_limiter=app.state.rate_limit_auth,
        global_limiter=app.state.rate_limit_global,
        enabled=app_settings.rate_limit_enabled,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(consent_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(crisis_router, prefix="/api/v1")

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    return app


app = create_app()
