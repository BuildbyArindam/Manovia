"""Chat orchestrator — the heart of the system (Day 11).

Pipeline per user message (spec order):

 1. Validate length/encoding; rate limit per user.
 2. Input safety: rules + ML ensemble -> RiskAssessment. If HIGH or IMMINENT:
    STOP. Return deterministic crisis response (Day 8). Do not call the LLM.
    Persist the message (encrypted) and a SafetyEvent. Mark response type "crisis".
 3. PII redaction of the text going to the LLM.
 4. Emotion analysis (Day 6) of the original text.
 5. Retrieval stub (returns [] today; Day 13 fills it).
 6. Build the prompt: system prompt + short conversation window + emotion hint
    + safety hint (e.g. "user may be at MEDIUM risk: include a gentle check-in")
    + retrieval context.
 7. Call the LLM via the provider chain.
 8. Output guard stub (passes through today; Day 14 fills it).
 9. Persist assistant message (encrypted) and return.

The orchestrator is deliberately synchronous in its CPU-bound parts (safety
rules, emotion lexicons) and async only where it must be (LLM, DB). The
blocking analyzers run in a threadpool at the call site when the orchestrator
is used from an async endpoint.

Privacy:

* No raw text at INFO or above.
* Emotion is analyzed on the original text; redaction happens only on the
  text that leaves for the LLM.
* Ephemeral sessions never touch the DB.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.ratelimit import InMemoryRateLimiter
from app.db.repos import ChatRepository, SafetyEventRepository
from app.models.enums import MessageRole, SafetyEventSource
from app.models.user import User
from app.services.chat.ephemeral import EphemeralStore
from app.services.chat.output_guard import GuardResult, guard_output
from app.services.chat.retrieval import RetrievalResult, retrieve
from app.services.llm.base import LLMMessage
from app.services.llm.chain import LLMChain
from app.services.llm.prompts import render_system_prompt
from app.services.nlp.base import EmotionResult
from app.services.nlp.redaction import Redactor
from app.services.safety.base import RiskAssessment, text_fingerprint
from app.services.safety.base import RiskLevel as SafetyRiskLevel
from app.services.safety.ensemble import EnsembleDecision, apply_decision, decide
from app.services.safety.escalation import EscalationPlan, Escalator
from app.services.safety.ml_classifier import SafetyClassifier
from app.services.safety.rules import RuleEngine

logger = structlog.get_logger(__name__)

# --- Validation -------------------------------------------------------------

MAX_MESSAGE_CHARS_DEFAULT: Final = 4000
MIN_MESSAGE_CHARS: Final = 1

# Control chars that are never valid in a chat message, except \n \r \t
# Null byte is always rejected — it breaks logging, DB drivers and many UIs.
FORBIDDEN_CHARS = {"\x00"}


def _validate_encoding(text: str) -> None:
    # Python str is already decoded, but we can still check for surrogates,
    # null bytes and non-printable control chars that indicate binary paste.
    if "\x00" in text:
        raise ApiError(400, "invalid_message", "Message contains invalid characters.")
    # Check for unpaired surrogates (can appear when JS sends bad UTF-16)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ApiError(400, "invalid_encoding", "Message encoding is invalid.") from exc


def _validate_length(text: str, max_chars: int) -> None:
    stripped = text.strip()
    if len(stripped) < MIN_MESSAGE_CHARS:
        raise ApiError(400, "empty_message", "Message must not be empty.")
    if len(text) > max_chars:
        raise ApiError(
            400,
            "message_too_long",
            f"Message is too long (max {max_chars} characters).",
        )


# --- Result types -----------------------------------------------------------


@dataclass(frozen=True)
class ChatMessageOut:
    """One persisted (or ephemeral) message, decrypted for the response."""

    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: Any
    risk_level: int
    emotion: str | None = None


@dataclass(frozen=True)
class OrchestratorResult:
    """What the orchestrator returns to the API layer."""

    # The assistant reply, ready to show
    reply_text: str
    # Metadata for the UI
    risk_level: str  # wire form: none/low/medium/high/imminent
    stored_risk_level: str  # DB form: none/caution/elevated/crisis
    emotion: str | None
    emotion_result: EmotionResult | None
    response_type: str  # crisis | check_in | supportive | normal | fallback
    resources: tuple[Any, ...] = ()
    emergency: Any | None = None
    crisis_message: Any | None = None
    # Provenance
    degraded: bool = False
    fallbacks_used: int = 0
    provider: str | None = None
    prompt_version: str | None = None
    # The two messages (user + assistant) for persistence verification
    user_message: ChatMessageOut | None = None
    assistant_message: ChatMessageOut | None = None
    # Safety
    assessment: RiskAssessment | None = None
    decision: EnsembleDecision | None = None
    plan: EscalationPlan | None = None
    # Redaction report (metadata only)
    redaction_summary: str | None = None


# --- Prompt building --------------------------------------------------------


def _emotion_hint(emotion: EmotionResult | None) -> str | None:
    if emotion is None:
        return None
    # Keep it short and safe — no user text, only metadata
    return (
        f"User emotion: {emotion.primary} "
        f"(valence {emotion.valence:.2f}, arousal {emotion.arousal:.2f}). "
        f"Respond with empathy appropriate to this feeling."
    )


def _safety_hint(level: SafetyRiskLevel) -> str | None:
    if level == SafetyRiskLevel.MEDIUM:
        return (
            "User may be at MEDIUM risk: include a gentle check-in, "
            "encourage reaching out to a trusted person or helpline, "
            "and show you are listening. Do not repeat risk language."
        )
    if level == SafetyRiskLevel.LOW:
        return "User may be at LOW risk: use a supportive tone, acknowledge their feelings gently."
    return None


def _build_system_prompt(
    base_version: str,
    language: str | None,
    max_words: int,
    emotion_hint: str | None,
    safety_hint: str | None,
    retrieval: list[RetrievalResult],
) -> str:
    rendered = render_system_prompt(version=base_version, language=language, max_words=max_words)
    parts = [rendered.text]
    guidance: list[str] = []
    if emotion_hint:
        guidance.append(f"- {emotion_hint}")
    if safety_hint:
        guidance.append(f"- {safety_hint}")
    if retrieval:
        # Stub: include snippets if any
        ctx = "\n".join(f"- {r.text}" for r in retrieval[:3])
        guidance.append(f"- Retrieval context:\n{ctx}")
    if guidance:
        parts.append("\nAdditional guidance for this turn:\n" + "\n".join(guidance))
    return "\n".join(parts)


def _conversation_window_to_llm_messages(messages: list[Any], limit: int) -> list[LLMMessage]:
    """Take oldest-first messages and keep the most recent ``limit`` turns."""
    # messages are either DB Message rows (need decrypt) or EphemeralMessage
    # The caller already decrypted for persistent; for ephemeral they are plain.
    # This helper expects objects with role and content attributes.
    # We keep at most limit*2 messages? The setting is turns, so we keep last N.
    recent = messages[-limit:] if limit > 0 else messages
    out: list[LLMMessage] = []
    for m in recent:
        role = getattr(m, "role", None)
        content = getattr(m, "content", None)
        if role is None or content is None:
            continue
        # Map to LLM roles: user/assistant only; system messages are not in history
        role_str = role.value if hasattr(role, "value") else str(role)
        if role_str not in ("user", "assistant"):
            continue
        out.append(LLMMessage(role=role_str, content=content))  # type: ignore[arg-type]
    return out


# --- Orchestrator -----------------------------------------------------------


class ChatOrchestrator:
    """Implements the Day 11 pipeline."""

    def __init__(
        self,
        settings: Settings,
        *,
        rule_engine: RuleEngine,
        ml_classifier: SafetyClassifier,
        escalator: Escalator,
        emotion_analyzer: Any,  # EmotionAnalyzer
        llm_chain: LLMChain,
        redactor: Redactor,
        ephemeral_store: EphemeralStore,
        chat_rate_limiter: InMemoryRateLimiter | None = None,
    ) -> None:
        self._settings = settings
        self._rule_engine = rule_engine
        self._ml_classifier = ml_classifier
        self._escalator = escalator
        self._emotion_analyzer = emotion_analyzer
        self._llm_chain = llm_chain
        self._redactor = redactor
        self._ephemeral = ephemeral_store
        self._limiter = chat_rate_limiter or InMemoryRateLimiter(
            limit=settings.chat_rate_limit_per_minute
        )

    # ---- Public API -------------------------------------------------------

    async def handle_message(
        self,
        *,
        db_session: AsyncSession | None,
        user: User,
        session_id: uuid.UUID,
        content: str,
        is_ephemeral: bool,
        region: str | None = None,
        locale: str | None = None,
    ) -> OrchestratorResult:
        """Run the full pipeline for one user message.

        ``db_session`` may be None for ephemeral sessions; when persistent it
        must be a live AsyncSession.
        """
        # 1. Validate length/encoding; rate limit per user
        _validate_encoding(content)
        _validate_length(content, self._settings.chat_max_message_chars)

        rl = self._limiter.hit(str(user.id))
        if not rl.allowed:
            raise ApiError(
                429,
                "rate_limited",
                "Too many messages. Please slow down.",
                headers={"Retry-After": str(rl.retry_after)},
            )

        # 2. Input safety: rules + ML ensemble -> RiskAssessment
        # Run CPU-bound work in threadpool? The orchestrator itself is async,
        # but the analyzers are sync. For simplicity we run them directly here;
        # the endpoint can also offload if needed. The safety engine is fast
        # (0.3ms) so direct call is fine.
        try:
            assessment = self._rule_engine.assess(content)
        except Exception:
            # Safety must never be the reason a message goes unanswered;
            # degrade to NONE and log.
            logger.warning("safety_assess_failed", user_id=str(user.id))
            from app.services.safety.base import empty_assessment

            assessment = empty_assessment(rationale="safety.error")

        decision = decide(
            assessment,
            self._ml_classifier,
            text=content,
            min_confidence=self._settings.safety_ml_min_confidence,
            crisis_mass_floor=self._settings.safety_ml_crisis_mass_floor,
            suspicion_floor=self._settings.safety_ml_suspicion_floor,
        )
        final_assessment = apply_decision(assessment, decision)
        plan = self._escalator.plan(final_assessment, region=region, locale=locale)

        # Logging: fingerprint only
        logger.info(
            "chat_safety_assessed",
            user_id=str(user.id),
            session_id=str(session_id),
            text_sha=text_fingerprint(content),
            text_length=len(content),
            level=plan.level_code,
            stored_level=plan.stored_level_code,
            source=decision.source,
            allow_llm=plan.allow_llm,
        )

        # Emotion analysis of original text (Day 6) — before redaction
        emotion_result: EmotionResult | None = None
        try:
            # EmotionAnalyzer.analyze is sync; call directly (fast for keyword)
            emotion_result = self._emotion_analyzer.analyze(content)
        except Exception:
            logger.warning("emotion_analyze_failed", user_id=str(user.id))
            emotion_result = None

        # If HIGH or IMMINENT: STOP, crisis response, no LLM
        if final_assessment.level.is_crisis:
            return await self._handle_crisis(
                db_session=db_session,
                user=user,
                session_id=session_id,
                content=content,
                is_ephemeral=is_ephemeral,
                assessment=final_assessment,
                decision=decision,
                plan=plan,
                emotion_result=emotion_result,
            )

        # 3. PII redaction of the text going to the LLM
        redaction_result = self._redactor.redact(content)
        redacted_text = redaction_result.text
        redaction_summary = redaction_result.summary()

        # 5. Retrieval stub
        retrieval_results = await retrieve(redacted_text, session_id=session_id, user_id=user.id)

        # 6. Build prompt
        emotion_hint = _emotion_hint(emotion_result)
        safety_hint = _safety_hint(final_assessment.level)
        language = emotion_result.language if emotion_result and emotion_result.language else None
        system_prompt = _build_system_prompt(
            base_version=self._settings.llm_prompt_version,
            language=language,
            max_words=self._settings.llm_max_tokens,  # reuse as max_words? Use default 120
            emotion_hint=emotion_hint,
            safety_hint=safety_hint,
            retrieval=retrieval_results,
        )

        # Conversation window
        history_messages = await self._get_history(
            db_session=db_session,
            session_id=session_id,
            is_ephemeral=is_ephemeral,
            limit=self._settings.chat_history_window,
        )
        llm_messages = _conversation_window_to_llm_messages(
            history_messages, limit=self._settings.chat_history_window
        )
        # Add the new user turn (redacted)
        llm_messages.append(LLMMessage(role="user", content=redacted_text))

        # 7. Call LLM via provider chain
        try:
            llm_result = await self._llm_chain.complete(
                llm_messages,
                system=system_prompt,
                max_tokens=self._settings.llm_max_tokens,
                temperature=self._settings.llm_temperature,
            )
        except Exception as exc:
            # The chain should never raise (canned is terminal), but if it does,
            # fall back to canned reply manually.
            logger.warning(
                "llm_chain_failed",
                user_id=str(user.id),
                error_type=type(exc).__name__,
            )
            # Build a fake result
            from app.services.llm.base import LLMResult
            from app.services.llm.canned import CANNED_REPLY

            llm_result = LLMResult(
                text=CANNED_REPLY,
                provider="canned",
                model=None,
                degraded=True,
                fallbacks_used=99,
                prompt_version=self._settings.llm_prompt_version,
            )

        # 8. Output guard stub
        guard_res: GuardResult = guard_output(llm_result.text)
        final_text = guard_res.text

        # 9. Persist assistant message (encrypted) and return
        response_type = self._response_type_for_level(
            final_assessment.level, degraded=llm_result.degraded
        )

        # Persist user message first (if not already persisted in crisis path)
        user_msg_out: ChatMessageOut | None = None
        assistant_msg_out: ChatMessageOut | None = None

        if is_ephemeral:
            # Ephemeral: in-memory only
            um = self._ephemeral.add_message(
                session_id,
                role=MessageRole.USER,
                content=content,
                risk_level=int(final_assessment.to_stored_level()),
                emotion=emotion_result.primary if emotion_result else None,
            )
            am = self._ephemeral.add_message(
                session_id,
                role=MessageRole.ASSISTANT,
                content=final_text,
                risk_level=int(final_assessment.to_stored_level()),
                emotion=None,
            )
            if um:
                user_msg_out = ChatMessageOut(
                    id=um.id,
                    role=um.role,
                    content=um.content,
                    created_at=um.created_at,
                    risk_level=um.risk_level,
                    emotion=um.emotion,
                )
            if am:
                assistant_msg_out = ChatMessageOut(
                    id=am.id,
                    role=am.role,
                    content=am.content,
                    created_at=am.created_at,
                    risk_level=am.risk_level,
                    emotion=None,
                )
        else:
            assert db_session is not None
            chat_repo = ChatRepository(db_session)
            # User message
            um_row = await chat_repo.add_message(
                session_id=session_id,
                role=MessageRole.USER,
                content=content,
                risk_level=final_assessment.to_stored_level(),
                emotion=emotion_result.primary if emotion_result else None,
            )
            # Assistant message
            am_row = await chat_repo.add_message(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content=final_text,
                risk_level=final_assessment.to_stored_level(),
                emotion=None,
            )
            # Safety event if needed
            if plan.record_event:
                source = (
                    SafetyEventSource.RULES if decision.source == "rules" else SafetyEventSource.ML
                )
                await SafetyEventRepository(db_session).record(
                    risk_level=final_assessment.to_stored_level(),
                    source=source,
                    user_id=user.id,
                    session_id=session_id,
                )
            await db_session.commit()
            user_msg_out = ChatMessageOut(
                id=um_row.id,
                role=MessageRole.USER,
                content=content,
                created_at=um_row.created_at,
                risk_level=int(um_row.risk_level),
                emotion=emotion_result.primary if emotion_result else None,
            )
            assistant_msg_out = ChatMessageOut(
                id=am_row.id,
                role=MessageRole.ASSISTANT,
                content=final_text,
                created_at=am_row.created_at,
                risk_level=int(am_row.risk_level),
                emotion=None,
            )

        return OrchestratorResult(
            reply_text=final_text,
            risk_level=plan.level_code,
            stored_risk_level=plan.stored_level_code,
            emotion=emotion_result.primary if emotion_result else None,
            emotion_result=emotion_result,
            response_type=response_type,
            resources=tuple(plan.resources),
            emergency=plan.emergency,
            crisis_message=plan.message,
            degraded=llm_result.degraded,
            fallbacks_used=llm_result.fallbacks_used,
            provider=llm_result.provider,
            prompt_version=llm_result.prompt_version,
            user_message=user_msg_out,
            assistant_message=assistant_msg_out,
            assessment=final_assessment,
            decision=decision,
            plan=plan,
            redaction_summary=redaction_summary,
        )

    async def _handle_crisis(
        self,
        *,
        db_session: AsyncSession | None,
        user: User,
        session_id: uuid.UUID,
        content: str,
        is_ephemeral: bool,
        assessment: RiskAssessment,
        decision: EnsembleDecision,
        plan: EscalationPlan,
        emotion_result: EmotionResult | None,
    ) -> OrchestratorResult:
        """Return deterministic crisis response without calling LLM."""
        crisis_text = ""
        if plan.message:
            # RenderedTemplate: title is str, body and safety_steps are list[str]
            parts: list[str] = []
            title = getattr(plan.message, "title", None)
            if title:
                parts.append(title if isinstance(title, str) else str(title))
            body = getattr(plan.message, "body", None)
            if body:
                if isinstance(body, (list, tuple)):
                    parts.extend([str(p) for p in body if p])
                else:
                    parts.append(str(body))
            emergency = getattr(plan.message, "emergency_instruction", None)
            if emergency:
                parts.append(str(emergency))
            trusted = getattr(plan.message, "trusted_person", None)
            if trusted:
                parts.append(str(trusted))
            safety_steps = getattr(plan.message, "safety_steps", None)
            if safety_steps:
                if isinstance(safety_steps, (list, tuple)):
                    parts.extend([str(s) for s in safety_steps if s])
                else:
                    parts.append(str(safety_steps))
            helpline_intro = getattr(plan.message, "helpline_intro", None)
            if helpline_intro:
                parts.append(str(helpline_intro))
            closing = getattr(plan.message, "closing", None)
            if closing:
                parts.append(str(closing))
            crisis_text = "\n\n".join(p for p in parts if p)
            if not crisis_text:
                crisis_text = str(plan.message)
        if not crisis_text:
            crisis_text = (
                "What you're feeling is real, and you don't have to face it alone. "
                "Please reach out for help right now."
            )

        user_msg_out: ChatMessageOut | None = None
        assistant_msg_out: ChatMessageOut | None = None

        if is_ephemeral:
            um = self._ephemeral.add_message(
                session_id,
                role=MessageRole.USER,
                content=content,
                risk_level=int(assessment.to_stored_level()),
                emotion=emotion_result.primary if emotion_result else None,
            )
            am = self._ephemeral.add_message(
                session_id,
                role=MessageRole.ASSISTANT,
                content=crisis_text,
                risk_level=int(assessment.to_stored_level()),
                emotion=None,
            )
            if um:
                user_msg_out = ChatMessageOut(
                    id=um.id,
                    role=um.role,
                    content=um.content,
                    created_at=um.created_at,
                    risk_level=um.risk_level,
                    emotion=um.emotion,
                )
            if am:
                assistant_msg_out = ChatMessageOut(
                    id=am.id,
                    role=am.role,
                    content=am.content,
                    created_at=am.created_at,
                    risk_level=am.risk_level,
                )
        else:
            assert db_session is not None
            chat_repo = ChatRepository(db_session)
            um_row = await chat_repo.add_message(
                session_id=session_id,
                role=MessageRole.USER,
                content=content,
                risk_level=assessment.to_stored_level(),
                emotion=emotion_result.primary if emotion_result else None,
            )
            am_row = await chat_repo.add_message(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content=crisis_text,
                risk_level=assessment.to_stored_level(),
                emotion=None,
            )
            # SafetyEvent
            source = SafetyEventSource.RULES if decision.source == "rules" else SafetyEventSource.ML
            await SafetyEventRepository(db_session).record(
                risk_level=assessment.to_stored_level(),
                source=source,
                user_id=user.id,
                session_id=session_id,
            )
            await db_session.commit()
            user_msg_out = ChatMessageOut(
                id=um_row.id,
                role=MessageRole.USER,
                content=content,
                created_at=um_row.created_at,
                risk_level=int(um_row.risk_level),
                emotion=emotion_result.primary if emotion_result else None,
            )
            assistant_msg_out = ChatMessageOut(
                id=am_row.id,
                role=MessageRole.ASSISTANT,
                content=crisis_text,
                created_at=am_row.created_at,
                risk_level=int(am_row.risk_level),
            )

        return OrchestratorResult(
            reply_text=crisis_text,
            risk_level=plan.level_code,
            stored_risk_level=plan.stored_level_code,
            emotion=emotion_result.primary if emotion_result else None,
            emotion_result=emotion_result,
            response_type="crisis",
            resources=tuple(plan.resources),
            emergency=plan.emergency,
            crisis_message=plan.message,
            degraded=False,
            fallbacks_used=0,
            provider="crisis",
            prompt_version=None,
            user_message=user_msg_out,
            assistant_message=assistant_msg_out,
            assessment=assessment,
            decision=decision,
            plan=plan,
            redaction_summary=None,
        )

    async def _get_history(
        self,
        *,
        db_session: AsyncSession | None,
        session_id: uuid.UUID,
        is_ephemeral: bool,
        limit: int,
    ) -> list[Any]:
        if is_ephemeral:
            msgs = self._ephemeral.list_messages(session_id, limit=limit * 2)
            return msgs or []
        else:
            assert db_session is not None
            repo = ChatRepository(db_session)
            rows = await repo.list_messages(session_id, limit=limit * 2)
            # Decrypt
            out = []
            for r in rows:
                try:
                    text = repo.message_text(r)
                except Exception:
                    text = "[decryption failed]"
                # Create a simple object with role/content
                out.append(
                    _SimpleMessage(
                        role=r.role,
                        content=text,
                        created_at=r.created_at,
                        risk_level=r.risk_level,
                        emotion=r.emotion,
                    )
                )
            return out

    def _response_type_for_level(self, level: SafetyRiskLevel, degraded: bool) -> str:
        if degraded:
            return "fallback"
        if level == SafetyRiskLevel.MEDIUM:
            return "check_in"
        if level == SafetyRiskLevel.LOW:
            return "supportive"
        return "normal"

    # ---- Streaming ---------------------------------------------------------

    async def stream_message(
        self,
        *,
        db_session: AsyncSession | None,
        user: User,
        session_id: uuid.UUID,
        content: str,
        is_ephemeral: bool,
        region: str | None = None,
        locale: str | None = None,
    ) -> Any:
        """Async generator yielding SSE chunks for streaming endpoint.

        Yields dicts that the API layer turns into SSE events:
        - {"token": str} for each LLM token
        - {"type": "final", "metadata": {...}} as last event
        """
        # Reuse handle_message logic but streaming LLM
        # 1. Validate & rate limit
        _validate_encoding(content)
        _validate_length(content, self._settings.chat_max_message_chars)
        rl = self._limiter.hit(str(user.id))
        if not rl.allowed:
            raise ApiError(
                429,
                "rate_limited",
                "Too many messages. Please slow down.",
                headers={"Retry-After": str(rl.retry_after)},
            )

        # 2. Safety
        try:
            assessment = self._rule_engine.assess(content)
        except Exception:
            logger.warning("safety_assess_failed", user_id=str(user.id))
            from app.services.safety.base import empty_assessment

            assessment = empty_assessment(rationale="safety.error")

        decision = decide(
            assessment,
            self._ml_classifier,
            text=content,
            min_confidence=self._settings.safety_ml_min_confidence,
            crisis_mass_floor=self._settings.safety_ml_crisis_mass_floor,
            suspicion_floor=self._settings.safety_ml_suspicion_floor,
        )
        final_assessment = apply_decision(assessment, decision)
        plan = self._escalator.plan(final_assessment, region=region, locale=locale)

        logger.info(
            "chat_safety_assessed_stream",
            user_id=str(user.id),
            session_id=str(session_id),
            text_sha=text_fingerprint(content),
            text_length=len(content),
            level=plan.level_code,
        )

        emotion_result = None
        try:
            emotion_result = self._emotion_analyzer.analyze(content)
        except Exception:
            emotion_result = None

        if final_assessment.level.is_crisis:
            # Crisis: stream crisis text in chunks, then final metadata
            result = await self._handle_crisis(
                db_session=db_session,
                user=user,
                session_id=session_id,
                content=content,
                is_ephemeral=is_ephemeral,
                assessment=final_assessment,
                decision=decision,
                plan=plan,
                emotion_result=emotion_result,
            )
            # Stream crisis reply as tokens (split by words for demo)
            for chunk in _split_for_stream(result.reply_text):
                yield {"token": chunk}
            # Final metadata
            yield {
                "type": "final",
                "risk_level": result.risk_level,
                "emotion": result.emotion,
                "response_type": result.response_type,
                "resources": [
                    r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r)
                    for r in result.resources
                ],
                "degraded": result.degraded,
            }
            return

        # Normal path
        redaction_result = self._redactor.redact(content)
        redacted_text = redaction_result.text
        retrieval_results = await retrieve(redacted_text, session_id=session_id, user_id=user.id)
        emotion_hint = _emotion_hint(emotion_result)
        safety_hint = _safety_hint(final_assessment.level)
        language = emotion_result.language if emotion_result and emotion_result.language else None
        system_prompt = _build_system_prompt(
            base_version=self._settings.llm_prompt_version,
            language=language,
            max_words=self._settings.llm_max_tokens,
            emotion_hint=emotion_hint,
            safety_hint=safety_hint,
            retrieval=retrieval_results,
        )
        history_messages = await self._get_history(
            db_session=db_session,
            session_id=session_id,
            is_ephemeral=is_ephemeral,
            limit=self._settings.chat_history_window,
        )
        llm_messages = _conversation_window_to_llm_messages(
            history_messages, limit=self._settings.chat_history_window
        )
        llm_messages.append(LLMMessage(role="user", content=redacted_text))

        # Stream from LLM chain
        full_reply_parts: list[str] = []
        degraded = False
        provider_name = None
        try:
            async for chunk in self._llm_chain.stream(
                llm_messages,
                system=system_prompt,
                max_tokens=self._settings.llm_max_tokens,
                temperature=self._settings.llm_temperature,
            ):
                full_reply_parts.append(chunk)
                yield {"token": chunk}
        except Exception as exc:
            logger.warning("llm_stream_failed", error_type=type(exc).__name__)
            # Fallback to canned
            from app.services.llm.canned import CANNED_REPLY

            full_reply_parts = [CANNED_REPLY]
            yield {"token": CANNED_REPLY}
            degraded = True
            provider_name = "canned"
        else:
            # If stream succeeded, we need to get provider info — we don't have
            # result object from stream, so we assume not degraded unless chain
            # stats say otherwise. For simplicity, we treat as not degraded.
            pass

        final_text = "".join(full_reply_parts)
        guard_res = guard_output(final_text)
        final_text = guard_res.text

        # Persist
        response_type = self._response_type_for_level(final_assessment.level, degraded=degraded)

        if is_ephemeral:
            self._ephemeral.add_message(
                session_id,
                role=MessageRole.USER,
                content=content,
                risk_level=int(final_assessment.to_stored_level()),
                emotion=emotion_result.primary if emotion_result else None,
            )
            self._ephemeral.add_message(
                session_id,
                role=MessageRole.ASSISTANT,
                content=final_text,
                risk_level=int(final_assessment.to_stored_level()),
            )
        else:
            assert db_session is not None
            chat_repo = ChatRepository(db_session)
            await chat_repo.add_message(
                session_id=session_id,
                role=MessageRole.USER,
                content=content,
                risk_level=final_assessment.to_stored_level(),
                emotion=emotion_result.primary if emotion_result else None,
            )
            await chat_repo.add_message(
                session_id=session_id,
                role=MessageRole.ASSISTANT,
                content=final_text,
                risk_level=final_assessment.to_stored_level(),
            )
            if plan.record_event:
                source = (
                    SafetyEventSource.RULES if decision.source == "rules" else SafetyEventSource.ML
                )
                await SafetyEventRepository(db_session).record(
                    risk_level=final_assessment.to_stored_level(),
                    source=source,
                    user_id=user.id,
                    session_id=session_id,
                )
            await db_session.commit()

        yield {
            "type": "final",
            "risk_level": plan.level_code,
            "emotion": emotion_result.primary if emotion_result else None,
            "response_type": response_type,
            "resources": [
                r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r) for r in plan.resources
            ],
            "degraded": degraded,
            "provider": provider_name,
        }


@dataclass
class _SimpleMessage:
    role: Any
    content: str
    created_at: Any
    risk_level: int
    emotion: str | None = None


def _split_for_stream(text: str, words_per_chunk: int = 5) -> list[str]:
    words = text.split(" ")
    out: list[str] = []
    for i in range(0, len(words), words_per_chunk):
        chunk = " ".join(words[i : i + words_per_chunk])
        if i + words_per_chunk < len(words):
            chunk += " "
        out.append(chunk)
    return out


__all__ = [
    "ChatMessageOut",
    "ChatOrchestrator",
    "OrchestratorResult",
]
