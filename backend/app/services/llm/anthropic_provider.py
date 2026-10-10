"""Anthropic Messages API, behind :class:`LLMProvider`.

Two rules this file obeys strictly, both from AGENTS.md:

* **no hard-coded model id.** The model is ``ANTHROPIC_MODEL`` from the
  environment or it is not used at all — :class:`ProviderNotConfigured` and the
  chain moves on. A default model baked into this file would quietly start
  costing money the day somebody forgets to set it, and would make "which model
  answered?" unanswerable from config alone.
* **no message text in logs or errors.** Failures carry a request fingerprint
  (roles + lengths) and nothing else.

The ``anthropic`` SDK is imported **lazily, inside the client factory**. The
test suite, CI, and any deployment that has not installed the ``llm`` extra must
be able to import this module, construct the provider, and observe
``is_configured is False`` — not crash at import time.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import structlog

from app.services.llm.base import (
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
    transcript,
)

#: Anthropic error classes we translate. Looked up by name at raise time so the
#: SDK never has to be importable for this module to load.
_ERROR_STATUS: dict[int, type[LLMError]] = {
    400: ProviderBadRequest,
    401: ProviderBadRequest,
    403: ProviderBadRequest,
    404: ProviderBadRequest,
    422: ProviderBadRequest,
    429: ProviderRateLimited,
    500: ProviderDown,
    502: ProviderDown,
    503: ProviderDown,
    529: ProviderDown,
}


class AnthropicProvider(LLMProvider):
    """``messages.create`` / ``messages.stream`` behind the LLM interface."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 15.0,
        client: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = (model or "").strip() or None
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._clock = clock or time.perf_counter
        # Injected clients exist so tests can drive this provider with a stub
        # that speaks the SDK's shape, with no network and no key.
        self._client = client
        self._owns_client = client is None

    @property
    def model_id(self) -> str | None:
        return self._model

    @property
    def api_key(self) -> str | None:
        """Never logged, never included in :meth:`describe`."""
        return self._api_key

    @property
    def is_configured(self) -> bool:
        """A key and a model id are both required; neither has a default."""
        return bool(self._api_key and self._api_key.strip()) and self._model is not None

    def configure_reason(self) -> str | None:
        """Why :attr:`is_configured` is False, or ``None`` when it is True.

        This string is logged at startup and on fallback. It must therefore
        never contain the key or any part of it.
        """
        if self._model is None:
            return "ANTHROPIC_MODEL is not set"
        if not self._api_key or not self._api_key.strip():
            return "ANTHROPIC_API_KEY is not set"
        return None

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "model": self._model,
            "configured": self.is_configured,
            # Deliberately absent: the key, its length, its prefix.
            "unconfigured_reason": self.configure_reason(),
        }

    # --- client management ---

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise ProviderNotAvailable(
                "the anthropic SDK is not installed (pip install -e '.[llm]')"
            ) from exc
        self._client = AsyncAnthropic(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            # The SDK retries internally; ours is the outer, jittered one and
            # it owns the deadline, so the inner loop is turned off.
            max_retries=0,
        )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            close = getattr(self._client, "close", None)
            if callable(close):
                result = close()
                if asyncio.iscoroutine(result):
                    await result
        self._client = None

    # --- the interface ---

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> LLMResult:
        if not self.is_configured:
            raise ProviderNotConfigured(self.configure_reason() or "not configured")
        client = self._get_client()
        payload = self._payload(
            messages, system=system, max_tokens=max_tokens, temperature=temperature
        )
        started = self._clock()
        try:
            response = await client.messages.create(**payload)
        except Exception as exc:
            raise _translate(exc, timeout_seconds=self._timeout_seconds) from exc
        return self._result(response, started=started)

    def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Validate eagerly, stream lazily.

        A plain ``def`` rather than an async generator: an unconfigured provider
        must fail the moment it is called, not the moment somebody starts
        reading the first token. The request itself still starts on the first
        ``__anext__``, which is what streaming means.
        """
        if not self.is_configured:
            raise ProviderNotConfigured(self.configure_reason() or "not configured")
        client = self._get_client()
        payload = self._payload(
            messages, system=system, max_tokens=max_tokens, temperature=temperature
        )
        return self._stream(client, payload)

    async def _stream(self, client: Any, payload: dict[str, Any]) -> AsyncIterator[str]:
        try:
            async with client.messages.stream(**payload) as stream:
                async for chunk in stream.text_stream:
                    if chunk:
                        yield chunk
        except Exception as exc:
            raise _translate(exc, timeout_seconds=self._timeout_seconds) from exc

    # --- internals ---

    def _payload(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Build the request body.

        ``system`` is passed as Anthropic's separate ``system`` parameter rather
        than as a ``system`` message, so the versioned prompt and the
        conversation stay separable in logs and in provider dashboards.
        """
        if not messages:
            raise ProviderBadRequest("at least one message is required")
        payload: dict[str, Any] = {
            # No default: an unset ANTHROPIC_MODEL is a configuration error,
            # and guessing a model id here would be a silent cost decision.
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if system:
            payload["system"] = system
        return payload

    def _result(self, response: Any, *, started: float) -> LLMResult:
        """Fold an SDK response into an :class:`LLMResult`.

        Everything is read through ``getattr`` so a stub response works, and so
        a future SDK shape that renames a field degrades to ``None`` instead of
        raising inside the happy path.
        """
        blocks = list(getattr(response, "content", None) or [])
        text = "".join(block.text for block in blocks if getattr(block, "type", None) == "text")
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        return LLMResult(
            text=text,
            provider=self.name,
            model=getattr(response, "model", None) or self._model,
            finish_reason=getattr(response, "stop_reason", None),
            usage=LLMUsage(
                input_tokens=int(input_tokens) if input_tokens is not None else None,
                output_tokens=int(output_tokens) if output_tokens is not None else None,
            ),
            latency_ms=round(max(0.0, (self._clock() - started) * 1000.0), 3),
        )


def _translate(exc: Exception, *, timeout_seconds: float) -> LLMError:
    """Map an SDK exception onto the LLM error tree.

    Imported lazily by name: this module must work without the SDK present, so
    the exception classes are matched on their *names* and on ``status_code``
    rather than by ``isinstance`` against imported symbols.
    """
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)

    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or name in {
        "APITimeoutError",
        "DeadlineExceededError",
    }:
        return ProviderTimeout(f"anthropic timed out after {timeout_seconds:.2f}s")

    if name == "RateLimitError" or status == 429:
        retry_after = _retry_after_seconds(exc)
        error = ProviderRateLimited("anthropic rate limited", retry_after=retry_after)
        return error

    if name == "APIConnectionError":
        return ProviderDown("anthropic connection failed")

    if status is not None:
        kind = _ERROR_STATUS.get(int(status))
        if kind is not None:
            if kind is ProviderRateLimited:
                retry_after = _retry_after_seconds(exc)
                return ProviderRateLimited("anthropic rate limited", retry_after=retry_after)
            return kind(f"anthropic returned HTTP {int(status)}")

    if name in {"AuthenticationError", "PermissionDeniedError", "NotFoundError"}:
        # A bad key is not retryable, and it is not a 500: the chain must fall
        # through to Ollama/canned rather than hammering a rejected credential.
        return ProviderBadRequest("anthropic rejected the credentials")

    if isinstance(exc, LLMError):
        return exc

    # Unknown SDK exception: treat as an outage and log the type, never the
    # exception body (it can echo request content).
    structlog.get_logger().warning("anthropic_unknown_error", error_type=name, status_code=status)
    return ProviderDown(f"anthropic raised {name}")


def _retry_after_seconds(exc: Exception) -> float | None:
    """Read ``Retry-After`` from an SDK exception, if it carried one."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:  # pragma: no cover - a headers object with no .get
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def fingerprint(messages: Sequence[LLMMessage]) -> str:
    """A stable, text-free identifier for a request (for logs only)."""
    return transcript(messages)
