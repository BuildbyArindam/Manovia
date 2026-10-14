"""The chat service: the orchestrator and the pieces it is built from.

Typical use (``app.main`` does this, lazily, on the first message)::

    orchestrator = build_orchestrator(settings, llm=build_llm_chain(settings), ...)
    admitted = orchestrator.admit(user_id=user.id, text=payload.message)
    reply = await orchestrator.respond(admitted, conversation)

See ``docs/architecture.md`` for the pipeline diagram and ADR 0011 for the
decisions behind it.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.ratelimit import InMemoryRateLimiter
from app.db.session import Database
from app.services.chat.conversation import (
    Conversation,
    EphemeralConversation,
    PersistentConversation,
)
from app.services.chat.ephemeral import EphemeralSessionStore
from app.services.chat.orchestrator import (
    AdmittedMessage,
    ChatOrchestrator,
    OrchestratorConfig,
)
from app.services.chat.output_guard import GuardVerdict, OutputGuard, PassthroughOutputGuard
from app.services.chat.retrieval import NullRetriever, RetrievedChunk, Retriever
from app.services.chat.sessions import (
    ChatMemory,
    ChatSessionService,
    OpenedSession,
    SessionInfo,
    StoredTurn,
)
from app.services.chat.types import (
    ChatMetadata,
    ChatReply,
    FinalEvent,
    ResponseType,
    StreamEvent,
    TokenEvent,
)
from app.services.llm import LLMProvider, Redactor, build_llm_chain
from app.services.nlp import build_analyzer
from app.services.nlp.base import EmotionAnalyzer
from app.services.safety.escalation import build_escalator
from app.services.safety.ml_classifier import SafetyClassifier, build_safety_classifier
from app.services.safety.rules import build_engine


def build_session_service(settings: Settings, database: Database) -> ChatSessionService:
    """The session service over this deployment's database and a fresh ephemeral store."""
    return ChatSessionService(
        database.session_factory,
        EphemeralSessionStore(
            ttl_seconds=settings.chat_ephemeral_ttl_seconds,
            max_sessions=settings.chat_ephemeral_max_sessions,
            max_turns=settings.chat_ephemeral_max_turns,
        ),
    )


def build_orchestrator(
    settings: Settings,
    *,
    llm: LLMProvider | None = None,
    analyzer: EmotionAnalyzer | None = None,
    classifier: SafetyClassifier | None = None,
) -> ChatOrchestrator:
    """Build the orchestrator this deployment should use.

    Nothing here makes a network call. Collaborators default to the same
    builders the rest of the app uses (the provider chain, the emotion chain,
    the shared rules engine and escalator, the ML classifier unless disabled);
    tests and scripts inject Fakes.
    """
    if classifier is None:
        # Day 33: the same selector the API dependency uses, so `SAFETY_ML` means
        # one thing across the whole app.
        classifier = build_safety_classifier(settings)
    # Day 13: the knowledge-backed retriever when retrieval is enabled; the
    # null one otherwise, and the pipeline is exactly what it was on Day 11.
    # Store/embedder load failures degrade to "no reference material" inside
    # the factory — never to a failed turn. (Imported here rather than at the
    # top of the module because the rag package imports this one: a module
    # level import would be a circular one.)
    from app.services.rag.factory import build_knowledge_retriever

    retriever = build_knowledge_retriever(settings) if settings.rag_enabled else NullRetriever()
    return ChatOrchestrator(
        llm=llm or build_llm_chain(settings),
        engine=build_engine(),
        classifier=classifier,
        escalator=build_escalator(),
        analyzer=analyzer or build_analyzer(settings),
        redactor=Redactor(),
        retriever=retriever,
        limiter=InMemoryRateLimiter(settings.chat_rate_limit_per_minute),
        config=OrchestratorConfig.from_settings(settings),
    )


__all__ = [
    "AdmittedMessage",
    "ChatMemory",
    "ChatMetadata",
    "ChatOrchestrator",
    "ChatReply",
    "ChatSessionService",
    "Conversation",
    "EphemeralConversation",
    "EphemeralSessionStore",
    "FinalEvent",
    "GuardVerdict",
    "NullRetriever",
    "OpenedSession",
    "OrchestratorConfig",
    "OutputGuard",
    "PassthroughOutputGuard",
    "PersistentConversation",
    "ResponseType",
    "RetrievedChunk",
    "Retriever",
    "SessionInfo",
    "StoredTurn",
    "StreamEvent",
    "TokenEvent",
    "build_orchestrator",
    "build_session_service",
]
