"""Proof that no PII reaches the provider.

This is the privacy claim the whole Day 10 exists for, so it is asserted from
the outside: not "the redactor works" (that is ``tests/unit/test_redaction.py``)
but "the bytes the provider actually received contain no identifiers".

The mechanism is the Fake provider's call log. Every call records the exact
payload — messages, system prompt, ``max_tokens``, ``temperature`` — so the test
can hold the request in its hand and read it.

Four things are checked for every sample:

1. no identifier appears anywhere in the payload, including the system prompt;
2. the payload contains the typed placeholders instead;
3. the assertion can fail — a control case with redaction switched off shows the
   raw identifier, which is what stops this file being a test that always passes;
4. nothing identifiable is written to the logs either, because a warning line
   with an email in it is the same leak by another door.
"""

from __future__ import annotations

import json
import re

import pytest
from structlog.testing import capture_logs

from app.services.llm.base import LLMMessage, LLMResult, ProviderDown
from app.services.llm.canned import CANNED_REPLY
from app.services.llm.chain import LLMChain
from app.services.llm.fake_provider import FakeLLMProvider
from app.services.llm.guard import TokenGuard
from app.services.nlp.redaction import Redactor

from .conftest import last_call, last_stream_call, messages, raw

#: ``(identifier, text, placeholder)`` — the numbers are invented, not real.
SAMPLES: tuple[tuple[str, str, str], ...] = (
    ("riya.sharma@example.com", "you can email me at riya.sharma@example.com", "[EMAIL]"),
    ("+91 98765 43210", "call me on +91 98765 43210 after six", "[PHONE]"),
    ("09876543210", "my landline is 09876543210", "[PHONE]"),
    ("9876543210", "just dial 9876543210", "[PHONE]"),
    ("1234 5678 9012", "my aadhaar is 1234 5678 9012", "[ID]"),
    ("ABCDE1234F", "pan ABCDE1234F on file", "[ID]"),
    ("123-45-6789", "ssn 123-45-6789", "[ID]"),
    ("4111111111111111", "card 4111111111111111 exp 12/28", "[CARD]"),
    (
        "https://app.example.com/reset?token=abc123",
        "the reset link was https://app.example.com/reset?token=abc123",
        "[URL]",
    ),
    ("+44 20 7946 0958", "uk number +44 20 7946 0958", "[PHONE]"),
)

ALL_IDENTIFIERS = [identifier for identifier, _, _ in SAMPLES]

#: A message carrying several identifiers at once, the realistic worst case.
COMBINED = (
    "Hi, I'm Riya. I've been drowning in work and I can't sleep. "
    "Reach me at riya.sharma@example.com or +91 98765 43210 if it matters — "
    "my aadhaar is 1234 5678 9012 and I used card 4111111111111111 for the "
    "subscription. The login link they sent was "
    "https://app.example.com/reset?token=abc123def"
)


def chain_with(primary: FakeLLMProvider, **kwargs: object) -> LLMChain:
    from app.services.llm.canned import CannedProvider

    return LLMChain(
        [primary, CannedProvider()],
        guard=TokenGuard(max_input_chars=8000, window_turns=20, window_tokens=4000),
        redactor=Redactor(),
        redact=True,
        **kwargs,  # type: ignore[arg-type]
    )


# --- the payload -------------------------------------------------------------


@pytest.mark.parametrize(("identifier", "text", "placeholder"), SAMPLES)
async def test_no_identifier_reaches_the_provider(
    identifier: str, text: str, placeholder: str
) -> None:
    primary = FakeLLMProvider()
    await chain_with(primary).complete(messages(text))
    payload = last_call(primary).as_payload()
    serialised = json.dumps(payload, ensure_ascii=False)
    assert identifier not in serialised, f"{identifier} reached the provider"
    assert placeholder in serialised


async def test_a_message_carrying_every_identifier_at_once() -> None:
    primary = FakeLLMProvider()
    await chain_with(primary).complete(messages(COMBINED))
    payload = last_call(primary).as_payload()
    serialised = json.dumps(payload, ensure_ascii=False)
    for identifier in ALL_IDENTIFIERS:
        assert identifier not in serialised, f"{identifier} reached the provider"
    for placeholder in ("[EMAIL]", "[PHONE]", "[ID]", "[CARD]", "[URL]"):
        assert placeholder in serialised
    # Names are not redacted by default, and that is stated rather than implied.
    assert "Riya" in serialised


async def test_the_exact_payload_is_the_redacted_text() -> None:
    """Asserting on the whole payload catches anything a substring check would
    miss — a second copy of the message, an echo in a metadata field. The
    system prompt is supplied here so the assertion is about redaction alone;
    prompt rendering has its own tests."""
    primary = FakeLLMProvider()
    await chain_with(primary).complete(
        messages("mail me at riya.sharma@example.com or call +91 98765 43210"),
        system="be kind",
        max_tokens=123,
        temperature=0.25,
    )
    assert last_call(primary).as_payload() == {
        "messages": [
            {
                "role": "user",
                "content": "mail me at [EMAIL] or call [PHONE]",
            }
        ],
        "system": "be kind",
        "max_tokens": 123,
        "temperature": 0.25,
    }


