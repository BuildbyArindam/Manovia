"""Unit tests for the Hugging Face analyzer, driven by a stub pipeline.

The real pipeline is exercised separately (``test_nlp_hf_pipeline.py`` builds a
tiny model locally; the ``model``-marked test downloads the real one). Here the
point is the code *around* the model: lazy loading, thread safety, batching,
truncation, label mapping and the failure paths that decide whether the product
degrades or breaks.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.services.nlp.base import EMOTIONS, NEUTRAL
from app.services.nlp.hf import (
    LABEL_MAP,
    HFEmotionAnalyzer,
    ModelUnavailableError,
    map_labels,
    normalize_label,
)

GOEMOTIONS_LABELS = (
    "admiration",
    "amusement",
    "anger",
    "annoyance",
    "approval",
    "caring",
    "confusion",
    "curiosity",
    "desire",
    "disappointment",
    "disapproval",
    "disgust",
    "embarrassment",
    "excitement",
    "fear",
    "gratitude",
    "grief",
    "joy",
    "love",
    "nervousness",
    "optimism",
    "pride",
    "realization",
    "relief",
    "remorse",
    "sadness",
    "surprise",
    "neutral",
)

EKMAN_LABELS = ("anger", "disgust", "fear", "joy", "neutral", "sadness", "surprise")


class StubPipeline:
    """Stands in for ``transformers.pipeline`` output: a list of label rows."""

    def __init__(self, rows: Sequence[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows) if rows is not None else [{"label": "joy", "score": 0.9}]
        self.calls: list[list[str]] = []

    def __call__(self, batch: Sequence[str]) -> list[list[dict[str, Any]]]:
        self.calls.append(list(batch))
        return [list(self.rows) for _ in batch]


class ExplodingFactory:
    """A pipeline factory that fails, like a bad model id or a blocked hub."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or ImportError("no module named transformers")
        self.calls = 0

    def __call__(self, task: str, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.error


def analyzer_with(rows: Sequence[dict[str, Any]] | None = None, **kwargs: Any) -> HFEmotionAnalyzer:
    """An analyzer whose "model" is a stub: no transformers, no download."""
    stub = StubPipeline(rows)
    return HFEmotionAnalyzer(
        "fake/model", pipeline_factory=lambda *args, **factory_kwargs: stub, **kwargs
    )


def calls_of(analyzer: HFEmotionAnalyzer) -> list[list[str]]:
    """The batches the loaded pipeline actually received."""
    pipeline = analyzer._pipeline
    assert isinstance(pipeline, StubPipeline)
    return pipeline.calls


class TestLabelMap:
    def test_every_goemotions_label_is_mapped(self) -> None:
        missing = [label for label in GOEMOTIONS_LABELS if label not in LABEL_MAP]
        assert missing == []

    def test_every_ekman_label_is_mapped(self) -> None:
        missing = [label for label in EKMAN_LABELS if label not in LABEL_MAP]
        assert missing == []

    def test_every_mapping_points_at_the_internal_taxonomy(self) -> None:
        for label, (emotion, weight) in LABEL_MAP.items():
            assert emotion in EMOTIONS, label
            assert 0.0 < weight <= 1.0, label
            assert label == label.strip().casefold(), label

    def test_the_taxonomy_labels_that_models_rarely_emit_are_still_known(self) -> None:
        """loneliness and shame have no GoEmotions head; they must still be
        reachable when a checkpoint does provide them."""
        assert LABEL_MAP["loneliness"][0] == "loneliness"
        assert LABEL_MAP["shame"][0] == "shame"
        assert LABEL_MAP["anxiety"][0] == "anxiety"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("joy", "joy"),
            ("JOY", "joy"),
            ("  Joy  ", "joy"),
            ("LABEL_3", "3"),
            ("label-12", "12"),
            ("nervousness", "nervousness"),
            ("some_new_label", "some new label"),
        ],
    )
    def test_label_normalisation(self, raw: str, expected: str) -> None:
        assert normalize_label(raw) == expected


