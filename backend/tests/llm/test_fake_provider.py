"""The Fake provider: deterministic, scriptable, and it records everything.

It is a test double, so it gets tested like one. The properties that matter are
the ones every test in this package depends on: the same script produces the
same bytes, failures can be scheduled, and the recorded payload is the *exact*
thing a real provider would have received — because the PII tests assert on it.
"""

from __future__ import annotations

import pytest

from app.services.llm.base import LLMMessage, ProviderDown, ProviderRateLimited
from app.services.llm.fake_provider import (
    DEFAULT_REPLY,
    FAKE_MODEL,
    FakeLLMProvider,
)

from .conftest import collect, last_call, messages


async def test_the_default_reply_is_fixed_and_independent_of_the_input() -> None:
    """A Fake that reacts to input is a Fake that can leak input into an
    assertion or a log."""
    provider = FakeLLMProvider()
    first = await provider.complete(messages("I am sad"))
    second = await provider.complete(messages("I am furious"))
    assert first.text == second.text == DEFAULT_REPLY
    assert first.provider == "fake"
    assert first.model == FAKE_MODEL


async def test_replies_are_consumed_in_order_and_the_last_repeats() -> None:
    provider = FakeLLMProvider(replies=["one", "two"])
    chain = [await provider.complete(messages("hi")) for _ in range(4)]
    assert [result.text for result in chain] == ["one", "two", "two", "two"]


async def test_script_returns_self_for_chaining() -> None:
    provider = FakeLLMProvider().script("a", "b")
    assert provider is not None
    assert (await provider.complete(messages("hi"))).text == "a"


async def test_fail_times_fails_only_the_first_n_calls() -> None:
    provider = FakeLLMProvider(fail_times=2)
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await provider.complete(messages("hi"))
    assert (await provider.complete(messages("hi"))).text == DEFAULT_REPLY


async def test_scripted_errors_expire_after_their_count() -> None:
    provider = FakeLLMProvider().script_error(ProviderDown("down"), times=2)
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await provider.complete(messages("hi"))
    assert (await provider.complete(messages("hi"))).text == DEFAULT_REPLY


async def test_an_llm_error_is_raised_unchanged() -> None:
    """Callers script typed errors, and the retry machinery reads the type."""
    provider = FakeLLMProvider(error=ProviderRateLimited(retry_after=1.5))
    with pytest.raises(ProviderRateLimited) as excinfo:
        await provider.complete(messages("hi"))
    assert excinfo.value.retry_after == 1.5


async def test_an_unconfigured_fake_reports_itself_unusable() -> None:
    provider = FakeLLMProvider(configured=False)
    assert provider.is_configured is False
    assert provider.describe()["configured"] is False


async def test_every_call_is_recorded_in_full() -> None:
    provider = FakeLLMProvider()
    history = [
        LLMMessage(role="user", content="first"),
        LLMMessage(role="assistant", content="reply"),
        LLMMessage(role="user", content="second"),
    ]
    await provider.complete(history, system="be kind", max_tokens=99, temperature=0.1)
    call = last_call(provider)
    assert call.index == 0
    assert call.system == "be kind"
    assert call.max_tokens == 99
    assert call.temperature == 0.1
    assert call.user_text == "first\nsecond"
    assert call.as_payload()["messages"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]


async def test_all_text_includes_the_system_prompt() -> None:
    provider = FakeLLMProvider()
    await provider.complete(messages("body"), system="system text")
    assert last_call(provider).all_text == "system text\nbody"


async def test_reset_forgets_calls_but_keeps_the_script() -> None:
    provider = FakeLLMProvider(replies=["one"]).script_error(ProviderDown(), times=1)
    with pytest.raises(ProviderDown):
        await provider.complete(messages("hi"))
    provider.reset()
    assert provider.call_count == 0
    assert (await provider.complete(messages("hi"))).text == "one"


async def test_streaming_splits_the_reply_into_reassemblable_chunks() -> None:
    provider = FakeLLMProvider(replies=["I hear you and I am here with you"])
    assert await collect(provider, "hi") == "I hear you and I am here with you"


async def test_streaming_can_be_scripted_chunk_by_chunk() -> None:
    provider = FakeLLMProvider().script_stream("I ", "hear ", "you.")
    assert await collect(provider, "hi") == "I hear you."


async def test_streaming_records_its_own_calls() -> None:
    provider = FakeLLMProvider()
    await collect(provider, "hi")
    assert provider.call_count == 0
    assert len(provider.stream_calls) == 1
    assert provider.stream_calls[0].user_text == "hi"


async def test_streaming_propagates_scripted_failures() -> None:
    provider = FakeLLMProvider().script_error(ProviderDown("down"), times=1)
    with pytest.raises(ProviderDown):
        await collect(provider, "hi")


async def test_latency_is_observable_but_tiny() -> None:
    provider = FakeLLMProvider(latency_ms=1.0)
    await provider.complete(messages("hi"))
    assert provider.call_count == 1


async def test_usage_is_reported_even_for_a_fake() -> None:
    """Callers that read ``usage`` must not crash on the test double."""
    result = await FakeLLMProvider().complete(messages("a" * 40))
    assert result.usage is not None
    assert result.usage.input_tokens == 10
    assert result.usage.total_tokens == 18


async def test_the_recorded_messages_are_the_same_objects() -> None:
    history = messages("hi")
    provider = FakeLLMProvider()
    await provider.complete(history)
    assert list(last_call(provider).messages) == history


def test_script_requires_at_least_one_reply() -> None:
    with pytest.raises(ValueError):
        FakeLLMProvider().script()


def test_the_fake_has_no_network_dependency() -> None:
    """Asserted structurally: constructing it touches no socket, no key, no
    clock. If a future edit adds one, this is where it should be noticed."""
    provider = FakeLLMProvider()
    assert provider.describe() == {
        "provider": "fake",
        "model": FAKE_MODEL,
        "configured": True,
        "calls": 0,
    }
