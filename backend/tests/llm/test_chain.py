"""The fallback chain: primary → Ollama → canned reply.

The claim this file exists to prove is in AGENTS.md and in the Day 10 brief:
**a provider being unusable is a degradation, never a 500.** So every test
here ends by asserting what the user got, not what the provider did.
"""

from __future__ import annotations

import pytest

from app.services.llm.base import (
    AllProvidersFailed,
    LLMMessage,
    ProviderBadRequest,
    ProviderDown,
    ProviderRateLimited,
)
from app.services.llm.canned import CANNED_REPLY, CannedProvider, split_for_stream
from app.services.llm.chain import LLMChain
from app.services.llm.fake_provider import FakeLLMProvider
from app.services.llm.guard import TokenGuard
from app.services.llm.prompts import PROMPT_VERSION
from app.services.nlp.redaction import Redactor

from .conftest import ScriptedProvider, collect, last_call, messages, raw


def chain(*providers: object, **kwargs: object) -> LLMChain:
    """A chain with a canned terminal link, redaction on, prompt rendering on."""
    links = [*providers, CannedProvider()]
    return LLMChain(
        links,  # type: ignore[arg-type]
        guard=TokenGuard(max_input_chars=400, window_turns=8, window_tokens=800),
        redactor=Redactor(),
        redact=True,
        **kwargs,  # type: ignore[arg-type]
    )


# --- the happy path ----------------------------------------------------------


async def test_the_leading_provider_answers_when_it_is_healthy() -> None:
    primary = FakeLLMProvider(replies=["I hear you."])
    result = await chain(primary).complete(messages("I had a hard day"))
    assert result.text == "I hear you."
    assert result.provider == "fake"
    assert result.degraded is False
    assert result.fallbacks_used == 0
    assert result.prompt_version == PROMPT_VERSION


async def test_the_system_prompt_is_the_versioned_product_prompt() -> None:
    primary = FakeLLMProvider()
    await chain(primary).complete(messages("I had a hard day today"))
    system = last_call(primary).system or ""
    assert "You are Manovia, an AI companion" in system
    assert "Never diagnose" in system
    assert "Reply in English" in system


async def test_a_caller_supplied_system_prompt_is_respected() -> None:
    """Tests and future evaluators must be able to override the prompt."""
    primary = FakeLLMProvider()
    await chain(primary).complete(messages("hello"), system="be terse")
    assert last_call(primary).system == "be terse"


# --- fallback ----------------------------------------------------------------


async def test_a_failing_primary_falls_through_to_the_next_provider() -> None:
    primary = FakeLLMProvider(error=ProviderDown("down"))
    second = FakeLLMProvider(replies=["from the spare"])
    result = await chain(primary, second).complete(messages("hello"))
    assert result.text == "from the spare"
    assert result.degraded is True
    assert result.fallbacks_used == 1
    assert primary.call_count == 1


async def test_everything_failing_still_produces_a_reply() -> None:
    """The whole point: no 500, ever."""
    result = await chain(
        FakeLLMProvider(error=ProviderDown("down")),
        FakeLLMProvider(error=ProviderRateLimited(retry_after=5)),
    ).complete(messages("hello"))
    assert result.text == CANNED_REPLY
    assert result.provider == "canned"
    assert result.degraded is True
    assert result.fallbacks_used == 2


async def test_an_unconfigured_provider_is_skipped_without_a_call() -> None:
    """A missing API key must not even spend one attempt."""
    primary = FakeLLMProvider(configured=False)
    result = await chain(primary).complete(messages("hello"))
    assert primary.call_count == 0
    assert result.text == CANNED_REPLY


async def test_a_bad_credential_falls_through_instead_of_retrying() -> None:
    """A 401 is not an outage. Hammering it would be, so the chain moves on."""
    primary = FakeLLMProvider(error=ProviderBadRequest("invalid api key"))
    second = FakeLLMProvider(replies=["here"])
    result = await chain(primary, second).complete(messages("hello"))
    assert result.text == "here"
    assert primary.call_count == 1


