"""The Ollama fallback provider, driven by ``httpx.MockTransport``.

No Ollama server, no socket. What is under test is the code around the HTTP
call: the ``/api/chat`` request shape, NDJSON streaming, model discovery via
``/api/tags``, and — most importantly — that the *expected* failure (nothing
listening on localhost) becomes :class:`ProviderDown` so the chain moves on to
the canned reply rather than surfacing a connection error.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.services.llm.base import (
    LLMMessage,
    ProviderBadRequest,
    ProviderDown,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
)
from app.services.llm.ollama_provider import DEFAULT_BASE_URL, OllamaProvider

from .conftest import messages

TEST_MODEL = "llama3.2:3b"


def client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=DEFAULT_BASE_URL)


def chat_response(text: str = "I hear you.", *, model: str = TEST_MODEL) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "message": {"role": "assistant", "content": text},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 31,
            "eval_count": 12,
        },
    )


def ndjson(*chunks: str) -> httpx.Response:
    """Ollama streams one JSON object per line, terminated by ``done: true``."""
    lines = [
        json.dumps({"message": {"role": "assistant", "content": chunk}, "done": False})
        for chunk in chunks
    ]
    lines.append(json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}))
    return httpx.Response(200, text="\n".join(lines) + "\n")


async def drain(provider: OllamaProvider) -> str:
    parts: list[str] = []
    async for chunk in provider.stream(messages("hi")):
        parts.append(chunk)
    return "".join(parts)


# --- configuration -----------------------------------------------------------


def test_the_default_base_url_is_localhost_only() -> None:
    """The local fallback must not default to a remote host: pointing it
    anywhere else by default would quietly export user text off the box."""
    assert DEFAULT_BASE_URL == "http://localhost:11434"
    assert OllamaProvider().base_url == DEFAULT_BASE_URL


async def test_no_base_url_means_not_configured() -> None:
    subject = OllamaProvider(base_url="   ")
    assert subject.is_configured is False
    with pytest.raises(ProviderNotConfigured):
        await subject.complete(messages("hi"))
    with pytest.raises(ProviderNotConfigured):
        await drain(subject)


async def test_an_unset_model_is_discovered_at_first_use() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": TEST_MODEL}]})
        return chat_response()

    subject = OllamaProvider(client=client(handler))
    assert subject.model_id is None  # nothing hard-coded
    result = await subject.complete(messages("hi"))
    assert result.text == "I hear you."
    assert seen == ["/api/tags", "/api/chat"]
    # Discovery is remembered, so describe() is honest afterwards.
    assert subject.model_id == TEST_MODEL


async def test_a_server_with_no_models_is_provider_down() -> None:
    subject = OllamaProvider(client=client(lambda r: httpx.Response(200, json={"models": []})))
    with pytest.raises(ProviderDown):
        await subject.complete(messages("hi"))


async def test_an_empty_conversation_is_a_bad_request() -> None:
    subject = OllamaProvider(model=TEST_MODEL, client=client(lambda r: chat_response()))
    with pytest.raises(ProviderBadRequest):
        await subject.complete([])
    with pytest.raises(ProviderBadRequest):
        async for _ in subject.stream([]):
            pass


# --- request and response shape ----------------------------------------------


async def test_the_chat_payload_shape() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return chat_response()

    subject = OllamaProvider(model=TEST_MODEL, client=client(handler))
    result = await subject.complete(
        [LLMMessage(role="user", content="hello")],
        system="be kind",
        max_tokens=120,
        temperature=0.1,
    )
    assert result.text == "I hear you."
    body = json.loads(captured[0].content)
    assert body["model"] == TEST_MODEL
    assert body["stream"] is False
    # Ollama has no separate system parameter: ours is prepended as a message.
    # Safe because the system prompt is rendered from a file, never from input.
    assert body["messages"][0] == {"role": "system", "content": "be kind"}
    assert body["messages"][1] == {"role": "user", "content": "hello"}
    assert body["options"]["num_predict"] == 120
    assert body["options"]["temperature"] == 0.1


async def test_no_system_prompt_means_no_system_message() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return chat_response()

    subject = OllamaProvider(model=TEST_MODEL, client=client(handler))
    await subject.complete(messages("hi"))
    body = json.loads(captured[0].content)
    assert [entry["role"] for entry in body["messages"]] == ["user"]


async def test_usage_and_provenance_are_reported() -> None:
    subject = OllamaProvider(model=TEST_MODEL, client=client(lambda r: chat_response()))
    result = await subject.complete(messages("hi"))
    assert result.provider == "ollama"
    assert result.model == TEST_MODEL
    assert result.finish_reason == "stop"
    assert result.usage is not None
    assert result.usage.input_tokens == 31
    assert result.usage.output_tokens == 12


async def test_streaming_reassembles_ndjson() -> None:
    subject = OllamaProvider(
        model=TEST_MODEL, client=client(lambda r: ndjson("I ", "hear ", "you."))
    )
    assert await drain(subject) == "I hear you."


async def test_streaming_tolerates_blank_and_junk_lines() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = "\n".join(["not json", "", '{"message": {"content": "ok"}}', '{"done": true}'])
        return httpx.Response(200, text=body)

    subject = OllamaProvider(model=TEST_MODEL, client=client(handler))
    assert await drain(subject) == "ok"


# --- failures ----------------------------------------------------------------


async def test_nothing_listening_is_provider_down() -> None:
    """The everyday case on a laptop: no Ollama installed. It is a degradation,
    and the chain's canned link absorbs it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    subject = OllamaProvider(model=TEST_MODEL, client=client(handler))
    with pytest.raises(ProviderDown) as excinfo:
        await subject.complete(messages("hi"))
    assert "not reachable" in str(excinfo.value)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, ProviderRateLimited),
        (500, ProviderDown),
        (503, ProviderDown),
        (400, ProviderBadRequest),
        (404, ProviderBadRequest),
    ],
)
async def test_http_statuses_map_onto_the_error_tree(status: int, expected: type) -> None:
    subject = OllamaProvider(
        model=TEST_MODEL, client=client(lambda r: httpx.Response(status, json={}))
    )
    with pytest.raises(expected):
        await subject.complete(messages("hi"))


