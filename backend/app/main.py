"""Manovia API application factory."""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import timedelta

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.agent import router as agent_router
from app.api.v1.assessments import router as assessments_router
from app.api.v1.auth import router as auth_router
from app.api.v1.cbt import router as cbt_router
from app.api.v1.chat import router as chat_router
from app.api.v1.consent import router as consent_router
from app.api.v1.crisis import router as crisis_router
from app.api.v1.dev import router as dev_router
from app.api.v1.exercises import router as exercises_router
from app.api.v1.health import router as health_router
from app.api.v1.insights import router as insights_router
from app.api.v1.journal import router as journal_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.meditation import build_meditation_service
from app.api.v1.meditation import router as meditation_router
from app.api.v1.metrics import router as metrics_router
from app.api.v1.mood import router as mood_router
from app.api.v1.plan import router as plan_router
from app.api.v1.privacy import router as privacy_router
from app.api.v1.settings import router as settings_router
from app.api.v1.share_view import router as share_view_router
from app.api.v1.sharing import router as sharing_router
from app.api.v1.voice import router as voice_router
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
from app.core.metrics_middleware import MetricsMiddleware
from app.core.middleware import (
    OriginGuardMiddleware,
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
    RequestTimeoutMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.ratelimit import InMemoryRateLimiter
from app.core.sentry import init_sentry
from app.core.tokens import TokenService
from app.db.session import Database, build_database
from app.services.agent.service import build_agent_navigator
from app.services.chat import ChatOrchestrator, build_session_service
from app.services.exercises.engine import get_catalog
from app.services.nlp import build_analyzer

API_TITLE = "Manovia API"
API_VERSION = "1.0.0"


def _build_shadow_runner(settings: Settings) -> object | None:
    """Day 34 shadow runner, or None. Never raises: shadow is optional.

    The candidate artefact is checksum-verified before a session exists;
    a bad artefact means shadow stays off and the reason is kept on the
    returned marker's behalf by the caller (``app.state.shadow_failure``).
    """

    if not settings.safety_shadow_enabled or not settings.safety_shadow_candidate_dir.strip():
        return None
    import json
    from pathlib import Path

    from app.services.ml_runtime.registry import (
        ChecksumMismatchError,
        LoadedModel,
        sha256_file,
    )
    from app.services.ml_runtime.runtime import OnnxModelRuntime
    from app.services.ml_runtime.shadow import ShadowRunner

    directory = Path(settings.safety_shadow_candidate_dir).expanduser()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    model_path = directory / str(manifest["model_file"])
    if sha256_file(model_path) != manifest["sha256"]:
        raise ChecksumMismatchError("shadow candidate checksum mismatch; shadow disabled")
    loaded = LoadedModel(
        model_name="shadow-candidate",
        version=settings.safety_shadow_candidate_version,
        directory=directory,
        model_path=model_path,
        manifest=manifest,
    )
    runtime = OnnxModelRuntime(
        loaded,
        timeout_seconds=settings.ml_runtime_timeout_seconds,
        max_batch_size=settings.ml_runtime_max_batch_size,
        batch_wait_ms=settings.ml_runtime_batch_wait_ms,
        max_workers=settings.ml_runtime_max_workers,
    )
    return ShadowRunner(
        runtime,
        candidate_version=settings.safety_shadow_candidate_version,
        candidate_labels=tuple(str(item) for item in manifest.get("labels", [])),
        log_path=settings.safety_shadow_log_path or None,
    )


def create_app(settings: Settings | None = None, *, database: Database | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    ``database`` can be injected (tests, scripts); by default one is built from
    ``settings.database_url``.
    """
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    # Day 27: error reporting first, so a failure during the rest of startup
    # is visible in Sentry. Without SENTRY_DSN this is a no-op (offline by
    # default), and every event it ever sends passes the PII scrubber.
    init_sentry(app_settings)
    # Reject the whole exercise catalogue before serving any request.
    get_catalog()

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
        # Day 34 warm-up: synthetic inputs through the ONNX sessions so the
        # first real message does not pay session warm-up. Failure is
        # recorded on the runtime, never raised — startup must not die
        # because a compressed model would not warm.
        runtime = getattr(created.state, "ml_runtime", None)
        if runtime is not None:
            with suppress(Exception):  # warm-up is best-effort by design
                runtime.warmup()
        shadow = getattr(created.state, "shadow_runner", None)
        if shadow is not None:
            with suppress(Exception):  # shadow warm-up is best-effort too
                shadow.warmup()
        yield
        orchestrator: ChatOrchestrator | None = created.state.chat_orchestrator
        if orchestrator is not None:
            await orchestrator.llm.aclose()
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
    # Day 29: per-user budget for the data export (RT-P-EX-02).
    app.state.rate_limit_export = InMemoryRateLimiter(
        app_settings.privacy_export_per_hour, window_seconds=3600
    )
    # Day 47: the public share-viewer endpoints are unauthenticated by design,
    # so they get their own, much tighter budget than the global one.
    app.state.rate_limit_share = InMemoryRateLimiter(app_settings.share_rate_limit_per_minute)

    # Emotion analysis (Day 6). Built here so one chain (and one model load,
    # and one result cache) is shared by every request. Nothing is loaded
    # eagerly: a bad EMOTION_MODEL_ID must not stop the app from starting, it
    # must fall back to the lexicon analyzers on first use.
    app.state.emotion_analyzer = build_analyzer(app_settings)

    # Day 34 compressed-model serving + shadow. Both are built (not warmed)
    # here; warm-up happens in the lifespan. Construction failures are
    # recorded as metadata and never stop startup (ADR 0034).
    app.state.ml_runtime = None
    app.state.ml_runtime_failure = "disabled"
    if app_settings.ml_runtime_enabled and app_settings.ml_runtime_registry_dir.strip():
        try:
            from app.services.ml_runtime.runtime import build_runtime_from_registry

            app.state.ml_runtime = build_runtime_from_registry(
                app_settings.ml_runtime_registry_dir,
                app_settings.ml_runtime_model_name,
                timeout_seconds=app_settings.ml_runtime_timeout_seconds,
                max_batch_size=app_settings.ml_runtime_max_batch_size,
                batch_wait_ms=app_settings.ml_runtime_batch_wait_ms,
                max_workers=app_settings.ml_runtime_max_workers,
            )
            app.state.ml_runtime_failure = "none"
        except Exception as exc:  # registry/checksum/session: degrade alike
            app.state.ml_runtime_failure = f"unavailable:{type(exc).__name__}"
    app.state.shadow_runner = None
    app.state.shadow_failure = "disabled"
    if app_settings.safety_shadow_enabled:
        try:
            app.state.shadow_runner = _build_shadow_runner(app_settings)
            app.state.shadow_failure = "none" if app.state.shadow_runner else "not_configured"
        except Exception as exc:
            app.state.shadow_failure = f"unavailable:{type(exc).__name__}"

    # Chat (Day 11). The session service is cheap and owns the ephemeral
    # (in-memory) store. The orchestrator is built on first use instead:
    # constructing it loads the ML artifact and the provider chain, which must
    # not slow or fail start-up (see app.api.v1.chat.get_orchestrator).
    app.state.chat_sessions = build_session_service(app_settings, app.state.db)
    app.state.chat_orchestrator = None

    # Day 44: the agentic care navigator. Like the orchestrator it is
    # built eagerly but cheaply — the gate, the loop and the directory
    # are all offline and deterministic; only the model adapter needs
    # keys, and it degrades to the Fake (with a warning) without them.
    # agent_enabled=false installs None, so the routes answer 503
    # instead of running a half-configured navigator.
    if app_settings.agent_enabled:
        app.state.agent_navigator = build_agent_navigator(app_settings)
    else:
        app.state.agent_navigator = None

    # Day 40: one process-wide, local-only voice runtime. It holds only model
    # handles and deterministic crisis assistant audio; each participant's PCM
    # and transcript are owned by an in-memory WebSocket session and discarded
    # on close. Building it does not load a STT model or make a network call.
    from app.services.voice.realtime import VoiceRuntime

    app.state.voice_runtime = VoiceRuntime.build(app_settings)

    # Day 45: one process-wide meditation service (generator + local TTS
    # renderer). Building it loads no model and makes no network call; with
    # LLM_PROVIDER=fake the generator is curated-only by design.
    app.state.meditation_service = build_meditation_service(app_settings)

    # Middleware added last runs first (outermost), so the request ID wraps
    # every response, including error responses. Rate limiting sits just inside
    # the security headers so even a 429 is fully dressed; it is skipped when
    # disabled (load tests only).
    # Day 27: the metrics middleware sits just inside the request-ID wrapper,
    # so every request — errors and 429s included — is counted and timed
    # against its route template (never the raw path).
    # Day 22 hardening. Added before the layers below, so they sit *inside*
    # CORS/security headers/rate limiting: a 413 or a 403 from these guards
    # still carries the standard envelope, request id and security headers.
    # Order inside the app (outermost first): origin guard, timeout, body
    # limit. The body ceiling is the innermost check because it is the most
    # expensive one to reach.
    app.add_middleware(
        OriginGuardMiddleware,
        allowed_origins=tuple(app_settings.allowed_origin_list),
        enabled=app_settings.origin_check_enabled,
    )
    app.add_middleware(
        RequestTimeoutMiddleware,
        timeout_seconds=app_settings.request_timeout_seconds,
        exempt_suffixes=("/stream",),
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=app_settings.max_request_bytes,
    )
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
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestIDMiddleware)

    # Day 27: the Prometheus scrape endpoint lives at the server root, not
    # under /api/v1. Registered only when metrics are enabled, like the
    # dev-only router below — an absent route leaks nothing.
    if app_settings.metrics_enabled:
        app.include_router(metrics_router)
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(consent_router, prefix="/api/v1")
    app.include_router(chat_router, prefix="/api/v1")
    app.include_router(agent_router, prefix="/api/v1")
    app.include_router(knowledge_router, prefix="/api/v1")
    app.include_router(mood_router, prefix="/api/v1")
    app.include_router(exercises_router, prefix="/api/v1")
    app.include_router(meditation_router, prefix="/api/v1")
    app.include_router(journal_router, prefix="/api/v1")
    app.include_router(cbt_router, prefix="/api/v1")
    app.include_router(assessments_router, prefix="/api/v1")
    app.include_router(insights_router, prefix="/api/v1")
    app.include_router(crisis_router, prefix="/api/v1")
    app.include_router(plan_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")
    app.include_router(privacy_router, prefix="/api/v1")
    app.include_router(voice_router, prefix="/api/v1")
    # Day 47: the owner side of the share bridge, and the public viewer side.
    # The public router only answers for tokens that are live, and only with
    # ciphertext.
    if app_settings.share_enabled:
        app.include_router(sharing_router, prefix="/api/v1")
        app.include_router(share_view_router, prefix="/api/v1")
    if not app_settings.is_production:
        # Dev-only surface: in production the route does not exist at all, so
        # it is absent from the schema and a request gets the generic 404.
        app.include_router(dev_router, prefix="/api/v1")

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    return app


app = create_app()
