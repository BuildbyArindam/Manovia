"""Day 23: the chat answers in the reader's language.

The deterministic parts of a conversation — the crisis card copy and the
MEDIUM check-in lines — are pre-written in ``content/i18n`` per locale, and
these tests pin that they actually arrive in the locale the request asked
for. For the model-written parts, the orchestrator instructs the provider in
the reader's language; with the scripted Fake provider the reply text is a
fixed English stand-in, so the assertion there is on the language decision
(``metadata.locale`` and the system prompt), which is the part this codebase
controls. A real provider gets the same instruction and answers in kind.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app  # noqa: F401  (keeps parity with sibling suites)
from app.services.chat import build_orchestrator
from app.services.llm.fake_provider import FakeLLMProvider
from app.services.nlp.keyword import KeywordFallbackAnalyzer
from tests.chat.conftest import NEUTRAL_MESSAGE

SESSIONS = "/api/v1/chat/sessions"
VERSION = "2026-10-01"

# Crisis sentences straight from the eval dataset (synthetic, no real user
# text). Both are deliberately messages the *rules* engine catches on its own:
# the test settings run with SAFETY_ML_ENABLED=false (pinned-text suites need
# deterministic levels), so a message that only the ML path would raise cannot
# be asserted here. The ML raise path for Indic text is covered by the evals.
HI_CRISIS = "ज़िंदगी खत्म कर देना चाहता हूँ।"
BN_CRISIS = "আমি আত্মহত্যা করতে চাই।"
HI_BENIGN = "आज मौसम बहुत अच्छा है, थोड़ा टहलने जाऊँगा।"
BN_BENIGN = "আজ অফিসে ভালো কাটল, প্রজেক্টের কাজ শেষ করে ফেলেছি।"

DEVANAGARI = re.compile(r"[\u0900-\u097F]")
BENGALI_SCRIPT = re.compile(r"[\u0980-\u09FF]")


@pytest.fixture
def llm() -> FakeLLMProvider:
    return FakeLLMProvider()


@pytest_asyncio.fixture(autouse=True)
async def installed_fake(app: FastAPI, settings: Settings, llm: FakeLLMProvider) -> None:
    app.state.chat_orchestrator = build_orchestrator(
        settings, llm=llm, analyzer=KeywordFallbackAnalyzer()
    )


async def signed_in_guest(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/guest")
    assert response.status_code == 201
    body = response.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    granted = await client.post(
        "/api/v1/consent",
        headers=headers,
        json={
            "grants": [
                {"kind": kind, "version": VERSION, "granted": True}
                for kind in ("ai_disclosure", "terms")
            ]
        },
    )
    assert granted.status_code == 201
    return headers


async def open_session(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    response = await client.post(SESSIONS, headers=headers)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def say(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    session_id: str,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    response = await client.post(
        f"{SESSIONS}/{session_id}/messages",
        headers=headers,
        json={"message": message, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("text", "locale", "script"),
    [
        (HI_CRISIS, "hi", DEVANAGARI),
        (BN_CRISIS, "bn", BENGALI_SCRIPT),
    ],
)
async def test_a_crisis_reply_is_written_in_the_readers_language(
    client: httpx.AsyncClient,
    text: str,
    locale: str,
    script: re.Pattern[str],
) -> None:
    headers = await signed_in_guest(client)
    session_id = await open_session(client, headers)

    body = await say(client, headers, session_id, text, region="IN", locale=locale)
    meta = body["metadata"]

    assert meta["response_type"] == "crisis"
    assert meta["locale"] == locale
    crisis = meta["crisis"]
    assert crisis is not None
    assert crisis["locale"] == locale, "the card copy comes from the requested locale's file"
    assert crisis["title"] and crisis["body"]
    # The words are actually in the reader's script, not English leftovers.
    assert script.search(crisis["title"]), crisis["title"]
    assert all(script.search(line) for line in crisis["body"]), crisis["body"]
    # The LLM never sees a crisis turn (AGENTS.md rule 1 is asserted elsewhere;
    # here we only pin that the deterministic copy won).


@pytest.mark.parametrize(
    ("text", "locale"),
    [
        (HI_BENIGN, "hi"),
        (BN_BENIGN, "bn"),
    ],
)
async def test_a_benign_turn_keeps_the_readers_locale(
    client: httpx.AsyncClient, text: str, locale: str
) -> None:
    headers = await signed_in_guest(client)
    session_id = await open_session(client, headers)

    body = await say(client, headers, session_id, text, region="IN", locale=locale)
    meta = body["metadata"]

    assert meta["response_type"] != "crisis"
    assert meta["locale"] == locale, "the turn reports the reader's locale back to the UI"
    assert body["reply"], "the Fake provider still produces a reply"


async def test_the_language_instruction_follows_the_locale(
    client: httpx.AsyncClient, llm: FakeLLMProvider
) -> None:
    """The system prompt the model receives names the reader's language."""
    headers = await signed_in_guest(client)
    session_id = await open_session(client, headers)

    await say(client, headers, session_id, HI_BENIGN, region="IN", locale="hi")

    assert llm.calls, "the benign turn reaches the model"
    system_prompt = llm.calls[-1].system
    assert "Hindi" in system_prompt or "hindi" in system_prompt.casefold(), system_prompt[:400]


async def test_an_english_message_with_no_locale_stays_english(
    client: httpx.AsyncClient,
) -> None:
    headers = await signed_in_guest(client)
    session_id = await open_session(client, headers)

    body = await say(client, headers, session_id, NEUTRAL_MESSAGE)
    assert body["metadata"]["locale"] == "en"
