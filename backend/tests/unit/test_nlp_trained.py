"""Unit tests for the fine-tuned ("trained") emotion analyzer (Day 32).

The model itself is never loaded here: ``TrainedEmotionAnalyzer`` takes a
``loader``, and this module injects a stub model/tokenizer together with a
minimal fake ``torch``. That keeps the suite offline and torch-free while still
exercising the *real* code path — batching, the padding/truncation call, the
sigmoid plumbing, label ordering from the manifest, and every failure mode that
decides whether the product degrades or breaks.

The artefact layout is checked against the ML package's own constants, so the
two halves of the feature cannot drift apart silently.
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.core.config import Settings
from app.services.nlp import (
    ANALYZER_CHOICES,
    AnalyzerChain,
    SentimentAnalyzer,
    TrainedEmotionAnalyzer,
    build_analyzer,
)
from app.services.nlp.base import EMOTIONS, NEUTRAL
from app.services.nlp.hf import ModelUnavailableError
from app.services.nlp.keyword import KeywordFallbackAnalyzer

REPO_ROOT = Path(__file__).resolve().parents[3]
ML_MODEL_MODULE = REPO_ROOT / "ml" / "src" / "manovia_ml" / "training" / "emotion" / "model.py"

#: Rows of probabilities in taxonomy order, served in order and cycled.
JOY = (0.95, 0.01, 0.02, 0.01, 0.01, 0.0, 0.0, 0.0, 0.0)
SADNESS = (0.01, 0.90, 0.0, 0.02, 0.05, 0.02, 0.0, 0.0, 0.0)
ANGER = (0.0, 0.0, 0.70, 0.10, 0.10, 0.0, 0.05, 0.05, 0.0)
NEUTRAL_ROW = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.99)
ROWS: tuple[tuple[float, ...], ...] = (JOY, SADNESS, ANGER, NEUTRAL_ROW)


class _FakeTensor:
    """Just enough of a tensor for the batch loop: ``.to()`` / ``.cpu().tolist()``."""

    def __init__(self, data: Any) -> None:
        self._data = data

    def to(self, _device: str) -> _FakeTensor:
        return self

    def cpu(self) -> _FakeTensor:
        return self

    def tolist(self) -> Any:
        return self._data


class _FakeTorch(ModuleType):
    """The two torch calls the analyzer makes, and nothing else."""

    def __init__(self) -> None:
        super().__init__("torch")

    def no_grad(self) -> Any:
        class _Context:
            def __enter__(self) -> None:
                return None

            def __exit__(self, *_: object) -> None:
                return None

        return _Context()

    def sigmoid(self, logits: Any) -> _FakeTensor:
        # The stub model already returns probabilities, so sigmoid is identity.
        return _FakeTensor(logits)


class _StubTokenizer:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.last_kwargs: dict[str, Any] = {}

    def __call__(self, batch: Sequence[str], **kwargs: Any) -> dict[str, _FakeTensor]:
        self.calls.append(list(batch))
        self.last_kwargs = dict(kwargs)
        return {
            "input_ids": _FakeTensor([[1, 2, 3] for _ in batch]),
            "attention_mask": _FakeTensor([[1, 1, 1] for _ in batch]),
        }


class _StubModel:
    """Serves ``rows`` in order, cycling, one row per input; records batch sizes."""

    def __init__(self, rows: Sequence[Sequence[float]] = ROWS) -> None:
        self.rows = list(rows)
        self.batches: list[int] = []
        self.eval_calls = 0
        self._index = 0

    def eval(self) -> _StubModel:
        self.eval_calls += 1
        return self

    def to(self, _device: str) -> _StubModel:
        return self

    def __call__(self, **encoded: Any) -> Any:
        size = len(encoded["input_ids"]._data)
        self.batches.append(size)
        rows = [self.rows[(self._index + offset) % len(self.rows)] for offset in range(size)]
        self._index += size

        class _Output:
            logits = rows

        return _Output()


def _artifact(
    tmp_path: Path,
    *,
    labels: Sequence[str] = EMOTIONS,
    thresholds: dict[str, float] | None = None,
    run_id: str | None = "0123456789abcdef",
    manifest: dict[str, Any] | None = None,
    card: bool = True,
) -> Path:
    directory = tmp_path / "manovia-emotion-v1"
    (directory / "model").mkdir(parents=True, exist_ok=True)
    (directory / "tokenizer").mkdir(exist_ok=True)
    (directory / "model" / "config.json").write_text("{}\n", encoding="utf-8")
    (directory / "tokenizer" / "tokenizer.json").write_text("{}\n", encoding="utf-8")
    (directory / "thresholds.json").write_text(
        json.dumps({"thresholds": thresholds or {label: 0.5 for label in labels}}, indent=2) + "\n",
        encoding="utf-8",
    )
    payload: dict[str, Any] = {
        "manifest_version": 1,
        "base_model": "FacebookAI/xlm-roberta-base",
        "labels": list(labels),
        "run_id": run_id,
        "written": "2026-10-13T00:00:00Z",
        "seed": 20261013,
    }
    payload.update(manifest or {})
    (directory / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if card:
        (directory / "MODEL_CARD.md").write_text("# card\n", encoding="utf-8")
    return directory


def _analyzer(
    directory: Path,
    *,
    rows: Sequence[Sequence[float]] = ROWS,
    **kwargs: Any,
) -> TrainedEmotionAnalyzer:
    tokenizer = _StubTokenizer()
    model = _StubModel(rows)
    return TrainedEmotionAnalyzer(
        directory,
        loader=lambda _directory, _device, _max_length: (model, tokenizer),
        **kwargs,
    )


@pytest.fixture
def fake_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _FakeTorch())


class TestLayout:
    def test_the_layout_matches_what_the_ml_package_writes(self) -> None:
        """The two halves of the feature are one contract; this pins it."""

        source = ML_MODEL_MODULE.read_text(encoding="utf-8")
        for name in ("model", "tokenizer", "thresholds.json", "manifest.json", "MODEL_CARD.md"):
            assert f'"{name}"' in source, f"ml writes {name!r}, the backend does not know it"

    def test_artifact_exists_needs_both_directories(self, tmp_path: Path) -> None:
        directory = _artifact(tmp_path)
        analyzer = _analyzer(directory)
        assert analyzer.artifact_exists() is True

        (directory / "tokenizer" / "tokenizer.json").unlink()
        (directory / "tokenizer").rmdir()
        assert analyzer.artifact_exists() is False


class TestMissingArtifact:
    def test_a_missing_directory_is_a_fallback_not_an_error(self, tmp_path: Path) -> None:
        analyzer = _analyzer(tmp_path / "gone")
        with capture_logs() as logs, pytest.raises(ModelUnavailableError, match="not found"):
            analyzer.analyze("I am dreading tomorrow")
        assert analyzer.artifact_exists() is False
        assert [entry["event"] for entry in logs] == ["trained_emotion_model_missing"]
        assert logs[0]["fallback"] == "lexicon"

    def test_a_broken_load_is_remembered_and_not_retried(self, tmp_path: Path) -> None:
        directory = _artifact(tmp_path)
        calls = 0

        def loader(_directory: Path, _device: str, _max_length: int) -> Any:
            nonlocal calls
            calls += 1
            raise RuntimeError("the weights are corrupt")

        analyzer = TrainedEmotionAnalyzer(directory, loader=loader)
        with capture_logs() as logs:
            for _ in range(3):
                with pytest.raises(ModelUnavailableError, match="could not be loaded"):
                    analyzer.analyze("hello")
        assert calls == 1  # three calls, one load attempt
        assert analyzer.load_count == 0
        assert [entry["event"] for entry in logs] == ["trained_emotion_model_unavailable"]

    def test_a_directory_that_disappears_after_a_load_keeps_answering(
        self, tmp_path: Path, fake_torch: None
    ) -> None:
        """Deleting the weights is supported; deleting them after a load changes nothing."""

        directory = _artifact(tmp_path)
        analyzer = _analyzer(directory, rows=(JOY, NEUTRAL_ROW, NEUTRAL_ROW))
        assert analyzer.analyze("I am so happy today").primary == "joy"
        for path in sorted(directory.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        directory.rmdir()
        assert analyzer.analyze("still here") == analyzer.analyze("and still here")
        assert analyzer.load_count == 1

    def test_the_chain_answers_from_the_lexicon_when_the_model_is_gone(
        self, tmp_path: Path
    ) -> None:
        settings = Settings(
            _env_file=None,
            emotion_analyzer="trained",
            emotion_trained_dir=str(tmp_path / "missing-artifact"),
        )
        with capture_logs() as logs:
            chain = build_analyzer(settings)
            assert isinstance(chain, AnalyzerChain)
            assert isinstance(chain.primary, TrainedEmotionAnalyzer)
            result = chain.analyze("I am so tired of everything today", lang="en")
        assert result.analyzer in {"keyword", "sentiment"}
        assert result.primary in EMOTIONS
        events = [entry["event"] for entry in logs]
        assert "trained_emotion_artifact_missing" in events  # told at build time too
        assert "trained_emotion_model_missing" in events


class TestInference:
    def test_the_result_schema_is_the_shared_one(self, tmp_path: Path, fake_torch: None) -> None:
        analyzer = _analyzer(_artifact(tmp_path))
        result = analyzer.analyze("I am so happy today", lang="en")
        assert result.analyzer == "trained"
        assert result.model == "0123456789abcdef"  # the MLflow run id from the manifest
        assert result.language == "en"
        assert set(result.scores) == set(EMOTIONS)
        assert sum(result.scores.values()) == pytest.approx(1.0)
        assert result.primary == max(result.scores, key=result.scores.__getitem__)
        assert result.primary == "joy"
        assert result.confidence == pytest.approx(0.95)
        assert result.truncated is False

    def test_probabilities_are_mapped_through_the_manifests_label_order(
        self, tmp_path: Path, fake_torch: None
    ) -> None:
        # The run's manifest declares the order; a shuffled head must still land
        # on the right label names.
        shuffled = list(reversed(EMOTIONS))
        analyzer = _analyzer(_artifact(tmp_path, labels=shuffled))
        result = analyzer.analyze("whatever")
        assert result.scores[shuffled[0]] == pytest.approx(0.95)

    def test_batching_keeps_order_and_skips_blank_input(
        self, tmp_path: Path, fake_torch: None
    ) -> None:
        directory = _artifact(tmp_path)
        tokenizer = _StubTokenizer()
        model = _StubModel(ROWS)
        analyzer = TrainedEmotionAnalyzer(
            directory,
            batch_size=2,
            loader=lambda _d, _dev, _m: (model, tokenizer),
        )
        results = analyzer.analyze_many(["a", "", "b", "c", "d"])
        assert [result.primary for result in results] == [
            "joy",
            NEUTRAL,
            "sadness",
            "anger",
            "neutral",
        ]
        assert model.batches == [2, 2]  # four non-blank texts, two per forward pass
        assert tokenizer.last_kwargs["truncation"] is True
        assert tokenizer.last_kwargs["padding"] is True
        assert tokenizer.last_kwargs["max_length"] == analyzer.max_length

    def test_long_input_is_truncated_not_rejected(self, tmp_path: Path, fake_torch: None) -> None:
        analyzer = _analyzer(_artifact(tmp_path), max_length=8)
        result = analyzer.analyze("word " * 100)
        assert result.truncated is True

    def test_empty_input_is_neutral_without_touching_the_model(
        self, tmp_path: Path, fake_torch: None
    ) -> None:
        analyzer = _analyzer(_artifact(tmp_path))
        result = analyzer.analyze("   ")
        assert result.primary == NEUTRAL
        assert analyzer.load_count == 0  # nothing to infer, so nothing to load

    def test_thresholds_live_in_asserted_labels_not_in_the_distribution(
        self, tmp_path: Path, fake_torch: None
    ) -> None:
        thresholds = {label: 0.5 for label in EMOTIONS}
        thresholds["anger"] = 0.9
        thresholds["joy"] = 0.1
        analyzer = _analyzer(_artifact(tmp_path, thresholds=thresholds), rows=(ANGER, JOY))
        assert analyzer.top_label("annoyed") == "anger"  # argmax, by the result contract
        assert analyzer.asserted_labels("relieved") == frozenset({"joy"})
        assert analyzer.thresholds["anger"] == 0.9
        assert analyzer.thresholds["joy"] == 0.1

    def test_a_missing_thresholds_file_falls_back_to_the_default(
        self, tmp_path: Path, fake_torch: None
    ) -> None:
        directory = _artifact(tmp_path)
        (directory / "thresholds.json").unlink()
        analyzer = _analyzer(directory)
        assert analyzer.thresholds == {}
        assert analyzer.asserted_labels("happy") == frozenset({"joy"})  # 0.5 default

    def test_describe_reports_provenance_and_no_text(
        self, tmp_path: Path, fake_torch: None
    ) -> None:
        analyzer = _analyzer(_artifact(tmp_path))
        analyzer.analyze("something private")
        described = analyzer.describe()
        assert described["base_model"] == "FacebookAI/xlm-roberta-base"
        assert described["run_id"] == "0123456789abcdef"
        assert described["load_count"] == 1
        assert "private" not in json.dumps(described)


class TestLoading:
    def test_concurrent_first_use_loads_once(self, tmp_path: Path, fake_torch: None) -> None:
        directory = _artifact(tmp_path)
        model = _StubModel([JOY])
        loads = 0

        def loader(_directory: Path, _device: str, _max_length: int) -> Any:
            nonlocal loads
            loads += 1
            return model, _StubTokenizer()

        analyzer = TrainedEmotionAnalyzer(directory, loader=loader)
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            analyzer.analyze("hello")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert loads == 1
        assert analyzer.load_count == 1
        assert analyzer.is_loaded is True

    def test_a_torch_that_is_not_installed_is_a_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The offline suite and a slim deployment share this path."""

        monkeypatch.setitem(sys.modules, "torch", None)  # `import torch` now raises
        analyzer = TrainedEmotionAnalyzer(_artifact(tmp_path))
        with (
            capture_logs() as logs,
            pytest.raises(ModelUnavailableError, match="could not be loaded"),
        ):
            analyzer.analyze("hello")
        # ``sys.modules["torch"] = None`` raises ModuleNotFoundError, an ImportError.
        assert logs[0]["error_type"] in {"ImportError", "ModuleNotFoundError"}
        assert logs[0]["fallback"] == "lexicon"

    def test_a_broken_manifest_does_not_stop_the_model(
        self, tmp_path: Path, fake_torch: None
    ) -> None:
        directory = _artifact(tmp_path)
        (directory / "manifest.json").write_text("{not json", encoding="utf-8")
        analyzer = _analyzer(directory)
        result = analyzer.analyze("hello")
        assert result.primary in EMOTIONS
        assert analyzer.model_id == directory.name  # falls back to the directory name

    def test_an_absurd_token_budget_is_refused_at_construction(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="max_length"):
            TrainedEmotionAnalyzer(_artifact(tmp_path), max_length=2)
        with pytest.raises(ValueError, match="batch_size"):
            TrainedEmotionAnalyzer(_artifact(tmp_path), batch_size=0)


