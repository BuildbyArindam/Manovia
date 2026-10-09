"""Integration tests for the dev-only analysis endpoint.

Covers the three things that make this endpoint safe to ship: it exists only
outside production, it never logs the text it is given, and it degrades instead
of failing.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import structlog

from app.core.config import Settings
from app.core.logging import configure_logging
from app.main import create_app
from app.services.nlp.base import EMOTIONS

ANALYZE_URL = "/api/v1/dev/analyze"
STATE_URL = "/api/v1/dev/analyze/state"

#: The six cases the Day 6 verification asks for.
CASES: list[tuple[str, str]] = [
    ("I got the job and I can't stop smiling", "joy"),
    ("I feel so alone lately", "loneliness"),
    ("exam kal hai, bahut dar lag raha hai", "fear"),
    ("I am fine", "calm"),
    ("", "neutral"),
    ("x" * 10_000, "neutral"),
]


def dev_client(factory: Callable[..., httpx.AsyncClient]) -> httpx.AsyncClient:
    """A client whose app uses the keyword analyzer: no model, no network."""
    return factory(emotion_analyzer="keyword", rate_limit_enabled=False)


async def post(client: httpx.AsyncClient, text: str, **extra: Any) -> httpx.Response:
    return await client.post(ANALYZE_URL, json={"text": text, **extra})


class TestAnalyze:
    @pytest.mark.parametrize(("text", "expected"), CASES)
    async def test_the_verification_cases(
        self, client_factory: Callable[..., httpx.AsyncClient], text: str, expected: str
    ) -> None:
        client = dev_client(client_factory)
        response = await post(client, text)
        assert response.status_code == 200
        body = response.json()
        assert body["primary"] == expected
        assert body["primary"] in EMOTIONS
        assert -1.0 <= body["valence"] <= 1.0
        assert 0.0 <= body["arousal"] <= 1.0
        assert set(body["scores"]) == set(EMOTIONS)
        assert body["duration_ms"] >= 0

    async def test_a_positive_message_reads_positive(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        response = await post(dev_client(client_factory), "I got the job and I can't stop smiling")
        assert response.json()["valence"] > 0.5

    async def test_a_distressed_message_reads_negative(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        response = await post(dev_client(client_factory), "I feel so alone lately")
        body = response.json()
        assert body["valence"] < 0
        assert body["primary"] == "loneliness"

    async def test_hinglish_is_detected_and_reported(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        response = await post(dev_client(client_factory), "exam kal hai, bahut dar lag raha hai")
        language = response.json()["language"]
        assert language["lang"] == "hi"
        assert language["hinglish"] is True
        assert language["script"] == "latin"

    async def test_an_empty_string_is_an_answer_not_an_error(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        response = await post(dev_client(client_factory), "")
        assert response.status_code == 200
        body = response.json()
        assert body["primary"] == "neutral"
        assert body["valence"] == 0.0
        assert body["language"]["lang"] == "other"

    async def test_text_can_be_omitted_entirely(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        response = await dev_client(client_factory).post(ANALYZE_URL, json={})
        assert response.status_code == 200
        assert response.json()["primary"] == "neutral"

    async def test_a_very_long_message_is_handled(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        text = "I feel anxious about the exam. " * 400  # ~12,000 characters
        assert len(text) > 10_000
        response = await post(dev_client(client_factory), text[:10_000])
        assert response.status_code == 200
        assert response.json()["primary"] == "anxiety"

    async def test_text_beyond_the_hard_cap_is_a_validation_error(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        response = await post(dev_client(client_factory), "x" * 20_001)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    async def test_an_explicit_language_hint_overrides_detection(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        response = await post(dev_client(client_factory), "I am fine", lang="hi")
        body = response.json()
        # The report still says what was detected; the hint only steers analysis.
        assert body["language"]["lang"] == "en"

    async def test_an_invalid_payload_shape_is_rejected(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        client = dev_client(client_factory)
        assert (await client.post(ANALYZE_URL, json={"text": 42})).status_code == 422
        assert (await client.post(ANALYZE_URL, content="plain")).status_code == 422

    async def test_a_get_is_not_allowed(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        response = await dev_client(client_factory).get(ANALYZE_URL)
        assert response.status_code == 405


class TestCaching:
    async def test_the_second_identical_request_is_served_from_the_cache(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        client = dev_client(client_factory)
        first = await post(client, "I feel so alone lately")
        second = await post(client, "I feel so alone lately")
        assert first.json()["cached"] is False
        assert second.json()["cached"] is True
        assert first.json()["primary"] == second.json()["primary"]

    async def test_a_different_message_is_not_a_cache_hit(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        client = dev_client(client_factory)
        await post(client, "I feel so alone lately")
        response = await post(client, "I feel so angry lately")
        assert response.json()["cached"] is False

    async def test_cache_counters_are_visible_in_the_state(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        client = dev_client(client_factory)
        await post(client, "I am fine")
        state = (await client.get(STATE_URL)).json()
        assert state["cache"]["size"] >= 1
        assert state["cache"]["hits"] + state["cache"]["misses"] >= 1
        assert state["primary"] == "keyword"
        assert state["loaded"] is False


class TestAvailability:
    async def test_the_route_does_not_exist_in_production(
        self, settings: Settings, database: Any
    ) -> None:
        """Not 403, not 401: a production deployment must not confirm it exists."""
        production = settings.model_copy(
            update={"app_env": "production", "secret_key": settings.secret_key}
        )
        assert production.is_production is True
        app = create_app(production, database=database)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(ANALYZE_URL, json={"text": "hello"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_the_route_is_absent_from_the_production_schema(
        self, settings: Settings, database: Any
    ) -> None:
        production = settings.model_copy(
            update={"app_env": "production", "secret_key": settings.secret_key}
        )
        assert production.is_production is True
        app = create_app(production, database=database)
        assert ANALYZE_URL not in app.openapi()["paths"]

    async def test_the_route_is_present_outside_production(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        client = dev_client(client_factory)
        assert (await client.get("/openapi.json")).json()["paths"][ANALYZE_URL] is not None

    async def test_the_in_endpoint_guard_refuses_a_production_setting(
        self, settings: Settings
    ) -> None:
        """Defence in depth: the endpoint re-checks even though the router is
        never mounted in production, so this branch is not dead code."""
        from types import SimpleNamespace

        from app.api.v1.dev import _require_dev
        from app.core.errors import ApiError

        def request_for(app_settings: Settings) -> Any:
            return SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(settings=app_settings))
            )

        _require_dev(request_for(settings))  # development: no raise

        production = settings.model_copy(
            update={"app_env": "production", "secret_key": settings.secret_key}
        )
        with pytest.raises(ApiError) as excinfo:
            _require_dev(request_for(production))
        assert excinfo.value.status_code == 404
        assert excinfo.value.code == "not_found"

    async def test_the_state_endpoint_describes_a_bare_analyzer(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        """A non-chain analyzer (the fake) has no describe(); the endpoint must
        still answer rather than assume the chain shape."""
        client = client_factory(emotion_analyzer="fake", rate_limit_enabled=False)
        state = (await client.get(STATE_URL)).json()
        assert state["analyzer"] == "fake"
        assert state["model"] == "fake-deterministic-keyword"
        assert state["loaded"] is False


class TestGracefulDegradation:
    async def test_an_unloadable_model_falls_back_instead_of_failing(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        """``EMOTION_ANALYZER=auto`` with no downloadable model still answers."""
        client = client_factory(emotion_analyzer="auto", rate_limit_enabled=False)
        response = await post(client, "I feel so alone lately")
        assert response.status_code == 200
        body = response.json()
        assert body["primary"] == "loneliness"
        assert body["analyzer"] == "keyword"

    async def test_the_state_endpoint_reports_the_chain(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        client = client_factory(emotion_analyzer="auto", rate_limit_enabled=False)
        state = (await client.get(STATE_URL)).json()
        assert state["primary"] == "hf"
        assert state["fallbacks"] == ["keyword", "sentiment"]
        assert state["health"]["state"] in {"closed", "open", "half_open"}


class TestLogging:
    async def test_the_text_is_never_logged(
        self, client_factory: Callable[..., httpx.AsyncClient]
    ) -> None:
        """AGENTS.md rule 5, checked over the whole request."""
        sentinel = "SENTINEL-I-felt-completely-alone-9d2c"
        # Build the app first: create_app() reconfigures structlog to stdout,
        # which would discard the capture buffer installed below.
        client = dev_client(client_factory)
        buffer = io.StringIO()
        configure_logging()
        structlog.configure(logger_factory=structlog.PrintLoggerFactory(buffer))
        try:
            assert (await post(client, sentinel)).status_code == 200
            assert (await post(client, sentinel)).status_code == 200
            assert (await post(client, "")).status_code == 200
            assert (await client.get(STATE_URL)).status_code == 200
        finally:
            configure_logging()

        output = buffer.getvalue()
        assert sentinel not in output
        assert "alone" not in output.lower()

        records = [json.loads(line) for line in output.strip().splitlines()]
        analyzed = [record for record in records if record.get("event") == "emotion_analyzed"]
        assert len(analyzed) == 3
        for record in analyzed:
            # A fingerprint and a length, never the message.
            assert len(record["text_sha"]) == 16
            assert record["text_length"] >= 0
            assert "primary" in record and "valence" in record
            assert "text" not in record

    async def test_a_failing_analyzer_logs_a_fingerprint_not_the_text(self) -> None:
        """The warning path is where a careless implementation would leak."""
        from app.services.nlp.base import EmotionAnalyzer, build_result
        from app.services.nlp.chain import AnalyzerChain

        class Exploding(EmotionAnalyzer):
            name = "exploding"

            def analyze(self, text: str, lang: str | None = None) -> Any:
                raise RuntimeError("boom")

        class Backup(EmotionAnalyzer):
            name = "backup"

            def analyze(self, text: str, lang: str | None = None) -> Any:
                return build_result({"sadness": 0.9}, analyzer="backup", confidence=0.9)

        sentinel = "SENTINEL-nobody-must-log-this-4f8a"
        buffer = io.StringIO()
        configure_logging()
        structlog.configure(logger_factory=structlog.PrintLoggerFactory(buffer))
        try:
            AnalyzerChain(Exploding(), [Backup()]).analyze(sentinel, "en")
        finally:
            configure_logging()

        output = buffer.getvalue()
        assert sentinel not in output
        assert "boom" not in output
        assert "emotion_analyzer_failed" in output
