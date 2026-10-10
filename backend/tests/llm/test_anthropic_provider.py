"""The Anthropic provider, driven by a stub that speaks the SDK's shape.

What is worth testing here is not the SDK — it is *our* code around it: that the
model id comes from the environment and never from a literal, that the system
prompt travels as Anthropic's separate ``system`` parameter, that the response is
folded into our result shape, and above all that **every SDK failure lands on
the right side of the retryable line**. A 429 must be retried; a 401 must not.

The error translation is matched on exception *name* and ``status_code`` rather
than by ``isinstance``, so it works with or without the SDK installed. Both
paths are tested: duck-typed stubs always, and the real SDK classes when the
``llm`` extra happens to be present.
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.llm.anthropic_provider import AnthropicProvider, _translate
from app.services.llm.base import (
    LLMMessage,
    ProviderBadRequest,
    ProviderDown,
    ProviderNotAvailable,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
)

from .conftest import messages

#: Whether the optional ``llm`` extra is installed. The provider must work
#: either way, so the real-SDK test below skips when it is not.
ANTHROPIC_INSTALLED: bool = importlib.util.find_spec("anthropic") is not None

# A model id used only by tests. Nothing in the application code names a model.
TEST_MODEL = "claude-sonnet-4-5"


def block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def response(text: str = "I hear you.", *, model: str = TEST_MODEL) -> SimpleNamespace:
    return SimpleNamespace(
        content=[block(text)],
        usage=SimpleNamespace(input_tokens=42, output_tokens=7),
        model=model,
        stop_reason="end_turn",
    )


class StubMessages:
    """``client.messages`` — records what it was asked for."""

    def __init__(self, *, result: Any = None, error: Exception | None = None) -> None:
        self._result = result if result is not None else response()
        self._error = error
        self.calls: list[dict[str, Any]] = []
        self.streams: list[dict[str, Any]] = []

    async def create(self, **payload: Any) -> Any:
        self.calls.append(dict(payload))
        if self._error is not None:
            raise self._error
        return self._result

    def stream(self, **payload: Any) -> StubStream:
        self.streams.append(dict(payload))
        if self._error is not None:
            raise self._error
        return StubStream(self._result)


class StubStream:
    """``client.messages.stream(...)`` as an async context manager."""

    def __init__(self, result: Any) -> None:
        self._result = result

    async def __aenter__(self) -> StubStream:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    @property
    def text_stream(self) -> Any:
        async def chunks() -> Any:
            for block in self._result.content:
                if getattr(block, "type", None) == "text":
                    for piece in block.text.split(" "):
                        yield piece + " "

        return chunks()


class StubClient:
    def __init__(self, **kwargs: Any) -> None:
        self.messages = StubMessages(**kwargs)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def provider(**kwargs: Any) -> AnthropicProvider:
    """A provider with a stub client, a test model id and a fake key."""
    defaults: dict[str, Any] = dict(api_key="test-key-not-real", model=TEST_MODEL)
    defaults.update(kwargs)
    return AnthropicProvider(**defaults)


# --- configuration -----------------------------------------------------------


def test_the_model_id_comes_from_the_environment_and_has_no_default() -> None:
    assert AnthropicProvider(api_key="k").is_configured is False
    assert AnthropicProvider(api_key="k").model_id is None
    assert AnthropicProvider(model=TEST_MODEL).is_configured is False


def test_unconfigured_reasons_never_mention_the_key() -> None:
    """These strings are logged at startup and on every fallback."""
    assert AnthropicProvider(api_key="k").configure_reason() == "ANTHROPIC_MODEL is not set"
    assert AnthropicProvider(model=TEST_MODEL).configure_reason() == "ANTHROPIC_API_KEY is not set"
    assert AnthropicProvider(api_key="   ", model=TEST_MODEL).is_configured is False
    assert AnthropicProvider(api_key="k", model=TEST_MODEL).configure_reason() is None


@pytest.mark.parametrize("api_key", ["", "   ", None])
async def test_completing_without_credentials_raises_not_configured(api_key: str | None) -> None:
    with pytest.raises(ProviderNotConfigured):
        await AnthropicProvider(api_key=api_key, model=TEST_MODEL).complete(messages("hi"))
    with pytest.raises(ProviderNotConfigured):
        await AnthropicProvider(api_key="k", model=None).complete(messages("hi"))


def test_describe_omits_the_key_entirely() -> None:
    described = provider(api_key="super-secret-value").describe()
    assert described["model"] == TEST_MODEL
    assert described["configured"] is True
    assert "super-secret-value" not in str(described)
    assert "api_key" not in described


# --- request shape -----------------------------------------------------------


async def test_the_payload_carries_the_env_model_and_a_separate_system() -> None:
    client = StubClient()
    subject = provider(client=client)
    await subject.complete(
        [LLMMessage(role="user", content="hello"), LLMMessage(role="assistant", content="hi")],
        system="be kind",
        max_tokens=120,
        temperature=0.2,
    )
    payload = client.messages.calls[0]
    assert payload["model"] == TEST_MODEL
    assert payload["system"] == "be kind"  # not a message in the list
    assert payload["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert payload["max_tokens"] == 120
    assert payload["temperature"] == 0.2


async def test_no_system_prompt_means_no_system_key() -> None:
    client = StubClient()
    await provider(client=client).complete(messages("hi"))
    assert "system" not in client.messages.calls[0]


async def test_an_empty_conversation_is_a_bad_request_not_a_crash() -> None:
    from app.services.llm.base import ProviderBadRequest as BadRequest

    subject = provider(client=StubClient())
    with pytest.raises(BadRequest):
        await subject.complete([])


async def test_the_response_is_folded_into_our_result_shape() -> None:
    client = StubClient(result=response("Take your time."))
    result = await provider(client=client).complete(messages("hi"))
    assert result.text == "Take your time."
    assert result.provider == "anthropic"
    assert result.model == TEST_MODEL
    assert result.finish_reason == "end_turn"
    assert result.usage is not None
    assert result.usage.input_tokens == 42
    assert result.usage.output_tokens == 7
    assert result.usage.total_tokens == 49
    assert result.latency_ms >= 0.0


async def test_non_text_blocks_are_ignored_rather_than_crashing() -> None:
    client = StubClient(
        result=SimpleNamespace(
            content=[SimpleNamespace(type="thinking", text="hmm"), block("visible")],
            usage=None,
            model=TEST_MODEL,
            stop_reason=None,
        )
    )
    result = await provider(client=client).complete(messages("hi"))
    assert result.text == "visible"
    assert result.usage is not None
    assert result.usage.input_tokens is None


async def test_streaming_yields_text_fragments() -> None:
    client = StubClient()
    subject = provider(client=client)
    parts = [chunk async for chunk in subject.stream(messages("hi"))]
    assert "".join(parts).strip() == "I hear you."
    assert client.messages.streams[0]["model"] == TEST_MODEL


async def test_closing_releases_an_owned_client() -> None:
    client = StubClient()
    subject = AnthropicProvider(api_key="k", model=TEST_MODEL, client=client)
    await subject.aclose()
    # A client the caller injected is theirs to close, not ours.
    assert client.closed is False
    assert subject._client is None


# --- error translation -------------------------------------------------------


def _named(name: str, status: int | None = None, **attrs: Any) -> Exception:
    """Build a duck-typed stand-in for an SDK error class."""
    exc: Exception = type(name, (Exception,), {})("boom")
    if status is not None:
        exc.status_code = status  # type: ignore[attr-defined]
    for key, value in attrs.items():
        setattr(exc, key, value)
    return exc


@pytest.mark.parametrize(
    ("name", "status", "expected"),
    [
        ("APITimeoutError", None, ProviderTimeout),
        ("DeadlineExceededError", None, ProviderTimeout),
        ("RateLimitError", 429, ProviderRateLimited),
        ("APIConnectionError", None, ProviderDown),
        ("InternalServerError", 500, ProviderDown),
        ("ServiceUnavailableError", 503, ProviderDown),
        ("OverloadedError", 529, ProviderDown),
        ("AuthenticationError", 401, ProviderBadRequest),
        ("PermissionDeniedError", 403, ProviderBadRequest),
        ("BadRequestError", 400, ProviderBadRequest),
        ("NotFoundError", 404, ProviderBadRequest),
    ],
)
def test_sdk_errors_land_on_the_right_side_of_the_retry_line(
    name: str, status: int | None, expected: type
) -> None:
    translated = _translate(_named(name, status), timeout_seconds=15.0)
    assert isinstance(translated, expected)


def test_a_rate_limit_carries_retry_after_when_the_sdk_reported_it() -> None:
    headers = {"retry-after": "2.5"}
    exc = _named("RateLimitError", 429, response=SimpleNamespace(headers=headers))
    translated = _translate(exc, timeout_seconds=15.0)
    assert isinstance(translated, ProviderRateLimited)
    assert translated.retry_after == 2.5


def test_an_unparseable_retry_after_is_ignored_not_fatal() -> None:
    exc = _named("RateLimitError", 429, response=SimpleNamespace(headers={"retry-after": "soon"}))
    translated = _translate(exc, timeout_seconds=15.0)
    assert isinstance(translated, ProviderRateLimited)
    assert translated.retry_after is None


def test_an_unknown_exception_becomes_provider_down() -> None:
    translated = _translate(RuntimeError("something new"), timeout_seconds=15.0)
    assert isinstance(translated, ProviderDown)
    # The exception's own text is not propagated: it can echo request content.
    assert "something new" not in str(translated)


def test_an_llm_error_passes_through_untouched() -> None:
    original = ProviderBadRequest("ours")
    assert _translate(original, timeout_seconds=1.0) is original


async def test_a_missing_sdk_is_provider_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``llm`` extra is optional: without it the chain must fall through,
    not crash at import and not raise an ImportError into a request."""
    import builtins
    import sys

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> Any:
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    subject = AnthropicProvider(api_key="k", model=TEST_MODEL)
    assert subject.is_configured is True  # configured as far as settings go
    with pytest.raises(ProviderNotAvailable):
        await subject.complete(messages("hi"))