async def test_a_rate_limit_carries_retry_after() -> None:
    subject = OllamaProvider(
        model=TEST_MODEL,
        client=client(lambda r: httpx.Response(429, headers={"retry-after": "3"}, json={})),
    )
    with pytest.raises(ProviderRateLimited) as excinfo:
        await subject.complete(messages("hi"))
    assert excinfo.value.retry_after == 3.0


async def test_a_timeout_is_provider_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    subject = OllamaProvider(model=TEST_MODEL, client=client(handler))
    with pytest.raises(ProviderTimeout):
        await subject.complete(messages("hi"))


async def test_a_non_json_body_is_provider_down_not_a_parse_error() -> None:
    subject = OllamaProvider(
        model=TEST_MODEL, client=client(lambda r: httpx.Response(200, text="<html>nope</html>"))
    )
    with pytest.raises(ProviderDown):
        await subject.complete(messages("hi"))


async def test_aclose_does_not_close_a_client_the_caller_injected() -> None:
    """Whoever made the client closes it; the provider must not."""
    http = client(lambda r: chat_response())
    subject = OllamaProvider(model=TEST_MODEL, client=http)
    await subject.aclose()
    assert http.is_closed is False
    await http.aclose()


async def test_aclose_closes_a_client_the_provider_created() -> None:
    """Ownership is the point of the flag: a provider that built its own client
    must not leak it. The attribute is set directly because the client is
    created lazily on first use, and this test is about teardown."""
    http = client(lambda r: chat_response())
    subject = OllamaProvider(model=TEST_MODEL)
    subject._client = http
    await subject.aclose()
    assert http.is_closed is True
    assert subject._client is None


def test_describe_is_metadata_only() -> None:
    described = OllamaProvider(model=TEST_MODEL).describe()
    assert described["provider"] == "ollama"
    assert described["model"] == TEST_MODEL
    assert described["base_url"] == DEFAULT_BASE_URL
    assert described["configured"] is True


def test_describe_says_when_the_model_will_be_discovered() -> None:
    described = OllamaProvider().describe()
    assert "(discovered" in str(described["model"])