class TestSettings:
    def test_the_dir_is_required_for_the_trained_choice(self) -> None:
        with pytest.raises(ValidationError, match="EMOTION_TRAINED_DIR"):
            Settings(_env_file=None, emotion_analyzer="trained")

    def test_a_blank_dir_is_not_a_dir(self) -> None:
        with pytest.raises(ValidationError, match="EMOTION_TRAINED_DIR"):
            Settings(_env_file=None, emotion_analyzer="trained", emotion_trained_dir="   ")

    def test_relative_paths_resolve_against_the_repository_root(self) -> None:
        settings = Settings(
            _env_file=None,
            emotion_analyzer="trained",
            emotion_trained_dir="ml/artifacts/emotion/manovia-emotion-v1",
        )
        expected = REPO_ROOT / "ml/artifacts/emotion/manovia-emotion-v1"
        assert settings.emotion_trained_path == expected

    def test_absolute_paths_are_left_alone(self, tmp_path: Path) -> None:
        settings = Settings(
            _env_file=None,
            emotion_analyzer="trained",
            emotion_trained_dir=str(tmp_path),
        )
        assert settings.emotion_trained_path == tmp_path

    def test_the_lexicon_choices_still_do_not_need_a_dir(self) -> None:
        assert Settings(_env_file=None, emotion_analyzer="keyword").emotion_trained_dir == ""

    def test_the_choice_set_is_documented(self) -> None:
        assert ANALYZER_CHOICES == (
            "auto",
            "hf",
            "baseline",
            "trained",
            "keyword",
            "sentiment",
            "fake",
        )

    @pytest.mark.parametrize("choice", ["baseline", "trained"])
    def test_the_new_choices_reach_the_factory(self, choice: str, tmp_path: Path) -> None:
        chain = build_analyzer(
            Settings(
                _env_file=None,
                emotion_analyzer=choice,
                emotion_trained_dir=str(tmp_path),
            )
        )
        assert isinstance(chain, AnalyzerChain)
        assert [candidate.name for candidate in chain.candidates[1:]] == ["keyword", "sentiment"]

    def test_a_baseline_choice_needs_a_model_id(self) -> None:
        with pytest.raises(ValidationError, match="EMOTION_MODEL_ID"):
            Settings(_env_file=None, emotion_analyzer="baseline", emotion_model_id=" ")

    def test_the_sentiment_choice_is_still_lexicon_only(self) -> None:
        chain = build_analyzer(Settings(_env_file=None, emotion_analyzer="sentiment"))
        assert isinstance(chain, AnalyzerChain)
        assert isinstance(chain.primary, SentimentAnalyzer)


def test_the_trained_analyzer_matches_the_keyword_analyzer_schema(
    tmp_path: Path, fake_torch: None
) -> None:
    """Same interface, same schema: the swap is a settings change, not a code change."""

    keyword = KeywordFallbackAnalyzer()
    trained = _analyzer(_artifact(tmp_path))
    for analyzer in (keyword, trained):
        result = analyzer.analyze("I am so tired of everything today", lang="en")
        assert set(result.scores) == set(EMOTIONS)
        assert sum(result.scores.values()) == pytest.approx(1.0)
        assert result.primary == max(result.scores, key=result.scores.__getitem__)
        # ``confidence`` is the analyzer's own raw certainty and is deliberately
        # not the normalised maximum (the keyword chain reports its match
        # strength, the trained model its raw probability) — both are in range.
        assert 0.0 <= result.confidence <= 1.0
