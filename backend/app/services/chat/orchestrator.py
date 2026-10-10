"""The chat orchestrator: the one place a user message becomes a reply.

The pipeline, in this order, for every message (Day 11 brief)::

    1. validate length/encoding; rate-limit per user          admit()
    2. input safety: rules + ML ensemble -> RiskAssessment    _triage()
         HIGH / IMMINENT -> STOP. Deterministic crisis reply.
         The LLM is not called. Message + SafetyEvent stored.  _crisis_reply()
    3. PII redaction of the text going to the LLM             _prepare()
    4. emotion analysis of the *original* text                _prepare()
    5. retrieval (stub: [] until Day 13)                      _prepare()
    6. build the prompt: system + window + emotion hint +
       safety hint + retrieval context                        _prepare()
    7. call the LLM through the provider chain                _generate()
    8. output guard (stub: pass-through until Day 14)         _guard()
    9. persist the assistant message and return               _finish()

Design rules the code below holds itself to:

* **Safety first, and fail closed.** Step 2 runs before anything that could reach
  a model. If the safety stage itself breaks there is *no verdict*, so the
  answer is a 503 — never "answer anyway".
* **The model cannot make a crisis response worse.** At HIGH/IMMINENT the model is
  never constructed into a request, let alone called. The reply is the
  pre-written, localised template from ``content/i18n``, and it is delivered even
  if storing it fails.
* **Nothing the model says is trusted.** It passes the output guard (a seam
  today) and, if it cannot answer at all, a pre-written fallback stands in. An
  LLM outage is a degraded reply, never a 500.
* **Nothing identifying goes out.** The text and the history window are redacted
  before the prompt is built; the user id never enters the prompt; if redaction
  itself fails the model is not called.
* **Never log what was said.** Log lines carry a fingerprint, a length, tiers and
  timings (AGENTS.md rule 5).

The orchestrator is built from interfaces (LLM provider, emotion analyzer,
classifier, retriever, output guard), each with a Fake, so the whole pipeline
runs offline in tests.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Final

import structlog
from starlette.concurrency import run_in_threadpool

from app.content.i18n import RenderedTemplate
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.ratelimit import InMemoryRateLimiter, RateLimiter
from app.models.enums import SafetyEventSource
from app.services.chat.conversation import Conversation
from app.services.chat.output_guard import OutputGuard, PassthroughOutputGuard
from app.services.chat.prompting import (
    build_window,
    compose_system,
    emotion_hint,
    retrieval_block,
    safety_hint,
    to_llm_messages,
)
from app.services.chat.retrieval import NullRetriever, RetrievedChunk, Retriever
from app.services.chat.types import (
    ChatMetadata,
    ChatReply,
    FinalEvent,
    ResponseType,
    StreamEvent,
    TokenEvent,
)
from app.services.chat.validation import validate_message
from app.services.llm.base import LLMMessage, LLMProvider
from app.services.llm.canned import CANNED_REPLY, split_for_stream
from app.services.llm.prompts import DEFAULT_MAX_WORDS, PROMPT_VERSION, render_system_prompt
from app.services.nlp.base import EmotionAnalyzer, EmotionResult
from app.services.nlp.redaction import Redactor
from app.services.safety.base import RiskLevel, text_fingerprint
from app.services.safety.ensemble import SOURCE_RULES, EnsembleDecision
from app.services.safety.escalation import EscalationPlan, Escalator
from app.services.safety.ml_classifier import SafetyClassifier
from app.services.safety.pipeline import run_ensemble
from app.services.safety.rules import RuleEngine

#: Shown only if an escalation plan somehow has no message at HIGH/IMMINENT. The
#: helpline loader validates every template at startup, so this is a last resort
#: that costs nothing: it names no number (those live only in ``helplines.json``).
LAST_RESORT_CRISIS_TEXT: Final = (
    "I'm really glad you told me. What you're describing needs a person, not an app. "
    "If you are in danger, please call your local emergency number now, or contact a "
    "crisis helpline. You don't have to go through this alone."
)

#: Curated rejection messages. Never an echo of the input.
RATE_LIMITED_MESSAGE: Final = "You're sending messages quite quickly. Please wait a moment."
SAFETY_UNAVAILABLE_MESSAGE: Final = (
    "I couldn't check that message safely just now, so I haven't replied. "
    "Please try again in a moment. If you are in danger, call your local emergency number."
)

_log = structlog.get_logger()


@dataclass(frozen=True)
class OrchestratorConfig:
    """Tunables, all sourced from ``Settings`` (none hard-coded in the pipeline)."""

    max_message_chars: int = 4000
    history_turns: int = 10
    max_tokens: int = 400
    temperature: float = 0.7
    prompt_version: str = PROMPT_VERSION
    max_words: int = DEFAULT_MAX_WORDS
    retrieval_limit: int = 3
    ml_min_confidence: float = 0.70
    ml_crisis_mass_floor: float = 0.30
    ml_suspicion_floor: float = 0.25
    rate_limit_enabled: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> OrchestratorConfig:
        return cls(
            max_message_chars=settings.chat_max_message_chars,
            history_turns=settings.chat_history_turns,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            prompt_version=settings.llm_prompt_version,
            ml_min_confidence=settings.safety_ml_min_confidence,
            ml_crisis_mass_floor=settings.safety_ml_crisis_mass_floor,
            ml_suspicion_floor=settings.safety_ml_suspicion_floor,
            rate_limit_enabled=settings.rate_limit_enabled,
        )


@dataclass(frozen=True)
class AdmittedMessage:
    """A message that passed step 1: valid, normalised, and within the user's budget.

    The only way to run the rest of the pipeline is to hold one of these, so
    "forgot to validate / rate-limit" is a type error, not a code-review catch.
    """

    text: str
    user_id: uuid.UUID
    #: Hints for the pre-written reply: region picks the helplines, locale the language.
    region: str | None = None
    locale: str | None = None


@dataclass
class _Turn:
    """Everything the LLM stage needs, built by steps 2-6."""

    message: AdmittedMessage
    plan: EscalationPlan
    emotion: EmotionResult | None
    #: ``None`` when redaction failed: the model must not be called.
    messages: list[LLMMessage] | None
    system: str
    started: float


class ChatOrchestrator:
    """Runs the nine-step pipeline over one message at a time."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        engine: RuleEngine,
        classifier: SafetyClassifier,
        escalator: Escalator,
        analyzer: EmotionAnalyzer,
        redactor: Redactor,
        retriever: Retriever | None = None,
        output_guard: OutputGuard | None = None,
        limiter: RateLimiter | None = None,
        config: OrchestratorConfig | None = None,
        rate_limit_per_minute: int = 30,
    ) -> None:
        self._llm = llm
        self._engine = engine
        self._classifier = classifier
        self._escalator = escalator
        self._analyzer = analyzer
        self._redactor = redactor
        self._retriever: Retriever = retriever or NullRetriever()
        self._guard: OutputGuard = output_guard or PassthroughOutputGuard()
        self._config = config or OrchestratorConfig()
        self._limiter: RateLimiter = limiter or InMemoryRateLimiter(rate_limit_per_minute)
        # Fail at construction, not on the first message, if the prompt is bad.
        render_system_prompt(version=self._config.prompt_version, max_words=self._config.max_words)

    @property
    def llm(self) -> LLMProvider:
        return self._llm

    @property
    def config(self) -> OrchestratorConfig:
        return self._config

    # ------------------------------------------------------------------ #
    # Step 1 — validate, rate-limit                                        #
    # ------------------------------------------------------------------ #

    def admit(
        self,
        *,
        user_id: uuid.UUID,
        text: str,
        region: str | None = None,
        locale: str | None = None,
    ) -> AdmittedMessage:
        """Validate length/encoding, then charge the user's rate-limit budget.

        Raises a curated ``ApiError``: 422 (``message_empty`` / ``message_too_long``
        / ``message_encoding``) or 429 (``rate_limited``, with ``Retry-After``).
        Validation comes first so a malformed request costs no budget.
        """
        clean = validate_message(text, max_chars=self._config.max_message_chars)
        if self._config.rate_limit_enabled:
            outcome = self._limiter.hit(str(user_id))
            if not outcome.allowed:
                raise ApiError(
                    429,
                    "rate_limited",
                    RATE_LIMITED_MESSAGE,
                    headers={"Retry-After": str(outcome.retry_after)},
                )
        return AdmittedMessage(text=clean, user_id=user_id, region=region, locale=locale)

    # ------------------------------------------------------------------ #
    # Public entry points                                                  #
    # ------------------------------------------------------------------ #

    async def respond(self, message: AdmittedMessage, conversation: Conversation) -> ChatReply:
        """Run the pipeline and return the whole reply (the JSON endpoint)."""
        started = time.perf_counter()
        plan, decision = await self._triage(message)
        if not plan.allow_llm:
            reply = await self._crisis_reply(message, conversation, plan, decision)
            self._log_turn(message, plan, reply, started, streamed=False)
            return reply

        turn = await self._prepare(message, conversation, plan, decision, started)
        text, template = await self._generate(turn)
        if not template:
            text, template = await self._guard_output(text, plan)
        reply = await self._finish(turn, conversation, text, template=template)
        self._log_turn(message, plan, reply, started, streamed=False)
        return reply

    async def stream(
        self, message: AdmittedMessage, conversation: Conversation
    ) -> AsyncIterator[StreamEvent]:
        """Run the pipeline, yielding ``TokenEvent`` s and one closing ``FinalEvent``.

        A crisis turn yields its pre-written text as a single token event and
        never touches the model. Otherwise tokens are forwarded live — unless the
        output guard says it needs the whole reply, in which case the stream is
        buffered, checked, and only then released (see :mod:`output_guard`).

        ``FinalEvent.reply`` is authoritative. If it differs from the tokens
        already sent (a guard rewrite, or a failure part-way), ``replaced`` is
        true and the client must swap its text for it.
        """
        started = time.perf_counter()
        plan, decision = await self._triage(message)
        if not plan.allow_llm:
            reply = await self._crisis_reply(message, conversation, plan, decision)
            self._log_turn(message, plan, reply, started, streamed=True)
            yield TokenEvent(reply.reply)
            yield FinalEvent(reply=reply, replaced=False)
            return

        turn = await self._prepare(message, conversation, plan, decision, started)
        buffered = self._guard.requires_full_text
        sent: list[str] = []  # tokens the client has actually been given
        pieces: list[str] = []  # everything the model produced
        failed = turn.messages is None

        if not failed:
            assert turn.messages is not None
            try:
                async for chunk in self._llm.stream(
                    turn.messages,
                    system=turn.system,
                    max_tokens=self._config.max_tokens,
                    temperature=self._config.temperature,
                ):
                    if not chunk:
                        continue
                    pieces.append(chunk)
                    if not buffered:
                        sent.append(chunk)
                        yield TokenEvent(chunk)
            except Exception as exc:
                failed = True
                _log.warning("chat_llm_stream_failed", error_type=type(exc).__name__)

        text = "".join(pieces)
        if failed or not text.strip():
            text, template = CANNED_REPLY, True
        else:
            template = text == CANNED_REPLY  # the chain's terminal link answered
            if not template:
                text, template = await self._guard_output(text, plan)

        # Release what the client has not seen. Nothing was sent when the stream
        # was buffered for the guard, or when the model never produced a token:
        # either way the final text goes out now. If live tokens *were* sent and
        # the final text differs (guard rewrite, mid-stream failure) the client
        # is told to replace what it rendered.
        replaced = False
        if not sent:
            for part in split_for_stream(text):
                sent.append(part)
                yield TokenEvent(part)
        else:
            replaced = "".join(sent) != text

        suffix = self._check_in_suffix(plan)
        if suffix:
            sent.append(suffix)
            yield TokenEvent(suffix)

        reply = await self._finish(turn, conversation, text, template=template)
        self._log_turn(message, plan, reply, started, streamed=True)
        yield FinalEvent(reply=reply, replaced=replaced)

    # ------------------------------------------------------------------ #
    # Step 2 — input safety                                                #
    # ------------------------------------------------------------------ #

    async def _triage(self, message: AdmittedMessage) -> tuple[EscalationPlan, EnsembleDecision]:
        """Rules + ML ensemble, then the escalation plan. Fails closed."""
        try:
            assessment, decision = await run_ensemble(
                message.text,
                None,
                self._engine,
                self._classifier,
                min_confidence=self._config.ml_min_confidence,
                crisis_mass_floor=self._config.ml_crisis_mass_floor,
                suspicion_floor=self._config.ml_suspicion_floor,
            )
            plan = self._escalator.plan(assessment, region=message.region, locale=message.locale)
        except Exception as exc:
            # No verdict means no reply: answering without the safety check is
            # the one thing this module must never do.
            _log.error(
                "chat_safety_unavailable",
                error_type=type(exc).__name__,
                text_sha=text_fingerprint(message.text),
            )
            raise ApiError(503, "safety_unavailable", SAFETY_UNAVAILABLE_MESSAGE) from exc
        return plan, decision

    async def _crisis_reply(
        self,
        message: AdmittedMessage,
        conversation: Conversation,
        plan: EscalationPlan,
        decision: EnsembleDecision,
    ) -> ChatReply:
        """The deterministic response. The model is not involved in any way.

        Persistence is best-effort *on this path only*: if the database is down,
        the person still gets the crisis response — the one reply that must never
        depend on a dependency.
        """
        template = plan.message
        text = _plain_text(template) if template is not None else LAST_RESORT_CRISIS_TEXT
        risk = plan.assessment.to_stored_level()
        message_id: uuid.UUID | None = None
        persisted = False
        try:
            await conversation.store_user_turn(
                message.text,
                risk=risk,
                emotion=None,
                safety_event=_event_source(decision),
            )
            message_id = await conversation.store_assistant_turn(text, risk=risk)
            persisted = conversation.stores_turns
        except Exception as exc:
            _log.error("chat_crisis_persist_failed", error_type=type(exc).__name__)

        return ChatReply(
            session_id=conversation.session_id,
            message_id=message_id,
            reply=text,
            metadata=ChatMetadata(
                risk_level=plan.level_code,
                emotion=None,
                response_type=ResponseType.CRISIS,
                resources=list(plan.resources),
                emergency=plan.emergency,
                crisis=template,
                persisted=persisted,
                about_someone_else=plan.about_someone_else,
                locale=plan.locale,
                region=plan.region,
            ),
        )

    # ------------------------------------------------------------------ #
    # Steps 3-6 — redact, emotion, retrieval, prompt                       #
    # ------------------------------------------------------------------ #

    async def _prepare(
        self,
        message: AdmittedMessage,
        conversation: Conversation,
        plan: EscalationPlan,
        decision: EnsembleDecision,
        started: float,
    ) -> _Turn:
        # Prior turns, read *before* this message is stored so it is not echoed.
        window = build_window(await conversation.history(self._config.history_turns))

        # Step 4 — emotion, from the ORIGINAL text (redaction would blunt it).
        emotion = await self._analyse_emotion(message.text)

        # Step 3 — redact everything that will leave the process.
        redacted = await self._redact([message.text, *(turn.text for turn in window.messages)])

        # The user's turn is stored now (risk and emotion are known), so it
        # survives even if the client disconnects mid-reply.
        await conversation.store_user_turn(
            message.text,
            risk=plan.assessment.to_stored_level(),
            emotion=emotion.primary if emotion is not None else None,
            safety_event=_event_source(decision) if plan.record_event else None,
        )

        if redacted is None:
            return _Turn(message, plan, emotion, None, "", started)

        # Step 5 — retrieval over the redacted query.
        chunks = await self._retrieve(redacted[0])

        # Step 6 — the prompt.
        base = render_system_prompt(
            version=self._config.prompt_version,
            language=emotion.language if emotion is not None else None,
            max_words=self._config.max_words,
        )
        system = compose_system(
            base.text,
            emotion=emotion_hint(emotion),
            safety=safety_hint(plan.level, recent_crisis=window.recent_crisis),
            retrieval=retrieval_block(chunks),
        )
        messages = to_llm_messages(window, redacted[1:], redacted[0])
        return _Turn(message, plan, emotion, messages, system, started)

    async def _analyse_emotion(self, text: str) -> EmotionResult | None:
        try:
            return await run_in_threadpool(self._analyzer.analyze, text)
        except Exception as exc:  # emotion is a tone cue; never a reason to fail
            _log.warning("chat_emotion_failed", error_type=type(exc).__name__)
            return None

    async def _redact(self, texts: Sequence[str]) -> list[str] | None:
        """Redact all outbound text. ``None`` means redaction failed: send nothing."""

        def _run() -> list[str]:
            return [self._redactor.redact(text).text for text in texts]

        try:
            return await run_in_threadpool(_run)
        except Exception as exc:
            _log.error("chat_redaction_failed", error_type=type(exc).__name__)
            return None

    async def _retrieve(self, query: str) -> list[RetrievedChunk]:
        try:
            return await self._retriever.retrieve(query, limit=self._config.retrieval_limit)
        except Exception as exc:  # reference material is optional
            _log.warning("chat_retrieval_failed", error_type=type(exc).__name__)
            return []

    # ------------------------------------------------------------------ #
    # Steps 7-9 — LLM, output guard, persist                               #
    # ------------------------------------------------------------------ #

    async def _generate(self, turn: _Turn) -> tuple[str, bool]:
        """Call the model. Returns ``(text, is_template)``; never raises."""
        if turn.messages is None:
            return CANNED_REPLY, True
        try:
            result = await self._llm.complete(
                turn.messages,
                system=turn.system,
                max_tokens=self._config.max_tokens,
                temperature=self._config.temperature,
            )
        except Exception as exc:
            _log.warning("chat_llm_failed", error_type=type(exc).__name__)
            return CANNED_REPLY, True
        text = result.text
        if not text.strip():
            return CANNED_REPLY, True
        return text, result.provider == "canned"

    async def _guard_output(self, text: str, plan: EscalationPlan) -> tuple[str, bool]:
        """Step 8. Returns ``(text, is_template)``; a crashing guard fails closed."""
        try:
            verdict = await self._guard.check(text, risk=plan.level)
        except Exception as exc:
            _log.error("chat_output_guard_failed", error_type=type(exc).__name__)
            return CANNED_REPLY, True
        if verdict.passed:
            return verdict.text, False
        _log.warning("chat_output_guard_replaced", reason=verdict.reason)
        return verdict.text, True

    def _check_in_suffix(self, plan: EscalationPlan) -> str:
        """The deterministic MEDIUM check-in line, appended after the model's text.

        The prompt *asks* the model for a gentle check-in; this makes sure one
        exists even if the model ignores the hint, or is down. It is the last
        body paragraph of the reviewed ``check_in.medium`` template ("We can keep
        talking. If hearing a person's voice would help, the helplines below…").
        """
        template = _check_in_template(plan)
        if template is None or not template.body:
            return ""
        return "\n\n" + template.body[-1]

    async def _finish(
        self, turn: _Turn, conversation: Conversation, text: str, *, template: bool
    ) -> ChatReply:
        """Append the check-in, store the assistant turn, build the metadata."""
        plan = turn.plan
        check_in = _check_in_template(plan)
        suffix = self._check_in_suffix(plan)
        final_text = text + suffix

        message_id: uuid.UUID | None = None
        persisted = False
        try:
            message_id = await conversation.store_assistant_turn(
                final_text, risk=plan.assessment.to_stored_level()
            )
            persisted = conversation.stores_turns
        except Exception as exc:
            # The reply exists; losing it because the write failed would punish
            # the person for our outage. The metadata says it was not stored.
            _log.error("chat_assistant_persist_failed", error_type=type(exc).__name__)

        if template:
            response_type = ResponseType.FALLBACK
        elif check_in is not None:
            response_type = ResponseType.CHECK_IN
        else:
            response_type = ResponseType.NORMAL

        return ChatReply(
            session_id=conversation.session_id,
            message_id=message_id,
            reply=final_text,
            metadata=ChatMetadata(
                risk_level=plan.level_code,
                emotion=turn.emotion.primary if turn.emotion is not None else None,
                response_type=response_type,
                resources=list(plan.resources),
                check_in=check_in,
                degraded=template,
                persisted=persisted,
                about_someone_else=plan.about_someone_else,
                locale=plan.locale,
                region=plan.region,
            ),
        )

    # ------------------------------------------------------------------ #
    # Logging                                                              #
    # ------------------------------------------------------------------ #

    def _log_turn(
        self,
        message: AdmittedMessage,
        plan: EscalationPlan,
        reply: ChatReply,
        started: float,
        *,
        streamed: bool,
    ) -> None:
        """One metadata line per turn. No text, no ids that name a person."""
        meta = reply.metadata
        _log.info(
            "chat_turn",
            text_sha=text_fingerprint(message.text),
            text_length=len(message.text),
            risk_level=meta.risk_level,
            emotion=meta.emotion,
            response_type=meta.response_type.value,
            degraded=meta.degraded,
            persisted=meta.persisted,
            streamed=streamed,
            llm_called=meta.response_type is not ResponseType.CRISIS,
            allow_llm=plan.allow_llm,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )


