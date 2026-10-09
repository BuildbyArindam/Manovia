"""Offline contracts, mapping, bounded execution, privacy and degradation regressions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import configure_logging, drop_sensitive_fields
from app.services.nlp import (
    EmotionAnalyzer,
    EmotionResult,
    EmotionService,
    FakeEmotionAnalyzer,
    HFEmotionAnalyzer,
    KeywordFallbackAnalyzer,
    SentimentAnalyzer,
)
from app.services.nlp.base import TAXONOMY, from_scores
from app.services.nlp.hf import LABEL_MAP
from app.services.nlp.language import detect_language
from app.services.nlp.service import build_emotion_service


@pytest.mark.parametrize("analyzer", [FakeEmotionAnalyzer(), KeywordFallbackAnalyzer()])
@pytest.mark.parametrize(
    ("text", "primary"),
    [
        ("I got the job and I can't stop smiling", "joy"),
        ("I feel so alone lately", "loneliness"),
        ("exam kal hai, bahut dar lag raha hai", "fear"),
        ("I am fine", "neutral"),
        ("", "neutral"),
        ("x" * 10000, "neutral"),
        ("not happy", "neutral"),
        ("unhappy", "sadness"),
        ("scared", "fear"),
        ("calm", "calm"),
        ("ashamed", "shame"),
        ("worried", "anxiety"),
        ("angry", "anger"),
        ("खुश", "joy"),
        ("একা", "loneliness"),
    ],
)
def test_keywords(analyzer: EmotionAnalyzer, text: str, primary: str) -> None:
    result = analyzer.analyze(text)
    assert result.primary == primary
    assert set(result.scores) == set(TAXONOMY)
    assert -1 <= result.valence <= 1
    assert 0 <= result.arousal <= 1
    assert analyzer.analyze_batch([text])[0] == result


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I feel so alone lately", "en"),
        ("I am fine", "en"),
        ("exam kal hai, bahut dar lag raha hai", "hi"),
        ("मैं खुश हूँ", "hi"),
        ("আমি ভালো আছি", "bn"),
        ("", "other"),
        ("😀123", "other"),
        ("Bonjour tout le monde, je parle français", "other"),
    ],
)
def test_language(text: str, expected: str) -> None:
    assert detect_language(text) == expected
    assert detect_language(text) == expected


def test_sentiment() -> None:
    analyzer = SentimentAnalyzer()
    assert analyzer.analyze("happy") > 0
    assert analyzer.analyze("sad") < 0
    assert analyzer.analyze("fine") == 0


def test_result_validation() -> None:
    with pytest.raises(ValidationError):
        EmotionResult(primary="joy", scores={"joy": 2}, valence=0, arousal=0)
    with pytest.raises(ValidationError):
        EmotionResult(primary="joy", scores={}, valence=float("nan"), arousal=0)
    assert from_scores({"joy": 2}).scores["joy"] == 1


def test_hf_batch_lazy_cpu_mapping() -> None:
    calls: list[dict[str, Any]] = []
    loads: list[str] = []

    def pipeline(texts: list[str], **kwargs: Any) -> list[list[dict[str, Any]]]:
        calls.append(kwargs)
        assert all(len(text) <= 10000 for text in texts)
        return [
            [
                {"label": "joy", "score": 0.6},
                {"label": "love", "score": 0.8},
                {"label": "sadness", "score": 0.4},
                {"label": "UNKNOWN", "score": 1},
            ]
            for _ in texts
        ]

    def loader(model_id: str) -> Any:
        loads.append(model_id)
        return pipeline

    analyzer = HFEmotionAnalyzer("fixture", loader=loader)
    assert loads == []
    assert analyzer.analyze_batch([]) == []
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(analyzer.analyze, ["x" * 20000] * 4))
    assert loads == ["fixture"]
    assert results[0].primary == "joy"
    assert results[0].scores["joy"] == 0.8
    assert results[0].scores["loneliness"] == 0
    assert calls[0] == dict(
        truncation=True, max_length=128, batch_size=4, top_k=None, function_to_apply="sigmoid"
    )
    assert len(analyzer.analyze_batch(["a", "b"])) == 2
    assert len(LABEL_MAP) == 28


def test_hf_failed_load_only_once() -> None:
    calls = 0

    def loader(_: str) -> Any:
        nonlocal calls
        calls += 1
        raise OSError("private raw text from provider")

    analyzer = HFEmotionAnalyzer("fixture", loader=loader)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="emotion_model"):
            analyzer.analyze("happy")
    assert calls == 1


@pytest.mark.parametrize("rows", [[], [[{"label": "LABEL_0", "score": 0.7}]]])
def test_hf_rejects_bad_output(rows: list[Any]) -> None:
    analyzer = HFEmotionAnalyzer("fixture", loader=lambda _: lambda *a, **k: rows)
    with pytest.raises(ValueError):
        analyzer.analyze("hello")


def test_fallback_failure_privacy(capsys: pytest.CaptureFixture[str]) -> None:
    class Broken(EmotionAnalyzer):
        def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
            raise ValueError(text)

    configure_logging("DEBUG")
    service = EmotionService(Broken())
    secret = "happy PRIVATE-SENTINEL-18375"
    assert service.analyze(secret, "en").primary == "joy"
    service.analyze(secret, "en")
    logs = capsys.readouterr().out
    assert "emotion_fallback" in logs
    assert "emotion_cache_hit" in logs
    assert "text_hash" in logs
    assert secret not in logs
    assert "PRIVATE-SENTINEL" not in logs
    # Restore the logger to the real stdout before capsys closes its stream.
    import sys

    with capsys.disabled():
        configure_logging("INFO")
    assert sys.stdout is not None
    assert drop_sensitive_fields(None, "info", {"text": secret, "nested": {"text": secret}}) == {
        "nested": {}
    }


def test_timeout_and_busy_are_bounded() -> None:
    started, release, finished = Event(), Event(), Event()

    class Slow(EmotionAnalyzer):
        def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
            started.set()
            release.wait(5)
            finished.set()
            return from_scores({"joy": 1})

    service = EmotionService(Slow(), timeout=0.01)
    try:
        assert service.analyze("sad", "en").primary == "sadness"
        assert started.is_set()
        assert service.analyze("alone", "en").primary == "loneliness"
        assert service._worker_lock.locked()
    finally:
        release.set()
        assert finished.wait(1)


def test_lru_hash_language_and_copy() -> None:
    class Counting(FakeEmotionAnalyzer):
        calls = 0

        def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
            self.calls += 1
            return super().analyze(text, lang)

    fake = Counting()
    service = EmotionService(fake, cache_size=2)
    result = service.analyze("happy", "en")
    result.scores["joy"] = 0
    assert service.analyze("happy", "en").scores["joy"] == 0.9
    service.analyze("sad", "en")
    service.analyze("happy", "en")  # promote to MRU
    service.analyze("alone", "en")  # evicts sad
    service.analyze("sad", "en")
    assert fake.calls == 4
    service.analyze("sad", "hi")
    assert fake.calls == 5
    assert all(len(key) == 64 and "sad" not in key for key in service._cache)
    no_cache = EmotionService(fake, cache_size=0)
    no_cache.analyze("happy", "en")
    assert not no_cache._cache


def test_non_english_never_calls_english_model() -> None:
    def forbidden(_: str) -> Any:
        pytest.fail("English model called on Hindi")

    service = EmotionService(HFEmotionAnalyzer("fixture", loader=forbidden))
    assert service.analyze("exam kal hai bahut dar lag raha hai").primary == "fear"
    assert service.analyze("").primary == "neutral"


@pytest.mark.parametrize("provider", ["fake", "fallback", "hf"])
def test_config_factory(provider: str) -> None:
    settings = Settings(emotion_provider=provider, emotion_model_id="fixture")
    service = build_emotion_service(settings)
    expected = {
        "fake": FakeEmotionAnalyzer,
        "fallback": KeywordFallbackAnalyzer,
        "hf": HFEmotionAnalyzer,
    }[provider]
    assert isinstance(service.analyzer, expected)


@pytest.mark.parametrize(
    "overrides",
    [
        {"emotion_provider": "hf"},
        {"emotion_provider": "unknown"},
        {"emotion_timeout_seconds": 0},
        {"emotion_cache_size": -1},
        {"emotion_max_length": 10000},
        {"emotion_batch_size": 0},
    ],
)
def test_invalid_config(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(**overrides)