@pytest.mark.skipif(not ANTHROPIC_INSTALLED, reason="the optional llm extra is not installed")
def test_the_real_sdk_error_classes_translate_correctly() -> None:
    """When the SDK *is* installed, check the real class names: the stubs above
    only prove our name matching works, not that the names are right."""
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def with_status(status: int) -> Any:
        return SimpleNamespace(status_code=status, headers={}, request=request)

    cases: list[tuple[str, Exception, type]] = [
        (
            "APITimeoutError",
            anthropic.APITimeoutError(request=request),
            ProviderTimeout,
        ),
        (
            "RateLimitError",
            anthropic.RateLimitError("boom", response=with_status(429), body=None),
            ProviderRateLimited,
        ),
        (
            "APIConnectionError",
            anthropic.APIConnectionError(request=request),
            ProviderDown,
        ),
        (
            "InternalServerError",
            anthropic.InternalServerError("boom", response=with_status(500), body=None),
            ProviderDown,
        ),
        (
            "AuthenticationError",
            anthropic.AuthenticationError("boom", response=with_status(401), body=None),
            ProviderBadRequest,
        ),
        (
            "BadRequestError",
            anthropic.BadRequestError("boom", response=with_status(400), body=None),
            ProviderBadRequest,
        ),
    ]
    for name, instance, expected in cases:
        translated = _translate(instance, timeout_seconds=1.0)
        assert isinstance(translated, expected), (
            f"{name} translated to {type(translated).__name__}, expected {expected.__name__}"
        )
