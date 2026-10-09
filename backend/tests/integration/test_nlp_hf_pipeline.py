"""Integration test for the real Hugging Face pipeline - with no download.

The ``model``-marked test needs the hub and is skipped by default. This one
needs neither: it builds a tiny, randomly-initialised DistilBERT with a
hand-written vocabulary in a temporary directory and points
:class:`~app.services.nlp.hf.HFEmotionAnalyzer` at it. The weights are
meaningless - the *plumbing* is the point. It proves, offline:

* the real ``transformers.pipeline`` call in ``_build_pipeline`` works, with
  ``device=-1``, ``truncation=True``, ``max_length`` and ``top_k=None``;
* multi-label output is folded into the internal taxonomy;
* batching, empty-string short-circuiting and truncation behave on a real model;
* concurrent first calls load the model exactly once.

Skipped when the ``nlp`` extra is not installed, which is the normal case in CI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services.nlp.base import EMOTIONS
from app.services.nlp.hf import HFEmotionAnalyzer

pytest.importorskip("torch", reason="the nlp extra is not installed")
transformers = pytest.importorskip("transformers", reason="the nlp extra is not installed")

LABELS = ["anger", "disgust", "fear", "joy", "neutral", "sadness", "surprise"]
VOCAB = [
    "[PAD]",
    "[UNK]",
    "[CLS]",
    "[SEP]",
    "[MASK]",
    "i",
    "am",
    "so",
    "not",
    "happy",
    "sad",
    "angry",
    "alone",
    "scared",
    "fine",
    "the",
    "job",
    "smiling",
    "exam",
    "feel",
]


@pytest.fixture(scope="module")
def tiny_model(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A real, local, 1-layer multi-label classifier. No network involved."""
    directory = tmp_path_factory.mktemp("tiny-emotion-model")
    (directory / "vocab.txt").write_text("\n".join(VOCAB), encoding="utf-8")

    tokenizer = transformers.BertTokenizerFast(vocab_file=str(directory / "vocab.txt"))
    tokenizer.save_pretrained(directory)

    config = transformers.DistilBertConfig(
        vocab_size=len(VOCAB),
        max_position_embeddings=64,
        dim=16,
        n_layers=1,
        n_heads=2,
        hidden_dim=32,
        num_labels=len(LABELS),
        id2label=dict(enumerate(LABELS)),
        label2id={label: index for index, label in enumerate(LABELS)},
        problem_type="multi_label_classification",
    )
    transformers.DistilBertForSequenceClassification(config).save_pretrained(directory)
    return str(directory)


def analyzer_for(model_dir: str, **kwargs: Any) -> HFEmotionAnalyzer:
    """A real analyzer, no stub: this exercises ``_build_pipeline`` itself."""
    return HFEmotionAnalyzer(model_dir, **kwargs)


def test_the_real_pipeline_loads_and_answers(tiny_model: str) -> None:
    analyzer = analyzer_for(tiny_model, max_length=32)
    assert analyzer.is_loaded is False  # nothing loaded yet

    result = analyzer.analyze("i am so happy about the job", "en")

    assert analyzer.is_loaded is True
    assert analyzer.load_count == 1
    assert result.primary in EMOTIONS
    assert set(result.scores) == set(EMOTIONS)
    assert sum(result.scores.values()) == pytest.approx(1.0, abs=1e-6)
    assert -1.0 <= result.valence <= 1.0
    assert 0.0 <= result.arousal <= 1.0
    assert 0.0 <= result.confidence <= 1.0
    assert result.analyzer == "hf"
    assert result.model == tiny_model


def test_multi_label_output_covers_several_emotions(tiny_model: str) -> None:
    """``top_k=None`` must return every head, not just the argmax."""
    result = analyzer_for(tiny_model, max_length=32).analyze("i feel scared and sad", "en")
    nonzero = [label for label, score in result.scores.items() if score > 0]
    assert len(nonzero) > 1, "a multi-label model should spread mass over labels"


def test_a_batch_is_analyzed_in_one_go(tiny_model: str) -> None:
    analyzer = analyzer_for(tiny_model, max_length=32, batch_size=2)
    texts = ["i am happy", "i feel alone", "the exam is tomorrow", "i am fine", ""]
    results = analyzer.analyze_many(texts, "en")
    assert len(results) == len(texts)
    assert all(result.analyzer in {"hf"} for result in results)
    # The empty string never reached the model, so exactly four were tokenised.
    assert results[4].primary == "neutral"
    assert results[4].confidence == 0.0


def test_long_input_is_truncated_without_failing(tiny_model: str) -> None:
    analyzer = analyzer_for(tiny_model, max_length=16)
    text = "i am so happy about the job and i cannot stop smiling " * 200
    assert len(text) > 10_000
    result = analyzer.analyze(text, "en")
    assert result.truncated is True
    assert result.primary in EMOTIONS


def test_input_beyond_the_token_budget_is_precut(tiny_model: str) -> None:
    """10,000 characters must not reach the tokenizer at full length."""
    analyzer = analyzer_for(tiny_model, max_length=16)
    analyzer.analyze("x" * 10_000, "en")
    assert analyzer.is_loaded is True


def test_concurrent_first_calls_load_the_model_once(tiny_model: str) -> None:
    import threading

    analyzer = analyzer_for(tiny_model, max_length=32)
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def worker() -> None:
        barrier.wait()
        try:
            analyzer.analyze("i am happy", "en")
        except BaseException as exc:  # pragma: no cover - only on a real bug
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert analyzer.load_count == 1


def test_a_bad_model_path_degrades_instead_of_raising_unexpectedly(tmp_path: Path) -> None:
    """The same code path a blocked hub takes."""
    from app.services.nlp.hf import ModelUnavailableError

    analyzer = analyzer_for(str(tmp_path / "does-not-exist"))
    with pytest.raises(ModelUnavailableError):
        analyzer.analyze("i am happy", "en")
    assert analyzer.try_load() is False