class TestMapLabels:
    def test_a_single_label_folds_into_its_emotion(self) -> None:
        weights, confidence, unknown = map_labels([{"label": "joy", "score": 0.9}])
        assert weights["joy"] == pytest.approx(0.9)
        assert confidence == pytest.approx(0.9)
        assert unknown == []

    def test_partial_weighting_applies(self) -> None:
        weights, _, _ = map_labels([{"label": "disgust", "score": 0.8}])
        assert weights["anger"] == pytest.approx(0.64)

    def test_same_family_labels_take_the_max_not_the_sum(self) -> None:
        """Summing "sadness" + "grief" would invent confidence."""
        weights, _, _ = map_labels(
            [{"label": "sadness", "score": 0.8}, {"label": "grief", "score": 0.6}]
        )
        assert weights["sadness"] == pytest.approx(0.8)

    def test_confidence_is_the_largest_raw_probability(self) -> None:
        _, confidence, _ = map_labels(
            [{"label": "joy", "score": 0.2}, {"label": "sadness", "score": 0.7}]
        )
        assert confidence == pytest.approx(0.7)

    def test_unknown_labels_are_reported_not_guessed(self) -> None:
        weights, _, unknown = map_labels([{"label": "schadenfreude", "score": 0.9}])
        assert unknown == ["schadenfreude"]
        assert max(weights.values()) == 0.0

    def test_out_of_range_and_garbage_scores_are_ignored(self) -> None:
        weights, confidence, _ = map_labels(
            [
                {"label": "joy", "score": 1.7},
                {"label": "sadness", "score": -0.4},
                {"label": "fear", "score": "not a number"},
                {"label": "anger"},
            ]
        )
        assert weights["joy"] == pytest.approx(1.0)  # clamped
        assert weights["sadness"] == pytest.approx(0.0)  # clamped
        assert weights["fear"] == 0.0
        assert weights["anger"] == 0.0
        assert confidence == pytest.approx(1.0)

    def test_empty_rows_produce_no_signal(self) -> None:
        weights, confidence, unknown = map_labels([])
        assert confidence == 0.0
        assert unknown == []
        assert max(weights.values()) == 0.0


class TestConstruction:
    def test_an_empty_model_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="model_id must not be empty"):
            HFEmotionAnalyzer("   ")

    def test_a_tiny_token_budget_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_length must be at least 8"):
            HFEmotionAnalyzer("m", max_length=4)

    def test_a_zero_batch_is_refused(self) -> None:
        with pytest.raises(ValueError, match="batch_size must be at least 1"):
            HFEmotionAnalyzer("m", batch_size=0)

    def test_nothing_is_loaded_at_construction_time(self) -> None:
        """Startup must stay fast and must not fail on a bad model id."""
        factory = ExplodingFactory()
        analyzer = HFEmotionAnalyzer("m", pipeline_factory=factory)
        assert analyzer.load_count == 0
        assert analyzer.is_loaded is False
        assert factory.calls == 0
        assert analyzer.model_id == "m"
        assert analyzer.max_length == 256
        assert analyzer.batch_size == 8


class TestLoading:
    def test_the_model_loads_once_on_first_use(self) -> None:
        analyzer = analyzer_with()
        analyzer.analyze("I am happy")
        analyzer.analyze("I am sad")
        assert analyzer.load_count == 1
        assert analyzer.is_loaded is True

    def test_concurrent_first_calls_load_exactly_once(self) -> None:
        analyzer = analyzer_with()
        barrier = threading.Barrier(8)
        results: list[Any] = []
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait()
            result = analyzer.analyze("I am happy")
            with lock:
                results.append(result)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(results) == 8
        assert analyzer.load_count == 1

    def test_a_failed_load_is_remembered_not_retried(self) -> None:
        factory = ExplodingFactory()
        analyzer = HFEmotionAnalyzer("bad/model", pipeline_factory=factory)
        with pytest.raises(ModelUnavailableError):
            analyzer.analyze("I am happy")
        with pytest.raises(ModelUnavailableError):
            analyzer.analyze("I am sad")
        assert factory.calls == 1  # not one per request
        assert analyzer.load_count == 0

    def test_a_failed_load_logs_a_warning_without_the_text(self) -> None:
        analyzer = HFEmotionAnalyzer("bad/model", pipeline_factory=ExplodingFactory())
        with capture_logs() as logs, pytest.raises(ModelUnavailableError):
            analyzer.analyze("I feel so alone lately")
        warnings = [entry for entry in logs if entry["event"] == "emotion_model_unavailable"]
        assert len(warnings) == 1
        assert warnings[0]["model_id"] == "bad/model"
        assert warnings[0]["error_type"] == "ImportError"
        assert "I feel so alone lately" not in str(logs)

    def test_try_load_reports_the_outcome(self) -> None:
        assert analyzer_with().try_load() is True
        failing = HFEmotionAnalyzer("bad/model", pipeline_factory=ExplodingFactory())
        assert failing.try_load() is False
        assert failing.is_loaded is False

    def test_close_releases_the_pipeline_so_it_can_be_reloaded(self) -> None:
        analyzer = analyzer_with()
        analyzer.analyze("I am happy")
        assert analyzer.is_loaded is True
        analyzer.close()
        assert analyzer.is_loaded is False
        analyzer.analyze("I am happy")
        assert analyzer.load_count == 2

    def test_a_successful_load_is_logged_with_its_settings(self) -> None:
        analyzer = analyzer_with(max_length=64, batch_size=4)
        with capture_logs() as logs:
            analyzer.analyze("I am happy")
        loaded = [entry for entry in logs if entry["event"] == "emotion_model_loaded"]
        assert loaded[0]["model_id"] == "fake/model"
        assert loaded[0]["max_length"] == 64
        assert loaded[0]["batch_size"] == 4