async def test_a_missing_key_never_raises_provider_not_configured_to_the_caller() -> None:
    """``ProviderNotConfigured`` is internal bookkeeping: the caller gets an
    answer, because the canned link is always configured."""
    broken = ScriptedProvider(name="broken", configured=False)
    result = await chain(broken).complete(messages("hello"))
    assert result.text == CANNED_REPLY
    assert broken.calls == 0  # not even attempted


async def test_degraded_count_and_failure_reasons_are_recorded() -> None:
    link = chain(FakeLLMProvider(error=ProviderDown("down")))
    await link.complete(messages("one"))
    await link.complete(messages("two"))
    assert link.degraded_count == 2
    assert link.failure_counts.get("fake:ProviderDown") == 2


async def test_streaming_falls_through_to_the_next_provider() -> None:
    primary = ScriptedProvider(ProviderDown("down"))
    spare = ScriptedProvider(["a", "b", "c"], name="spare")
    assert await collect(chain(primary, spare), "hi") == "abc"
    assert primary.stream_calls == 1
    assert spare.stream_calls == 1


async def test_streaming_falls_through_to_the_canned_reply() -> None:
    primary = ScriptedProvider(ProviderDown("down"))
    text = await collect(chain(primary), "hi")
    assert text == CANNED_REPLY


async def test_a_chain_without_a_terminal_link_raises_all_providers_failed() -> None:
    """Construction mistake, not an outage — and it is loud on purpose."""
    link = LLMChain([FakeLLMProvider(error=ProviderDown("down"))], guard=TokenGuard())
    with pytest.raises(AllProvidersFailed):
        await link.complete(messages("hello"))


async def test_a_chain_needs_at_least_one_provider() -> None:
    with pytest.raises(ValueError):
        LLMChain([], guard=TokenGuard())


# --- the canned reply --------------------------------------------------------


async def test_the_canned_reply_is_generic_and_safe() -> None:
    """It must not echo, describe or react to what the person wrote."""
    secret = "my email is riya@example.com and I feel hopeless"
    result = await chain(FakeLLMProvider(error=ProviderDown("down"))).complete(messages(secret))
    assert result.text == CANNED_REPLY
    for leak in ("riya", "example.com", "hopeless"):
        assert leak not in result.text.casefold()


async def test_the_canned_reply_points_at_human_help() -> None:
    lowered = CANNED_REPLY.casefold()
    assert "helpline" in lowered
    assert "someone you trust" in lowered
    # It says what is wrong (on our side), rather than faking comprehension.
    assert "limitation on my side" in lowered


async def test_the_canned_reply_offers_no_advice_and_promises_nothing() -> None:
    lowered = CANNED_REPLY.casefold()
    for forbidden in ("you should try", "i understand", "everything will be fine", "diagnos"):
        assert forbidden not in lowered


async def test_canned_streaming_reassembles_into_exactly_the_same_text() -> None:
    assert "".join(split_for_stream(CANNED_REPLY)) == CANNED_REPLY
    assert await collect(CannedProvider(), "hi") == CANNED_REPLY
    chunks = [chunk async for chunk in CannedProvider().stream(messages("hi"))]
    assert len(chunks) > 1  # it really streams, rather than one big chunk


async def test_the_canned_provider_is_always_configured() -> None:
    provider = CannedProvider()
    assert provider.is_configured is True
    assert provider.model_id is None
    assert provider.describe()["terminal"] is True


# --- redaction, guard and prompt wiring --------------------------------------


async def test_redaction_runs_before_the_provider_sees_anything() -> None:
    primary = FakeLLMProvider()
    await chain(primary).complete(messages("mail me at riya@example.com or call +91 98765 43210"))
    assert last_call(primary).user_text == "mail me at [EMAIL] or call [PHONE]"


