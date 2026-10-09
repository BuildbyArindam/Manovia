"""Manovia API application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.health import router as health_router
from app.core.config import Settings, get_settings
from app.core.crypto import FernetCipher, configure_cipher
from app.core.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.core.logging import RequestIDMiddleware, configure_logging
from app.core.middleware import SecurityHeadersMiddleware
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

    @asynccontextmanager
    async def lifespan(created: FastAPI) -> AsyncIterator[None]:
        db: Database = created.state.db
        yield
        await db.dispose()

    app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=lifespan)

    app.state.settings = app_settings
    app.state.db = database or build_database(app_settings)

    # Middleware added last runs first (outermost), so the request ID wraps
    # every response, including error responses.
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

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    return app


app = create_app()
