"""The fallback chain: primary → Ollama → canned reply.

The ordering is a safety decision, not a performance one. A person mid-conversation
must never see an error page because a vendor had a bad minute, so the chain
ends in a link that **cannot fail**: :class:`~app.services.llm.canned.CannedProvider`.

What the chain owns, in order, once per request:

1. **the system prompt** — rendered from the versioned file
   (:mod:`app.services.llm.prompts`) unless the caller supplied one. The caller
   passing ``system=None`` means "use the product prompt", which is the common
   case and the one that must always be safe.
2. **redaction** — every outbound message goes through
   :class:`~app.services.nlp.redaction.Redactor`. The reversible map is held in
   a local variable for the duration of the call and then dropped; it is never
   stored, and it is never needed, because the model's reply contains only
   placeholders, which is exactly what we want.
3. **the token/cost guard** — truncation and conversation windowing
   (:mod:`app.services.llm.guard`).
4. **the providers, in order** — the first one that answers wins. A provider
   that is not configured is skipped without spending any of the deadline.

The result carries ``degraded`` and ``fallbacks_used`` so the API layer can
decide whether to tell the user the companion is on a spare wheel. Silently
substituting a canned reply as if it were the model's answer would be a lie
about where the words came from.

``AllProvidersFailed`` can only be raised by a chain built *without* the canned
terminal link, which is a construction mistake; the default chain cannot fail.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Final

import structlog

from app.services.llm.base import (
    AllProvidersFailed,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMResult,
)
from app.services.llm.guard import GuardReport, TokenGuard
from app.services.llm.prompts import (
    DEFAULT_MAX_WORDS,
    PROMPT_VERSION,
    SystemPrompt,
    render_system_prompt,
)
from app.services.nlp.redaction import Redactor

#: Name reported in results for a successful chain call.
CHAIN_NAME: Final[str] = "chain"


class LLMChain(LLMProvider):
    """Ordered providers plus the prompt, redaction and budget guards."""

    name = CHAIN_NAME

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        guard: TokenGuard | None = None,
        redactor: Redactor | None = None,
        redact: bool = True,
        prompt_version: str = PROMPT_VERSION,
        max_words: int = DEFAULT_MAX_WORDS,
    ) -> None:
        if not providers:
            raise ValueError("a chain needs at least one provider")
        self._providers: tuple[LLMProvider, ...] = tuple(providers)
        self._guard = guard or TokenGuard()
        self._redactor = redactor
        self._redact = redact
        self._prompt_version = prompt_version
        self._max_words = max_words
        #: How many requests the leading provider could not serve.
        self.degraded_count = 0
        #: Per-provider failure counts, keyed by provider name.
        self.failure_counts: dict[str, int] = {}

    @property
    def providers(self) -> tuple[LLMProvider, ...]:
        return self._providers

    @property
    def guard(self) -> TokenGuard:
        return self._guard

    @property
    def model_id(self) -> str | None:
        return self._providers[0].model_id

    @property
    def is_configured(self) -> bool:
        """True when at least one link can answer — the canned one always can."""
        return any(provider.is_configured for provider in self._providers)

    async def aclose(self) -> None:
        for provider in self._providers:
            await provider.aclose()

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "model": self.model_id,
            "configured": self.is_configured,
            "prompt_version": self._prompt_version,
            "redact": self._redact,
            "guard": self._guard.describe(),
            "chain": [provider.describe() for provider in self._providers],
            "degraded": self.degraded_count,
            "failures": dict(self.failure_counts),
        }

    # --- the interface ---

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> LLMResult:
        request = self._prepare(messages, system=system)
        last_error: LLMError | None = None

        for index, provider in enumerate(self._providers):
            if not provider.is_configured:
                self._note_failure(provider.name, "not_configured")
                continue
            try:
                result = await provider.complete(
                    request.messages,
                    system=request.system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except LLMError as exc:
                last_error = exc
                self._note_failure(provider.name, type(exc).__name__)
                structlog.get_logger().warning(
                    "llm_chain_provider_failed",
                    provider=provider.name,
                    position=index,
                    error_type=type(exc).__name__,
                )
                continue
            return self._finish(result, position=index, request=request)

        raise AllProvidersFailed(
            f"every provider failed (last: {type(last_error).__name__ if last_error else 'none'})"
        )

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream from the first provider that produces a token.

        Each provider is tried in turn; a failure *before the first token*
        moves to the next one, which is the case that matters (the provider is
        down). A failure mid-stream is surfaced rather than silently restarted,
        because restarting would repeat text the user has already read.
        """
        request = self._prepare(messages, system=system)
        last_error: LLMError | None = None

        for index, provider in enumerate(self._providers):
            if not provider.is_configured:
                self._note_failure(provider.name, "not_configured")
                continue
            yielded = False
            try:
                async for chunk in provider.stream(
                    request.messages,
                    system=request.system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    if index > 0 and not yielded:
                        self.degraded_count += 1
                    yielded = True
                    yield chunk
            except LLMError as exc:
                last_error = exc
                self._note_failure(provider.name, type(exc).__name__)
                if yielded:
                    raise
                structlog.get_logger().warning(
                    "llm_chain_provider_failed",
                    provider=provider.name,
                    position=index,
                    error_type=type(exc).__name__,
                )
                continue
            return

        raise AllProvidersFailed(
            f"every provider failed (last: {type(last_error).__name__ if last_error else 'none'})"
        )

    # --- internals ---

    def _prepare(self, messages: Sequence[LLMMessage], *, system: str | None) -> _PreparedRequest:
        """Redact, then window, then render the prompt."""
        redacted = self._redact_messages(messages)
        system_text = system if system is not None else self._render_prompt(redacted).text
        guarded = self._guard.guard(redacted, system_text)
        return _PreparedRequest(
            messages=guarded.messages,
            system=guarded.system,
            report=guarded.report,
            prompt_version=self._prompt_version if system is None else None,
        )

    def _redact_messages(self, messages: Sequence[LLMMessage]) -> list[LLMMessage]:
        """Redact every outbound message.

        The reversible map is intentionally discarded here: nothing downstream
        needs the original, and keeping it would put the one copy of the user's
        identifiers in a place that outlives the call.
        """
        if not self._redact or self._redactor is None:
            return list(messages)
        out: list[LLMMessage] = []
        for message in messages:
            out.append(LLMMessage(role=message.role, content=self._redact_text(message.content)))
        return out

    def _redact_text(self, content: str) -> str:
        """Redact one message and drop the reversible map immediately.

        :meth:`Redactor.redact` returns a map of placeholder → original. It is
        built for the one legitimate case (restoring the user's own words in
        text that never left the process) and dropped here for every other one:
        nothing downstream needs the originals, and keeping the map alive would
        leave the only copy of a person's identifiers in memory after the call.
        """
        assert self._redactor is not None  # narrowed by the caller
        result = self._redactor.redact(content)
        text = result.text
        del result
        return text

    def _render_prompt(self, messages: Sequence[LLMMessage]) -> SystemPrompt:
        """Render the versioned prompt in the language the person wrote in."""
        language = _detect_language(messages)
        return render_system_prompt(
            version=self._prompt_version, language=language, max_words=self._max_words
        )

    def _note_failure(self, provider: str, reason: str) -> None:
        key = f"{provider}:{reason}"
        self.failure_counts[key] = self.failure_counts.get(key, 0) + 1

    def _finish(self, result: LLMResult, *, position: int, request: _PreparedRequest) -> LLMResult:
        raw = dict(result.raw or {})
        raw["chain_position"] = position
        raw["guard"] = request.report.as_dict()
        degraded = position > 0
        if degraded:
            self.degraded_count += 1
        return result.model_copy(
            update={
                "degraded": degraded or result.degraded,
                "fallbacks_used": position,
                "prompt_version": request.prompt_version or result.prompt_version,
                "raw": raw,
            }
        )


class _PreparedRequest:
    """The guarded request, plus the metadata needed to annotate the result."""

    __slots__ = ("messages", "prompt_version", "report", "system")

    def __init__(
        self,
        *,
        messages: tuple[LLMMessage, ...],
        system: str | None,
        report: GuardReport,
        prompt_version: str | None,
    ) -> None:
        self.messages = messages
        self.system = system
        self.report = report
        self.prompt_version = prompt_version


def _detect_language(messages: Sequence[LLMMessage]) -> str:
    """The language of the newest user turn, for the ``{language}`` placeholder.

    Detection is best-effort and never fatal: if it raises or finds nothing the
    prompt falls back to English, because a failed language guess must not stop
    somebody from getting a reply.
    """
    text = ""
    for message in reversed(messages):
        if message.role == "user":
            text = message.content
            break
    if not text:
        return ""
    try:
        from app.services.nlp.language import detect_language

        info = detect_language(text)
    except Exception:  # pragma: no cover - detection is a best effort
        return ""
    language = getattr(info, "lang", None)
    if not isinstance(language, str) or not language.strip():
        return ""
    code = language.strip().casefold()
    # "other" is what detection says when it has no idea (short or mixed text).
    # Returning it would put "reply in other" in the prompt, so it is folded to
    # "" and the prompt falls back to "the language they wrote in".
    return "" if code == "other" else code
