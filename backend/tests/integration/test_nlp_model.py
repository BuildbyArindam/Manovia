"""The one test that needs the real model: ``pytest -m model``.

Excluded from the default run (``addopts = -ra -m 'not model'`` in
``pyproject.toml``) because it downloads from the Hugging Face hub. Run it with
the ``nlp`` extra installed and network access:

    pip install -e ".[dev,nlp]"
    pytest -m model -q

It asserts only things that must hold for *any* working checkpoint: the model
loads, the answer is a valid result over the internal taxonomy, and the label
map covers the checkpoint's label set. It deliberately does **not** assert which
emotion a sentence gets - that is what an eval set is for (see ``evals/``), and
a hard-coded expectation here would only encode today's model.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.nlp.base import EMOTIONS
from app.services.nlp.hf import LABEL_MAP, HFEmotionAnalyzer, normalize_label

pytestmark = pytest.mark.model

torch = pytest.importorskip("torch", reason="the nlp extra is not installed")
pytest.importorskip("transformers", reason="the nlp extra is not installed")


@pytest.fixture(scope="module")
def analyzer() -> HFEmotionAnalyzer:
    settings = Settings(_env_file=None)
    return HFEmotionAnalyzer(
        settings.emotion_model_id,
        max_length=settings.emotion_max_length,
        batch_size=settings.emotion_batch_size,
    )


def test_the_configured_model_loads(analyzer: HFEmotionAnalyzer) -> None:
    assert analyzer.try_load() is True, (
        f"EMOTION_MODEL_ID={analyzer.model_id!r} could not be loaded. "
        "Check network access and the model id; the offline suite covers the "
        "fallback path, so this failure is about the model, not the code."
    )
    assert analyzer.is_loaded is True
    assert analyzer.load_count == 1


def test_the_answer_is_a_valid_result(analyzer: HFEmotionAnalyzer) -> None:
    result = analyzer.analyze("I got the job and I can't stop smiling", "en")
    assert result.primary in EMOTIONS
    assert set(result.scores) == set(EMOTIONS)
    assert sum(result.scores.values()) == pytest.approx(1.0, abs=1e-6)
    assert -1.0 <= result.valence <= 1.0
    assert 0.0 <= result.arousal <= 1.0
    assert result.analyzer == "hf"
    assert result.model == analyzer.model_id


def test_the_checkpoints_labels_are_all_mapped(analyzer: HFEmotionAnalyzer) -> None:
    """An unmapped head is silently dropped, so this must be checked on the
    real checkpoint rather than assumed from the model card."""
    config = analyzer._pipeline.model.config  # type: ignore[union-attr]
    labels = [str(label) for label in config.id2label.values()]
    unmapped = [label for label in labels if normalize_label(label) not in LABEL_MAP]
    assert unmapped == [], f"unmapped labels in {analyzer.model_id}: {unmapped}"


def test_a_batch_matches_single_calls(analyzer: HFEmotionAnalyzer) -> None:
    texts = ["I feel so alone lately", "exam kal hai, bahut dar lag raha hai"]
    batched = analyzer.analyze_many(texts, "en")
    singles = [analyzer.analyze(text, "en") for text in texts]
    assert [result.primary for result in batched] == [result.primary for result in singles]


def test_long_input_does_not_fail(analyzer: HFEmotionAnalyzer) -> None:
    result = analyzer.analyze("I feel anxious. " * 1200, "en")
    assert result.truncated is True
    assert result.primary in EMOTIONS
