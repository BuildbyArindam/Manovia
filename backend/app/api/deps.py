"""FastAPI dependencies: the data layer, the current user, and consent gates.

``get_session`` hands out a session but does not commit: a request that writes
must call ``await session.commit()`` itself (or use ``session.begin()`` for
explicit transaction scope). Keeping commits out of the dependency makes the
write boundary visible in the endpoint instead of hidden in teardown code.

``get_current_user`` authenticates the bearer access token (JWT, 15 minutes)
and loads the live user; ``require_consent(*kinds)`` builds on it and refuses
endpoints until the current versions of those agreements are granted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.content import ConsentDocuments, HelplineContent, load_consent_documents, load_helplines
from app.core.errors import ApiError
from app.core.lockout import LoginLockout
from app.core.tokens import ACCESS_TOKEN_TYPE, TokenError, TokenExpiredError, TokenService
from app.db.repos import ConsentRepository, UserRepository
from app.db.session import Database
from app.models.enums import ConsentKind
from app.models.user import User
from app.services.nlp.base import EmotionAnalyzer
from app.services.safety.escalation import Escalator, build_escalator
from app.services.safety.ml_classifier import NullClassifier, SafetyClassifier, build_ml_classifier
from app.services.safety.rules import RuleEngine, build_engine


def get_database(request: Request) -> Database:
    """The engine/session factory owned by this application instance."""
    database: Database = request.app.state.db
    return database


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A per-request :class:`AsyncSession`."""
    database: Database = request.app.state.db
    async with database.session_factory() as session:
        yield session


def get_token_service(request: Request) -> TokenService:
    """The JWT service installed by :func:`app.main.create_app`."""
    service: TokenService = request.app.state.token_service
    return service


def get_login_lockout(request: Request) -> LoginLockout:
    """The failed-login tracker installed by :func:`app.main.create_app`."""
    lockout: LoginLockout = request.app.state.login_lockout
    return lockout


def get_consent_documents() -> ConsentDocuments:
    """The shipped, versioned consent documents."""
    return load_consent_documents()


def get_helplines() -> HelplineContent:
    """The shipped, human-verified helpline set."""
    return load_helplines()


def get_rule_engine() -> RuleEngine:
    """The process-wide safety rules engine (AGENTS.md safety rule 1).

    Built once per process from the shipped pattern YAML and shared: it is
    stateless, so every request sees the same rules and the same answers.
    """
    return build_engine()


def get_escalator() -> Escalator:
    """The level-to-policy mapper and the pre-written crisis messages."""
    return build_escalator()


def get_ml_classifier(request: Request) -> SafetyClassifier:
    """The Day 9 ML safety classifier (Day 8 behaviour when disabled).

    The artifact loads once per process and is shared: prediction is
    stateless. ``SAFETY_ML_ENABLED=false`` (the test suite's default) or a
    missing artifact both hand back a disabled classifier, and the pipeline
    runs rules-only — degradation, never failure.
    """
    settings = request.app.state.settings
    if not settings.safety_ml_enabled:
        return NullClassifier(reason="disabled")
    return build_ml_classifier(artifact_dir=settings.safety_ml_artifact_dir)


def get_emotion_analyzer(request: Request) -> EmotionAnalyzer:
    analyzer: EmotionAnalyzer = request.app.state.emotion_analyzer
    return analyzer


# The chat dependencies deliberately do **not** live here. An earlier draft of
# the Day 11 milestone added `get_llm_chain` / `get_redactor` /
# `get_ephemeral_store` / `get_chat_rate_limiter` / `get_orchestrator` to this
# module; they read `app.state` names that `create_app` never installs and
# called a `ChatOrchestrator` constructor that does not exist, so importing this
# module raised and no test could run. The orchestrator is built lazily by
# `app.api.v1.chat.get_orchestrator`, which is the only provider the routes use.
# `tests/unit/test_import_graph.py` now fails CI if a name imported from
# `app.services.chat` anywhere in `app/` stops existing.


SessionDep = Annotated[AsyncSession, Depends(get_session)]
TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]
ConsentDocumentsDep = Annotated[ConsentDocuments, Depends(get_consent_documents)]
HelplinesDep = Annotated[HelplineContent, Depends(get_helplines)]
RuleEngineDep = Annotated[RuleEngine, Depends(get_rule_engine)]
EscalatorDep = Annotated[Escalator, Depends(get_escalator)]
MLClassifierDep = Annotated[SafetyClassifier, Depends(get_ml_classifier)]
LockoutDep = Annotated[LoginLockout, Depends(get_login_lockout)]
EmotionAnalyzerDep = Annotated[EmotionAnalyzer, Depends(get_emotion_analyzer)]


def _bearer_token(request: Request) -> str:
    """Extract the bearer token, or raise the 401 the caller deserves."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(
            401,
            "token_missing",
            "Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


async def get_current_user(
    request: Request,
    session: SessionDep,
    tokens: TokenServiceDep,
) -> User:
    """Authenticate the request and return the live user.

    Accepts a valid **access** token only. Anonymous (guest) users authenticate
    normally — that is the point of guest mode — but a soft-deleted account can
    never sign in again. Failure codes distinguish the cases for the client
    (``token_missing`` / ``token_expired`` / ``token_invalid`` /
    ``account_deleted``) without leaking whether an account exists.
    """
    raw = _bearer_token(request)
    try:
        claims = tokens.verify(raw, expected_type=ACCESS_TOKEN_TYPE)
    except TokenExpiredError as exc:
        raise ApiError(
            401,
            "token_expired",
            "Your session has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except TokenError as exc:
        raise ApiError(
            401,
            "token_invalid",
            "Sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = await UserRepository(session).get(claims.subject)
    if user is None or user.is_deleted:
        raise ApiError(
            401,
            "account_deleted",
            "This account no longer exists.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_consent(*kinds: ConsentKind) -> Callable[..., Awaitable[User]]:
    """Dependency factory: the current user must have granted ``kinds``.

    The gate checks each grant against the **current** document version (see
    ``app/content/consent_documents.json``), so an agreement to an old version
    does not silently satisfy a new one. Chat, journal and mood endpoints are
    built on this (``ai_disclosure`` + ``terms``); the error is
    ``403 consent_required`` and names the missing kinds.
    """
    if not kinds:
        raise ValueError("require_consent needs at least one consent kind")

    async def dependency(
        user: CurrentUser,
        session: SessionDep,
        documents: ConsentDocumentsDep,
    ) -> User:
        repo = ConsentRepository(session)
        missing = [
            kind.value
            for kind in kinds
            if not await repo.is_granted(
                user_id=user.id, kind=kind, version=documents.version_for(kind)
            )
        ]
        if missing:
            raise ApiError(
                403,
                "consent_required",
                f"Consent required: {', '.join(missing)}.",
            )
        return user

    return dependency
