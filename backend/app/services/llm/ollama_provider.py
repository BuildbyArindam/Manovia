"""Ollama (local HTTP) provider — the first fallback.

Ollama is the "the network is down but the box is fine" path: a model running
on localhost, no data leaving the machine, no per-token bill. It is slower and
blunter than a hosted model, which is exactly why it is a *fallback* rather
than a peer.

It speaks Ollama's ``/api/chat`` endpoint over plain ``httpx`` (already a
dependency — no extra package to install, so the fallback path is always
available). Streaming uses NDJSON: one JSON object per line, each with a
``message.content`` fragment.

``OLLAMA_MODEL`` is optional on purpose. If it is unset the provider asks the
server what it has (``/api/tags``) and uses the first model listed; a local
developer running one model should not have to spell its name twice. In
production it should be set, so which model answered is a config fact.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import structlog

from app.services.llm.base import (
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMResult,
    LLMUsage,
    ProviderBadRequest,
    ProviderDown,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderTimeout,
)

#: Default base URL when ``OLLAMA_BASE_URL`` is unset. Localhost only: this
#: provider exists to keep a conversation alive on one machine, and pointing it
#: at a remote host by default would silently export user text.
DEFAULT_BASE_URL: str = "http://localhost:11434"

#: Ollama reports ``prompt_eval_count`` / ``eval_count`` instead of tokens.
_CHAT_PATH: str = "/api/chat"
_TAGS_PATH: str = "/api/tags"


class OllamaProvider(LLMProvider):
    """``POST {base_url}/api/chat`` behind the LLM interface."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
        clock: Any | None = None,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._model = (model or "").strip() or None
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None
        self._clock = clock or time.perf_counter

    @property
    def model_id(self) -> str | None:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def is_configured(self) -> bool:
        """A base URL is enough; the model can be discovered from the server."""
        return bool(self._base_url.strip())

    def describe(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "base_url": self._base_url,
            "model": self._model or "(discovered from /api/tags)",
            "configured": self.is_configured,
        }

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
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
            raise ProviderNotConfigured("OLLAMA_BASE_URL is not set")
        if not messages:
            raise ProviderBadRequest("at least one message is required")
        model = await self._resolve_model()
        payload = self._payload(
            messages, system=system, max_tokens=max_tokens, temperature=temperature
        )
        payload["model"] = model
        payload["stream"] = False
        started = self._clock()
        data = await self._request("POST", _CHAT_PATH, payload)
        return self._result(data, model=model, started=started)

    def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Validate eagerly, then let :meth:`_stream` read the NDJSON body.

        Model discovery needs an ``await`` so it happens inside
        :meth:`_stream`, but the two checks that need no I/O happen here, on
        call, where a caller (and a test) can see them.
        """
        if not self.is_configured:
            raise ProviderNotConfigured("OLLAMA_BASE_URL is not set")
        if not messages:
            raise ProviderBadRequest("at least one message is required")
        payload = self._payload(
            messages, system=system, max_tokens=max_tokens, temperature=temperature
        )
        payload["stream"] = True
        return self._stream(payload)

    async def _stream(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        model = await self._resolve_model()
        payload["model"] = model
        client = self._get_client()
        try:
            async with client.stream(
                "POST", f"{self._base_url}{_CHAT_PATH}", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    chunk = _parse_stream_line(stripped)
                    if chunk:
                        yield chunk
        except Exception as exc:
            raise _translate(exc) from exc

    # --- internals ---

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    def _payload(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        """Build the ``/api/chat`` body.

        Ollama has no separate system parameter: the system prompt is a
        ``system`` message at the head of the list. Because
        :mod:`app.services.llm.prompts` renders it as a plain string (never
        templated with user text), prepending it is safe.
        """
        payload_messages: list[dict[str, str]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(
            {"role": message.role, "content": message.content} for message in messages
        )
        return {
            "messages": payload_messages,
            "stream": False,
            "options": {
                # Ollama calls the output cap ``num_predict``.
                "num_predict": int(max_tokens),
                "temperature": float(temperature),
            },
        }

    async def _resolve_model(self) -> str:
        """Return the configured model, or the first one the server reports."""
        if self._model:
            return self._model
        data = await self._request("GET", _TAGS_PATH, None)
        models = data.get("models") if isinstance(data, dict) else None
        if isinstance(models, list):
            for entry in models:
                if isinstance(entry, dict):
                    name = entry.get("name") or entry.get("model")
                    if isinstance(name, str) and name.strip():
                        # Remember it: one discovery per process is enough, and
                        # it makes `describe()` honest after the first call.
                        self._model = name.strip()
                        return self._model
        raise ProviderDown("ollama reported no local models")

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        client = self._get_client()
        try:
            if method == "GET":
                response = await client.get(f"{self._base_url}{path}")
            else:
                response = await client.post(f"{self._base_url}{path}", json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise _translate(exc) from exc
        try:
            data: Any = response.json()
        except ValueError as exc:
            raise ProviderDown("ollama returned a non-JSON body") from exc
        if not isinstance(data, dict):
            raise ProviderDown("ollama returned an unexpected body shape")
        return data

    def _result(self, data: dict[str, Any], *, model: str, started: float) -> LLMResult:
        message = data.get("message")
        text = ""
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                text = content
        return LLMResult(
            text=text,
            provider=self.name,
            model=data.get("model") or model,
            finish_reason=data.get("done_reason"),
            usage=LLMUsage(
                input_tokens=_as_int(data.get("prompt_eval_count")),
                output_tokens=_as_int(data.get("eval_count")),
            ),
            latency_ms=round(max(0.0, (self._clock() - started) * 1000.0), 3),
            raw={"done": bool(data.get("done", True))},
        )


def _as_int(value: Any) -> int | None:
    """Ollama omits the counts when ``stream`` was used; ``None`` then."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _parse_stream_line(line: str) -> str:
    """Pull ``message.content`` out of one NDJSON line, tolerating junk."""
    try:
        data: Any = json.loads(line)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    message = data.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def _translate(exc: Exception) -> LLMError:
    """Map ``httpx`` (and JSON) failures onto the LLM error tree."""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeout("ollama timed out")
    if isinstance(exc, httpx.ConnectError):
        # The common case: no Ollama running. This is the expected degraded
        # state, so it is logged at info, not warning, by the chain.
        return ProviderDown("ollama is not reachable")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return ProviderRateLimited("ollama rate limited", retry_after=_retry_after(exc))
        if status in {400, 404, 422}:
            return ProviderBadRequest(f"ollama returned HTTP {status}")
        return ProviderDown(f"ollama returned HTTP {status}")
    if isinstance(exc, httpx.HTTPError):
        return ProviderDown(f"ollama request failed ({type(exc).__name__})")
    if isinstance(exc, LLMError):
        return exc
    structlog.get_logger().warning("ollama_unknown_error", error_type=type(exc).__name__)
    return ProviderDown(f"ollama raised {type(exc).__name__}")


def _retry_after(exc: httpx.HTTPStatusError) -> float | None:
    raw = exc.response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
