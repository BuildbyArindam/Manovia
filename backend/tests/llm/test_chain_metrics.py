"""Day 27: the chain's LLM observability counters.

The chain is where "the leading provider could not serve" is a fact (the
``llm_fallbacks_total`` hop counter, with reasons from a closed set), and where
a token iterator finally exposes which link served a stream — the orchestrator
records the product-level outcome from that.
"""

from __future__ import annotations

import pytest
from prometheus_client import generate_latest

from app.core.metrics import get_metrics, reset_metrics
from app.services.llm.base import AllProvidersFailed, LLMProvider, ProviderDown
from app.services.llm.canned import CannedProvider
from app.services.llm.chain import LLMChain
from app.services.llm.fake_provider import FakeLLMProvider
from app.services.llm.guard import TokenGuard
from app.services.nlp.redaction import Redactor

from .conftest import ScriptedProvider, messages


@pytest.fixture(autouse=True)
def fresh_metrics() -> None:
    reset_metrics()


def body() -> str:
    text: str = generate_latest(get_metrics().registry).decode()
    return text


def plain_chain(*providers: LLMProvider) -> LLMChain:
    return LLMChain(
        [*providers, CannedProvider()],
        guard=TokenGuard(),
        redactor=Redactor(),
    )


async def test_a_provider_failure_counts_as_a_fallback_hop() -> None:
    primary = FakeLLMProvider(error=ProviderDown("down"))
    second = FakeLLMProvider(replies=["from the spare"])
    result = await plain_chain(primary, second).complete(messages("hello"))
    assert result.provider == "fake"
    assert 'manovia_llm_fallbacks_total{reason="provider_failed"} 1.0' in body()


async def test_an_unconfigured_provider_is_counted_separately() -> None:
    offline = ScriptedProvider(configured=False)
    primary = FakeLLMProvider(replies=["ok"])
    await plain_chain(offline, primary).complete(messages("hello"))
    assert 'manovia_llm_fallbacks_total{reason="not_configured"} 1.0' in body()


async def test_a_crashing_provider_counts_as_provider_crashed() -> None:
    primary = ScriptedProvider(RuntimeError("sdk exploded"))
    second = FakeLLMProvider(replies=["ok"])
    await plain_chain(primary, second).complete(messages("hello"))
    assert 'manovia_llm_fallbacks_total{reason="provider_crashed"} 1.0' in body()


async def test_stream_records_which_link_served() -> None:
    primary = ScriptedProvider(ProviderDown("down"))
    second = FakeLLMProvider(replies=["spare tokens"])
    streaming_chain = plain_chain(primary, second)
    chunks = [chunk async for chunk in streaming_chain.stream(messages("hello"))]
    assert "".join(chunks) == "spare tokens"
    assert streaming_chain.last_stream_served_by == ("fake", 1)
    assert 'manovia_llm_fallbacks_total{reason="provider_failed"} 1.0' in body()


async def test_stream_leaves_served_by_unset_when_nothing_streams() -> None:
    primary = ScriptedProvider(ProviderDown("down"), ProviderDown("still down"))
    broken = plain_chain(primary)
    broken._providers = (primary,)  # no canned link: the chain must raise
    chunks: list[str] = []
    with pytest.raises(AllProvidersFailed):
        async for chunk in broken.stream(messages("hello")):
            chunks.append(chunk)
    assert chunks == []
    assert broken.last_stream_served_by is None
