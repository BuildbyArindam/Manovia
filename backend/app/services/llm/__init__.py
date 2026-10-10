"""The LLM layer: one interface, three providers, one chain that cannot fail.

Everything a caller needs is here:

    from app.services.llm import build_llm_chain
    chain = build_llm_chain(settings)
    result = await chain.complete(messages)

Design in one paragraph: :class:`~app.services.llm.base.LLMProvider` is the
contract; :mod:`anthropic_provider` (hosted), :mod:`ollama_provider` (local) and
:mod:`fake_provider` (offline, for tests) implement it;
:class:`~app.services.llm.resilience.ResilientProvider` wraps any of them in
retry + timeout + circuit breaker; :class:`~app.services.llm.chain.LLMChain`
orders them and terminates in :class:`~app.services.llm.canned.CannedProvider`.

No model id in this package has a default. Every model comes from the
environment (``ANTHROPIC_MODEL``, ``OLLAMA_MODEL``), because "which model is
answering our users?" must be answerable from configuration alone.

See [ADR 0010](../../../docs/adr/0010-llm-abstraction.md) for the decision and
its alternatives.
"""

from __future__ import annotations

import structlog

from app.core.config import Settings
from app.services.llm.anthropic_provider import AnthropicProvider
from app.services.llm.base import (
    AllProvidersFailed,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMResult,
    LLMUsage,
    ProviderBadRequest,
    ProviderDown,
    ProviderNotAvailable,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
)
from app.services.llm.canned import CANNED_REPLY, CannedProvider
from app.services.llm.chain import LLMChain
from app.services.llm.fake_provider import FakeLLMProvider
from app.services.llm.guard import TokenGuard
from app.services.llm.ollama_provider import OllamaProvider
from app.services.llm.prompts import (
    PROMPT_VERSION,
    PromptDocument,
    PromptError,
    SystemPrompt,
    describe_prompt,
    load_prompt,
    render_system_prompt,
)
from app.services.llm.resilience import (
    CircuitBreaker,
    ResilientProvider,
    RetryPolicy,
)
from app.services.nlp.redaction import Redactor

#: Providers the chain can lead with; the canned reply is always last.
PROVIDER_CHOICES: tuple[str, ...] = ("anthropic", "ollama", "fake")


def build_llm_chain(
    settings: Settings,
    *,
    redactor: Redactor | None = None,
    guard: TokenGuard | None = None,
) -> LLMChain:
    """Build the chain this deployment should use.

    Nothing here makes a network call: providers are lazy, so a missing key or
    a dead Ollama is discovered on first use as
    :class:`ProviderNotConfigured` / :class:`ProviderDown` and handled by the
    chain, not at startup.

    The chain is always ``primary → … → canned``, which is what makes
    "missing API key ⇒ fallback, not a 500" true by construction rather than by
    careful error handling in an endpoint.
    """
    choice = settings.llm_provider.strip().casefold()
    if choice not in PROVIDER_CHOICES:
        raise ValueError(f"LLM_PROVIDER must be one of {PROVIDER_CHOICES}, got {choice!r}")

    timeout = settings.llm_timeout_seconds
    links: list[LLMProvider] = []

    if choice == "anthropic":
        links.append(
            _armour(
                AnthropicProvider(
                    api_key=settings.anthropic_api_key,
                    model=settings.anthropic_model,
                    timeout_seconds=timeout,
                ),
                settings,
            )
        )
        # Local fallback second: no data leaves the machine, and it needs no key.
        links.append(
            _armour(
                OllamaProvider(
                    base_url=settings.ollama_base_url,
                    model=settings.ollama_model,
                    timeout_seconds=timeout,
                ),
                settings,
            )
        )
    elif choice == "ollama":
        links.append(
            _armour(
                OllamaProvider(
                    base_url=settings.ollama_base_url,
                    model=settings.ollama_model,
                    timeout_seconds=timeout,
                ),
                settings,
            )
        )
    else:  # "fake" — the offline test double.
        links.append(FakeLLMProvider())

    # Terminal link: cannot fail, cannot be unconfigured.
    links.append(CannedProvider())

    chain = LLMChain(
        links,
        guard=guard
        or TokenGuard(
            max_input_chars=settings.llm_max_input_chars,
            window_turns=settings.llm_window_turns,
            window_tokens=settings.llm_window_tokens,
        ),
        redactor=redactor or Redactor(),
        redact=settings.llm_redact_pii,
        prompt_version=settings.llm_prompt_version,
    )
    structlog.get_logger().info(
        "llm_chain_configured",
        primary=choice,
        chain=[link.name for link in links],
        configured=[link.is_configured for link in links],
        prompt_version=settings.llm_prompt_version,
        redact=settings.llm_redact_pii,
        timeout_seconds=timeout,
    )
    return chain


def _armour(provider: LLMProvider, settings: Settings) -> ResilientProvider:
    """Wrap a provider in jittered retry, a hard timeout and a breaker."""
    return ResilientProvider(
        provider,
        retry=RetryPolicy(
            max_attempts=settings.llm_max_attempts,
            base_seconds=settings.llm_retry_base_seconds,
            max_seconds=settings.llm_retry_max_seconds,
        ),
        breaker=CircuitBreaker(
            failure_threshold=settings.llm_circuit_failure_threshold,
            cooldown_seconds=settings.llm_circuit_cooldown_seconds,
        ),
        timeout_seconds=settings.llm_timeout_seconds,
    )


__all__ = [
    "CANNED_REPLY",
    "PROMPT_VERSION",
    "PROVIDER_CHOICES",
    "AllProvidersFailed",
    "AnthropicProvider",
    "CannedProvider",
    "CircuitBreaker",
    "FakeLLMProvider",
    "LLMChain",
    "LLMError",
    "LLMMessage",
    "LLMProvider",
    "LLMResult",
    "LLMUsage",
    "OllamaProvider",
    "PromptDocument",
    "PromptError",
    "ProviderBadRequest",
    "ProviderDown",
    "ProviderNotAvailable",
    "ProviderNotConfigured",
    "ProviderRateLimited",
    "ProviderTimeout",
    "Redactor",
    "ResilientProvider",
    "RetryPolicy",
    "SystemPrompt",
    "TokenGuard",
    "build_llm_chain",
    "describe_prompt",
    "load_prompt",
    "render_system_prompt",
]