# ---------------------------------------------------------------------- #
# Helpers                                                                  #
# ---------------------------------------------------------------------- #


def _event_source(decision: EnsembleDecision) -> SafetyEventSource:
    """Which detector earned the level, in ``safety_events`` vocabulary."""
    return SafetyEventSource.RULES if decision.source == SOURCE_RULES else SafetyEventSource.ML


def _check_in_template(plan: EscalationPlan) -> RenderedTemplate | None:
    """The MEDIUM check-in copy, only when the model answers at MEDIUM."""
    if plan.allow_llm and plan.level == RiskLevel.MEDIUM:
        return plan.message
    return None


def _plain_text(template: RenderedTemplate) -> str:
    """A plain-text rendering of a crisis template, for clients without the card.

    Title, body, the emergency instruction and the closing line. Helplines are
    deliberately *not* inlined: they travel as structured ``resources`` so the
    card can render them as tappable links, and they live only in
    ``helplines.json``.
    """
    parts = [template.title, *template.body]
    if template.emergency_instruction:
        parts.append(template.emergency_instruction)
    if template.closing:
        parts.append(template.closing)
    return "\n\n".join(parts)


__all__ = [
    "LAST_RESORT_CRISIS_TEXT",
    "AdmittedMessage",
    "ChatOrchestrator",
    "OrchestratorConfig",
]
