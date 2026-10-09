"""Opt-in model check: explicitly reports unavailable dependencies/downloads."""

import importlib.util
import os

import pytest

from app.services.nlp import EmotionService, HFEmotionAnalyzer


@pytest.mark.model
def test_real_model() -> None:
    model_id = os.environ.get("MODEL_TEST_ID", "SamLowe/roberta-base-go_emotions")
    analyzer = HFEmotionAnalyzer(model_id)
    if any(importlib.util.find_spec(name) is None for name in ("transformers", "torch")):
        assert EmotionService(analyzer).analyze("I am happy", "en").primary == "joy"
        pytest.skip("Real model extras missing; HF failure-to-fallback verified")
    try:
        results = analyzer.analyze_batch(["I am happy and excited", "I feel sad and alone"])
    except RuntimeError as error:
        service = EmotionService(analyzer)
        assert service.analyze("I am happy", "en").primary == "joy"
        pytest.skip(f"Real model unavailable ({error}); fallback verified")
    assert results[0].primary == "joy"
    assert results[1].valence < 0
    assert all(0 <= result.arousal <= 1 for result in results)
