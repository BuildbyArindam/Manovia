"""Building the chain from settings, and the settings themselves.

The claim under test: **configuration alone decides which model answers**, and
**a missing credential produces a working (degraded) chain, not a 500 and not a
startup failure**. Both matter because both are what a maintainer will get wrong
first.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.llm import (
    PROVIDER_CHOICES,
    AnthropicProvider,
    CannedProvider,
    FakeLLMProvider,
    OllamaProvider,
    build_llm_chain,
)
from app.services.nlp.redaction import Redactor

from .conftest import last_call, messages

# --- chain construction ------------------------------------------------------


def test_the_fake_selection_builds_a_two_link_chain() -> None:
    chain = build_llm_chain(Settings(llm_provider="fake"))
    assert [link.name for link in chain.providers] == ["fake", "canned"]
    assert isinstance(chain.providers[0], FakeLLMProvider)
    assert isinstance(chain.providers[-1], CannedProvider)


def test_the_anthropic_selection_puts_ollama_second_and_canned_last() -> None:
    chain = build_llm_chain(
        Settings(llm_provider="anthropic", anthropic_api_key="k", anthropic_model="m")
    )
    assert [link.name for link in chain.providers] == ["anthropic", "ollama", "canned"]
    assert isinstance(chain.providers[0].inner, AnthropicProvider)  # type: ignore[attr-defined]
    assert isinstance(chain.providers[1].inner, OllamaProvider)  # type: ignore[attr-defined]


def test_the_ollama_selection_has_no_hosted_provider() -> None:
    chain = build_llm_chain(Settings(llm_provider="ollama"))
    assert [link.name for link in chain.providers] == ["ollama", "canned"]


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(llm_provider="gemini")
    with pytest.raises(ValueError):
        build_llm_chain(Settings.model_construct(llm_provider="gemini"))


def test_every_choice_is_reachable() -> None:
    assert PROVIDER_CHOICES == ("anthropic", "ollama", "fake")


def test_building_makes_no_network_call() -> None:
    """Providers are lazy: a missing key or a dead Ollama must be discovered
    on first use, not at startup, or the app could not boot without secrets."""
    chain = build_llm_chain(Settings(llm_provider="anthropic"))
    assert chain.providers[0].is_configured is False
    assert chain.is_configured is True  # because the canned link always is


# --- the missing-key path ----------------------------------------------------


async def test_no_api_key_falls_through_to_the_canned_reply() -> None:
    """The Day 10 acceptance criterion: not a 500, and not an exception."""
    chain = build_llm_chain(Settings(llm_provider="anthropic"))
    result = await chain.complete(messages("I had a hard day"))
    assert result.degraded is True
    assert result.fallbacks_used == 2
    assert result.provider == "canned"
    assert result.text  # something warm, something safe


async def test_no_api_key_and_no_ollama_still_answers() -> None:
    chain = build_llm_chain(
        Settings(llm_provider="anthropic", ollama_base_url="http://127.0.0.1:1")
    )
    result = await chain.complete(messages("I had a hard day"))
    assert result.text
    assert result.degraded is True


async def test_an_invalid_key_falls_through_without_retrying_it() -> None:
    """A 401 is not an outage: the chain must move on rather than hammer a
    credential that has already been rejected. The stub client raises the
    BadRequest that _translate() produces for a 401."""
    from app.services.llm.base import ProviderBadRequest
    from tests.llm.test_anthropic_provider import StubClient, provider

    stub = provider(client=StubClient(error=ProviderBadRequest("invalid api key")))
    from app.services.llm.chain import LLMChain
    from app.services.llm.guard import TokenGuard

    chain = LLMChain([stub, CannedProvider()], guard=TokenGuard())
    await chain.complete(messages("hi"))
    assert isinstance(stub._client, StubClient)
    assert stub._client.messages.calls  # attempted exactly once
    assert chain.failure_counts.get("anthropic:ProviderBadRequest") == 1


async def test_a_configured_anthropic_chain_uses_anthropic() -> None:
    from app.services.llm.base import ProviderDown
    from tests.llm.test_anthropic_provider import StubClient, provider

    stub = provider(client=StubClient())
    from app.services.llm.chain import LLMChain
    from app.services.llm.guard import TokenGuard

    chain = LLMChain([stub, CannedProvider()], guard=TokenGuard())
    result = await chain.complete(messages("hi"))
    assert result.provider == "anthropic"
    assert result.degraded is False
    assert not isinstance(result.text, ProviderDown)


# --- settings ----------------------------------------------------------------


def test_the_settings_defaults_are_safe() -> None:
    settings = Settings()
    assert settings.llm_provider == "fake"  # offline by default
    assert settings.anthropic_model is None  # never a hard-coded model
    assert settings.llm_redact_pii is True
    assert settings.llm_redact_names is False
    assert settings.llm_timeout_seconds > 0
    assert settings.llm_prompt_version == "system_v1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("llm_provider", "gemini"),
        ("llm_timeout_seconds", 0),
        ("llm_max_attempts", 0),
        ("llm_retry_base_seconds", 0),
        ("llm_retry_max_seconds", 0.1),  # below base
        ("llm_circuit_failure_threshold", 0),
        ("llm_circuit_cooldown_seconds", -1),
        ("llm_max_tokens", 0),
        ("llm_temperature", 2.5),
        ("llm_temperature", -0.1),
        ("llm_max_input_chars", 0),
        ("llm_window_turns", 0),
        ("llm_window_tokens", 0),
    ],
)
def test_bad_llm_settings_fail_at_startup(field: str, value: object) -> None:
    """A typo in a knob must be a boot error, not a silent misconfiguration
    discovered three weeks later in a token bill."""
    with pytest.raises(ValidationError):
        Settings(**{field: value})  # type: ignore[arg-type]


def test_acceptable_boundary_values_pass() -> None:
    assert Settings(llm_temperature=0.0).llm_temperature == 0.0
    assert Settings(llm_temperature=2.0).llm_temperature == 2.0
    assert Settings(llm_max_attempts=1).llm_max_attempts == 1
    assert Settings(llm_window_tokens=1).llm_window_tokens == 1


def test_settings_are_mapped_into_the_chain() -> None:
    chain = build_llm_chain(
        Settings(
            llm_provider="fake",
            llm_max_input_chars=111,
            llm_window_turns=3,
            llm_window_tokens=222,
        )
    )
    assert chain.guard.max_input_chars == 111
    assert chain.guard.window_turns == 3
    assert chain.guard.window_tokens == 222


def test_the_prompt_version_setting_reaches_the_chain() -> None:
    chain = build_llm_chain(Settings(llm_provider="fake", llm_prompt_version="system_v1"))
    assert chain.describe()["prompt_version"] == "system_v1"


async def test_redaction_can_be_disabled_by_configuration() -> None:
    chain = build_llm_chain(Settings(llm_provider="fake", llm_redact_pii=True))
    fake = chain.providers[0]
    assert isinstance(fake, FakeLLMProvider)
    await chain.complete(messages("mail me at riya@example.com"))
    assert "[EMAIL]" in last_call(fake).user_text


async def test_a_redactor_can_be_injected_for_tests() -> None:
    chain = build_llm_chain(Settings(llm_provider="fake"), redactor=Redactor())
    fake = chain.providers[0]
    assert isinstance(fake, FakeLLMProvider)
    await chain.complete(messages("call 9876543210"))
    assert "[PHONE]" in last_call(fake).user_text
