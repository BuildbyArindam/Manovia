"""Dev route is offline via an injected Fake and absent from production."""

from collections.abc import Callable

import httpx
import pytest
from fastapi import FastAPI

from app.services.nlp import FakeEmotionAnalyzer, KeywordFallbackAnalyzer


@pytest.mark.parametrize(
    "text", ["happy", "alone", "exam kal hai bahut dar lag raha hai", "", "x" * 10000]
)
async def test_dev_analyze(client: httpx.AsyncClient, app: FastAPI, text: str) -> None:
    assert isinstance(app.state.emotion_service.analyzer, FakeEmotionAnalyzer)
    response = await client.post("/api/v1/dev/analyze", json={"text": text})
    assert response.status_code == 200
    assert response.json() == KeywordFallbackAnalyzer().analyze(text).model_dump()


async def test_validation(client: httpx.AsyncClient) -> None:
    for payload in [{}, {"text": None}, {"text": "x" * 10001}, {"text": "hi", "lang": "fr"}]:
        response = await client.post("/api/v1/dev/analyze", json=payload)
        assert response.status_code == 422


async def test_production_404(client_factory: Callable[..., httpx.AsyncClient]) -> None:
    client = client_factory(app_env=" Production ")
    response = await client.post("/api/v1/dev/analyze", json={"text": "happy"})
    assert response.status_code == 404
    schema = (await client.get("/openapi.json")).json()
    assert "/api/v1/dev/analyze" not in schema["paths"]
