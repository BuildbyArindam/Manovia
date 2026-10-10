#!/usr/bin/env python3
"""One real LLM call, or an honest report of why there wasn't one.

This is the script behind Day 10 verification item 4. It answers the question
"does the provider layer actually talk to a model?" in three steps, and stops at
the first one that works:

1. **Anthropic**, if ``ANTHROPIC_API_KEY`` and ``ANTHROPIC_MODEL`` are both set.
   One harmless call ("Say hello in one sentence"), printing the reply and the
   latency. If the key is missing or rejected it says so and falls through — a
   rejected credential is a fallback, never a crash.
2. **Ollama**, if something is listening on ``OLLAMA_BASE_URL``.
3. **The Fake provider plus the canned fallback**, which is what the sandbox and
   CI always run, and which is the path a user sees when both real providers are
   unavailable.

    python3 scripts/llm_probe.py
    ANTHROPIC_MODEL=... python3 scripts/llm_probe.py --max-tokens 32

It never prints a message it is about to send that still contains identifiers,
and it never prints the API key.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import Settings  # noqa: E402
from app.services.llm import (  # noqa: E402
    AnthropicProvider,
    FakeLLMProvider,
    OllamaProvider,
    build_llm_chain,
)
from app.services.llm.base import LLMMessage  # noqa: E402

PROBE = "Say hello in one sentence"


async def probe_anthropic(settings: Settings, max_tokens: int) -> bool:
    provider = AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    reason = provider.configure_reason()
    if reason is not None:
        print(f"  anthropic  skipped: {reason}")
        return False
    print(f"  anthropic  calling {provider.model_id} ...")
    started = time.perf_counter()
    try:
        result = await provider.complete(
            [LLMMessage(role="user", content=PROBE)],
            max_tokens=max_tokens,
            temperature=0.0,
        )
    except Exception as exc:  # any provider error is a fall-through here
        print(f"  anthropic  FAILED after {time.perf_counter() - started:.2f}s: "
              f"{type(exc).__name__}: {exc}")
        return False
    elapsed = time.perf_counter() - started
    print(f"  anthropic  replied in {elapsed * 1000:.0f} ms "
          f"(provider-reported latency {result.latency_ms:.0f} ms)")
    print(f"  anthropic  model  : {result.model}")
    print(f"  anthropic  tokens : {result.usage.total_tokens if result.usage else 'not reported'}")
    print(f"  anthropic  reply  : {result.text.strip()!r}")
    return True


async def probe_ollama(settings: Settings, max_tokens: int) -> bool:
    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=min(settings.llm_timeout_seconds, 10.0),
    )
    print(f"  ollama     trying {provider.base_url} ...")
    started = time.perf_counter()
    try:
        result = await provider.complete(
            [LLMMessage(role="user", content=PROBE)],
            max_tokens=max_tokens,
            temperature=0.0,
        )
    except Exception as exc:
        print(f"  ollama     FAILED after {time.perf_counter() - started:.2f}s: "
              f"{type(exc).__name__}: {exc}")
        return False
    elapsed = time.perf_counter() - started
    print(f"  ollama     replied in {elapsed * 1000:.0f} ms using {result.model}")
    print(f"  ollama     reply   : {result.text.strip()!r}")
    return True


async def probe_fake(settings: Settings) -> None:
    """The path every offline run takes — and the one a user gets when both
    real providers are down."""
    chain = build_llm_chain(Settings(llm_provider="fake"))
    result = await chain.complete([LLMMessage(role="user", content=PROBE)])
    fake = chain.providers[0]
    assert isinstance(fake, FakeLLMProvider)
    print(f"  fake       replied with: {result.text.strip()!r}")
    print(f"  fake       degraded={result.degraded} provider={result.provider} "
          f"prompt={result.prompt_version}")
    print(f"  fake       the provider saw: {fake.last_call.as_payload()['messages']!r}")

    # Now break the Fake and show the canned terminal link answering.
    chain.providers[0].script_error(RuntimeError("probe: simulated outage"))
    degraded = await chain.complete([LLMMessage(role="user", content=PROBE)])
    print(f"  canned     replied with: {degraded.text.strip()[:90]}...")
    print(f"  canned     degraded={degraded.degraded} provider={degraded.provider} "
          f"fallbacks_used={degraded.fallbacks_used}")


async def main(max_tokens: int) -> int:
    settings = Settings()
    print("Manovia LLM probe")
    print("=" * 78)
    print(f"LLM_PROVIDER        = {settings.llm_provider}")
    print(f"ANTHROPIC_API_KEY   = {'set' if settings.anthropic_api_key else 'not set'}")
    print(f"ANTHROPIC_MODEL     = {settings.anthropic_model or 'not set'}")
    print(f"OLLAMA_BASE_URL     = {settings.ollama_base_url or 'not set (default localhost:11434)'}")
    print("=" * 78)

    if await probe_anthropic(settings, max_tokens):
        return 0
    print()
    if await probe_ollama(settings, max_tokens):
        return 0
    print()
    print("  neither real provider answered — falling back to the offline path")
    print()
    await probe_fake(settings)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-tokens", type=int, default=64)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.max_tokens)))