class TestInference:
    def test_labels_are_mapped_and_reported_with_provenance(self) -> None:
        analyzer = analyzer_with([{"label": "nervousness", "score": 0.83}])
        result = analyzer.analyze("exam kal hai", "hi")
        assert result.primary == "anxiety"
        assert result.analyzer == "hf"
        assert result.model == "fake/model"
        assert result.language == "hi"
        assert result.confidence == pytest.approx(0.83)

    def test_the_top_emotion_wins(self) -> None:
        analyzer = analyzer_with(
            [
                {"label": "grief", "score": 0.7},
                {"label": "joy", "score": 0.1},
                {"label": "neutral", "score": 0.05},
            ]
        )
        assert analyzer.analyze("I feel empty").primary == "sadness"

    def test_a_model_that_only_knows_neutral_reports_neutral(self) -> None:
        analyzer = analyzer_with([{"label": "neutral", "score": 0.99}])
        result = analyzer.analyze("the report is on the desk")
        assert result.primary == NEUTRAL
        # A confident neutral is still an answer.
        assert result.is_informative is True

    def test_an_unmapped_label_is_warned_about_once(self) -> None:
        analyzer = analyzer_with([{"label": "ennui", "score": 0.5}])
        with capture_logs() as logs:
            analyzer.analyze("a")
            analyzer.analyze("b")
        warnings = [entry for entry in logs if entry["event"] == "emotion_label_unmapped"]
        assert len(warnings) == 1
        assert warnings[0]["label"] == "ennui"

    def test_batches_are_chunked_at_batch_size(self) -> None:
        analyzer = analyzer_with(batch_size=2)
        results = analyzer.analyze_many(["a", "b", "c", "d", "e"])
        assert len(results) == 5
        assert [len(call) for call in calls_of(analyzer)] == [2, 2, 1]

    def test_a_single_text_is_one_batch(self) -> None:
        analyzer = analyzer_with(batch_size=8)
        analyzer.analyze_many(["only one"])
        assert calls_of(analyzer) == [["only one"]]

    def test_empty_strings_never_reach_the_model(self) -> None:
        analyzer = analyzer_with()
        results = analyzer.analyze_many(["I am happy", "", "   "])
        assert calls_of(analyzer) == [["I am happy"]]
        assert results[0].primary == "joy"
        assert results[1].primary == NEUTRAL
        assert results[2].primary == NEUTRAL
        assert results[1].is_informative is False

    def test_batch_results_keep_their_input_order(self) -> None:
        analyzer = analyzer_with()
        results = analyzer.analyze_many(["", "I am happy", ""])
        assert [result.primary for result in results] == [NEUTRAL, "joy", NEUTRAL]

    def test_long_input_is_truncated_and_flagged(self) -> None:
        analyzer = analyzer_with(max_length=16)
        result = analyzer.analyze("word " * 100)
        assert result.truncated is True

    def test_very_long_input_is_precut_before_tokenising(self) -> None:
        analyzer = analyzer_with(max_length=16)
        analyzer.analyze("x" * 10_000)
        sent = calls_of(analyzer)[0][0]
        assert len(sent) == 16 * 4  # max_length * CHARS_PER_TOKEN

    def test_short_input_is_not_flagged_as_truncated(self) -> None:
        analyzer = analyzer_with(max_length=256)
        assert analyzer.analyze("I am fine").truncated is False

    def test_an_inference_failure_becomes_model_unavailable(self) -> None:
        class Broken(StubPipeline):
            def __call__(self, batch: Sequence[str]) -> Any:
                raise RuntimeError("cuda out of memory")

        analyzer = HFEmotionAnalyzer("m", pipeline_factory=lambda *a, **k: Broken())
        with capture_logs() as logs, pytest.raises(ModelUnavailableError):
            analyzer.analyze("I am happy")
        warnings = [entry for entry in logs if entry["event"] == "emotion_model_inference_failed"]
        assert len(warnings) == 1
        assert warnings[0]["error_type"] == "RuntimeError"
        assert "I am happy" not in str(logs)

    @pytest.mark.parametrize(
        "raw",
        [
            {"label": "joy", "score": 0.9},  # top_k=1, single text
            [{"label": "joy", "score": 0.9}],  # top_k=None, single text
            [[{"label": "joy", "score": 0.9}]],  # batch of one
        ],
    )
    def test_single_label_and_batch_shapes_are_all_accepted(self, raw: Any) -> None:
        analyzer = HFEmotionAnalyzer("m", pipeline_factory=lambda *a, **k: lambda b: raw)
        result = analyzer.analyze("I am happy")
        assert result.primary == "joy"
