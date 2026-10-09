"""Opt-in model check: explicitly reports unavailable dependencies/downloads."""

import os

import pytest

from app.services.nlp import EmotionService, HFEmotionAnalyzer


@pytest.mark.model
def test_real_model() -> None:
    model_id = os.environ.get("MODEL_TEST_ID", "SamLowe/roberta-base-go_emotions")
    pytest.importorskip("transformers")
    pytest.importorskip("torch")
    analyzer = HFEmotionAnalyzer(model_id)
    try:
        results = analyzer.analyze_batch(["I am happy and excited", "I feel sad and alone"])
    except RuntimeError as error:
        service = EmotionService(analyzer)
        assert service.analyze("I am happy", "en").primary == "joy"
        pytest.skip(f"Real model unavailable ({error}); fallback verified")
    assert results[0].primary == "joy"
    assert results[1].valence < 0
    assert all(0 <= result.arousal <= 1 for result in results)