async def test_the_system_prompt_carries_no_user_text() -> None:
    primary = FakeLLMProvider()
    await chain_with(primary).complete(messages(COMBINED))
    system = last_call(primary).system or ""
    for identifier in ALL_IDENTIFIERS:
        assert identifier not in system


async def test_redaction_runs_before_truncation_not_after() -> None:
    """If truncation ran first it could cut an address in half, and the
    redactor would no longer recognise what was left."""
    primary = FakeLLMProvider()
    text = "my email is riya.sharma@example.com " + "and then I rambled " * 400
    await chain_with(primary).complete(messages(text))
    sent = last_call(primary).messages[0].content
    assert "riya.sharma@example.com" not in sent
    assert "[EMAIL]" in sent
    assert sent.count("[") == sent.count("]")


# --- the control: the test must be able to fail ------------------------------


async def test_with_redaction_off_the_identifier_does_reach_the_provider() -> None:
    """Without this, every assertion above could pass because the assertion is
    broken rather than because the code is right."""
    primary = FakeLLMProvider()
    link = LLMChain(
        [primary],
        guard=TokenGuard(),
        redactor=Redactor(),
        redact=False,
    )
    await link.complete(messages("mail me at riya.sharma@example.com"))
    assert "riya.sharma@example.com" in last_call(primary).user_text


async def test_the_default_chain_from_settings_redacts() -> None:
    from app.core.config import Settings
    from app.services.llm import build_llm_chain

    built = build_llm_chain(Settings(llm_provider="fake"))
    fake = built.providers[0]
    assert isinstance(fake, FakeLLMProvider)
    await built.complete(messages("mail me at riya.sharma@example.com"))
    assert "riya.sharma@example.com" not in fake.last_call.user_text  # type: ignore[union-attr]
    assert "[EMAIL]" in fake.last_call.user_text  # type: ignore[union-attr]


# --- logs and results --------------------------------------------------------


async def test_a_failing_provider_does_not_log_the_message() -> None:
    """A warning line with an email in it is the same leak by another door."""
    primary = FakeLLMProvider(error=ProviderDown("simulated outage"))
    with capture_logs() as captured:
        result = await chain_with(primary).complete(messages(COMBINED))
    # The chain degraded to the canned reply, which says nothing about the input.
    assert result.text == CANNED_REPLY
    logged = json.dumps(captured, ensure_ascii=False)
    for identifier in ALL_IDENTIFIERS:
        assert identifier not in logged
    assert captured, "expected at least one log line for the degradation"


async def test_the_result_carries_no_original_text() -> None:
    primary = FakeLLMProvider(replies=["I hear you. That sounds heavy."])
    result: LLMResult = await chain_with(primary).complete(messages(COMBINED))
    for identifier in ALL_IDENTIFIERS:
        assert identifier not in result.text
        assert identifier not in json.dumps(raw(result), ensure_ascii=False)


async def test_describe_carries_no_user_text() -> None:
    primary = FakeLLMProvider()
    link = chain_with(primary)
    await link.complete(messages(COMBINED))
    described = json.dumps(link.describe(), ensure_ascii=False)
    for identifier in ALL_IDENTIFIERS:
        assert identifier not in described


async def test_the_guard_report_carries_no_user_text() -> None:
    primary = FakeLLMProvider()
    result = await chain_with(primary).complete(messages(COMBINED))
    guard = json.dumps(raw(result)["guard"], ensure_ascii=False)
    assert re.search(r"\d{9,}", guard) is None or "input_chars" in guard
    for identifier in ALL_IDENTIFIERS:
        assert identifier not in guard


async def test_streaming_payloads_are_redacted_too() -> None:
    """The streaming path is a separate code path with its own `_prepare`."""
    primary = FakeLLMProvider()
    link = chain_with(primary)
    parts = [chunk async for chunk in link.stream(messages(COMBINED))]
    assert parts
    # Streaming records into its own log, so the assertion names it explicitly.
    assert primary.last_call is None
    payload = json.dumps(last_stream_call(primary).as_payload(), ensure_ascii=False)
    for identifier in ALL_IDENTIFIERS:
        assert identifier not in payload


async def test_a_multi_turn_history_is_redacted_turn_by_turn() -> None:
    primary = FakeLLMProvider()
    history = [
        LLMMessage(role="user", content="first: riya.sharma@example.com"),
        LLMMessage(role="assistant", content="I hear you."),
        LLMMessage(role="user", content="second: +91 98765 43210"),
    ]
    await chain_with(primary).complete(history)
    payload = json.dumps(last_call(primary).as_payload(), ensure_ascii=False)
    assert "riya.sharma@example.com" not in payload
    assert "+91 98765 43210" not in payload
    assert payload.count("[EMAIL]") == 1
    assert payload.count("[PHONE]") == 1