async def test_redaction_can_be_switched_off_for_a_caller_that_needs_it() -> None:
    """Only legitimate for an internal caller that has already redacted; the
    test exists so the flag is never quietly flipped by accident."""
    primary = FakeLLMProvider()
    link = LLMChain(
        [primary, CannedProvider()],
        guard=TokenGuard(),
        redactor=Redactor(),
        redact=False,
    )
    await link.complete(messages("mail me at riya@example.com"))
    assert "riya@example.com" in last_call(primary).user_text


async def test_the_guard_report_travels_with_the_result() -> None:
    primary = FakeLLMProvider()
    result = await chain(primary).complete(messages("hello"))
    guard = raw(result)["guard"]
    assert guard["turns_in"] == 1
    assert guard["dropped_turns"] == 0
    assert guard["within_budget"] is True


async def test_long_conversations_are_windowed_to_the_budget() -> None:
    primary = FakeLLMProvider()
    turns = messages(*[f"turn number {index} with some words in it" for index in range(40)])
    await chain(primary).complete(turns)
    # 8-turn window, and the newest turn is always the one that survived.
    assert len(last_call(primary).messages) == 8
    # messages() alternates user/assistant, so the newest *user* turn is 38.
    assert "turn number 38" in last_call(primary).user_text


async def test_over_long_input_is_truncated_never_rejected() -> None:
    primary = FakeLLMProvider()
    long_message = "word " * 500
    await chain(primary).complete(messages(long_message))
    sent = last_call(primary).messages[0].content
    assert len(sent) <= 400
    assert sent.endswith("[…]")  # the model is told text was removed


async def test_the_prompt_follows_the_language_of_the_newest_user_turn() -> None:
    primary = FakeLLMProvider()
    await chain(primary).complete(messages("আজ খুব মন খারাপ, কিছু ভালো লাগছে না"))
    assert "Reply in Bengali" in (last_call(primary).system or "")


async def test_an_undetermined_language_is_not_named_in_the_prompt() -> None:
    """Detection says "other" for short text. "Reply in other" is not an
    instruction a model can follow, so the prompt falls back to the phrase
    that lets the model read the language off the message itself."""
    primary = FakeLLMProvider()
    await chain(primary).complete(messages("hmm"))
    system = last_call(primary).system or ""
    assert "Reply in the language they wrote in" in system
    assert "Reply in other" not in system


async def test_describe_carries_the_whole_chain_without_secrets() -> None:
    described = chain(FakeLLMProvider(), ScriptedProvider(name="spare")).describe()
    assert described["prompt_version"] == PROMPT_VERSION
    links = described["chain"]
    assert isinstance(links, list)
    assert [entry["provider"] for entry in links] == ["fake", "spare", "canned"]
    assert described["redact"] is True


async def test_aclose_closes_every_link() -> None:
    link = chain(FakeLLMProvider(), FakeLLMProvider())
    await link.aclose()  # Fakes hold nothing open; must not raise


async def test_the_chain_is_configured_because_the_canned_link_always_is() -> None:
    assert chain(FakeLLMProvider(configured=False)).is_configured is True


async def test_model_id_comes_from_the_leading_provider() -> None:
    assert chain(FakeLLMProvider(model="fake-deterministic-v1")).model_id == (
        "fake-deterministic-v1"
    )


async def test_empty_messages_are_still_answered() -> None:
    """A degenerate request must degrade, not raise: this is the path a
    half-built client takes on its first day."""
    result = await chain(FakeLLMProvider()).complete([])
    assert result.text
    assert isinstance(result.raw, dict)


async def test_message_objects_are_not_mutated_by_the_chain() -> None:
    """Redaction and truncation copy; the caller's objects are its own."""
    original = LLMMessage(role="user", content="mail me at riya@example.com")
    await chain(FakeLLMProvider()).complete([original])
    assert original.content == "mail me at riya@example.com"
