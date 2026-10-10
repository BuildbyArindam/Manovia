"""The LLM contract, the message/result shapes, and the typed failures.

Everything that talks to a model — real or faked — implements
:class:`LLMProvider` and returns an :class:`LLMResult`. Two things about this
module are load-bearing:

**The interface is async and streaming is a first-class method.** A wellbeing
companion is read one token at a time; a provider that can only ``complete`` is
usable but second-rate, so ``stream`` is abstract rather than "return the whole
thing as one chunk". Providers that genuinely cannot stream still implement it
by yielding the finished text once (:class:`~app.services.llm.canned` does
exactly that).

**Failures are typed, not strings.** The retry/breaker/fallback machinery in
:mod:`app.services.llm.resilience` and :mod:`app.services.llm.chain` decides
what to do from the *class* of the exception:

===================================  ========  ==============================
error                                retry?    what the chain does
===================================  ========  ==============================
:class:`ProviderTimeout`             yes       next attempt, then next provider
:class:`ProviderRateLimited`         yes*      waits ``retry_after``, then next
:class:`ProviderDown`                yes       next attempt, then next provider
:class:`ProviderBadRequest`          no        straight to the next provider
:class:`ProviderNotConfigured`       no        straight to the next provider
===================================  ========  ==============================

``*`` a rate-limit is retried only if the provider told us how long to wait and
that wait fits inside the request's remaining deadline; hammering a 429 that
just arrived is how you earn a longer one.

No error here ever carries message text. A provider failure is logged with a
fingerprint and a length — see :mod:`app.services.nlp.redaction` and AGENTS.md
safety rule 5.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Who said it. Deliberately narrow: there is no ``system`` role here because
#: the system prompt is owned by :mod:`app.services.llm.prompts` and passed as
#: its own argument, so it can be versioned and swapped without rewriting the
#: message list. No ``tool`` role either — this product has no tools.
Role = Literal["user", "assistant"]
ROLES: tuple[str, ...] = ("user", "assistant")


class LLMMessage(BaseModel):
    """One turn of a conversation."""

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str

    @field_validator("content")
    @classmethod
    def _content_is_not_none(cls, value: str) -> str:
        # An empty assistant turn is legal in some APIs and fatal in others;
        # normalising to "" here means the guard and the providers never have
        # to special-case None.
        return value or ""


class LLMUsage(BaseModel):
    """Token accounting. ``None`` means the provider did not report it."""

    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class LLMResult(BaseModel):
    """What a provider returned, plus enough provenance to debug it."""

    text: str = Field(description="The reply, as it should be shown.")
    provider: str = Field(
        description="Which provider produced it (anthropic, ollama, fake, canned)."
    )
    model: str | None = Field(default=None, description="Model id, if the provider names one.")
    finish_reason: str | None = Field(default=None, description="Provider stop reason, if given.")
    usage: LLMUsage | None = Field(default=None, description="Token accounting, when reported.")
    latency_ms: float = Field(default=0.0, ge=0.0, description="Wall-clock duration of the call.")
    # --- Degradation and provenance ---
    #: True when this answer did *not* come from the leading provider. The
    #: caller uses it to decide whether to tell the user the companion is
    #: running on a spare wheel.
    degraded: bool = Field(default=False)
    #: How many providers in the chain were tried and failed before this one.
    fallbacks_used: int = Field(default=0, ge=0)
    prompt_version: str | None = Field(default=None, description="Which system prompt was used.")
    raw: dict[str, Any] | None = Field(
        default=None,
        description="Provider-specific extras. Never message text — metadata only.",
    )

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


# --- Errors -----------------------------------------------------------------


class LLMError(Exception):
    """Base class for every failure this package raises on purpose.

    Carries no message text: an exception can end up in a log line or a trace
    report, and user text must never travel there (AGENTS.md safety rule 5).
    """

    #: Whether retrying the *same* provider could plausibly succeed.
    retryable: bool = False

    def __init__(self, message: str = "", **details: object) -> None:
        super().__init__(message or type(self).__name__)
        self.message = message or type(self).__name__
        self.details: dict[str, object] = dict(details)


class ProviderTimeout(LLMError):
    """The call exceeded its deadline. Retryable — it may just be slow."""

    retryable = True


class ProviderRateLimited(LLMError):
    """429 (or an equivalent). Retryable only within ``retry_after``."""

    retryable = True

    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(message, retry_after=retry_after)
        self.retry_after = retry_after


class ProviderDown(LLMError):
    """5xx, connection refused, DNS failure, reset stream. Retryable."""

    retryable = True


class ProviderBadRequest(LLMError):
    """4xx that retrying will not fix. Not retryable."""

    retryable = False


class ProviderNotConfigured(LLMError):
    """No API key, no model id, no base URL — the provider cannot be used.

    This is the *expected* state on a developer machine and in CI: it is not an
    error, it is the reason the chain moves on to the next provider.
    """

    retryable = False


class ProviderNotAvailable(LLMError):
    """The provider's SDK is not installed (see the ``llm`` extra)."""

    retryable = False


class AllProvidersFailed(LLMError):
    """Every link in the chain failed, including the canned responder.

    Raised only when :class:`~app.services.llm.chain.LLMChain` is built without
    a terminal canned provider, which is a configuration mistake rather than an
    outage. The default chain cannot raise it.
    """


# --- The interface ----------------------------------------------------------


class LLMProvider(ABC):
    """What every completion source implements.

    Implementations must not log message text, must not raise anything outside
    the :class:`LLMError` tree (wrap unexpected exceptions as
    :class:`ProviderDown`), and must not mutate the messages they are given.
    """

    #: Short stable name, used in results, logs and circuit-breaker keys.
    name: str = "abstract"

    @property
    def model_id(self) -> str | None:
        """The model behind this provider, if it names one."""
        return None

    @property
    def is_configured(self) -> bool:
        """False when the provider cannot be used at all (no key, no model id).

        The chain checks this *before* spending a deadline on it.
        """
        return True

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> LLMResult:
        """Return one reply for a conversation."""

    @abstractmethod
    def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Return an async iterator that yields the reply in pieces.

        Declared **without** ``async`` on purpose. An ``async def`` with a
        ``yield`` is an async *generator*, and a caller cannot tell from the
        signature whether the request starts at call time or at the first
        ``__anext__`` — which is exactly the difference between "raises
        ProviderNotConfigured when you call it" and "raises when you start
        reading". Implementations that validate first do it eagerly (plain
        ``def`` returning an inner async generator); ones that only stream use
        ``async def`` with ``yield``.

        Either way the caller must exhaust or close the iterator: abandoning it
        leaks a connection.
        """

    async def aclose(self) -> None:  # noqa: B027
        """Release connections. Default: nothing to release.

        Deliberately *not* abstract: most providers hold nothing open (the Fake
        and the canned reply certainly do not), and forcing every implementer to
        write an empty method would be ceremony. Overriders close their own
        client and nothing else.
        """

    def describe(self) -> dict[str, object]:
        """Metadata for ``/api/v1/health``-style output. No secrets, ever."""
        return {
            "provider": self.name,
            "model": self.model_id,
            "configured": self.is_configured,
        }


def transcript(messages: Sequence[LLMMessage]) -> str:
    """Render messages for a *fingerprint*, never for a log line.

    Kept here so every provider that needs a request identifier produces the
    same one.
    """
    return "\n".join(f"{message.role}:{len(message.content)}" for message in messages)
