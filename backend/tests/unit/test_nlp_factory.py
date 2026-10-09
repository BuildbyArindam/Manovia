"""Unit tests for the analyzer factory: what a deployment actually gets."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.nlp import (
    ANALYZER_CHOICES,
    FALLBACK_ORDER,
    AnalysisCache,
    AnalyzerChain,
    FakeEmotionAnalyzer,
    HFEmotionAnalyzer,
    KeywordFallbackAnalyzer,
    SentimentAnalyzer,
    build_analyzer,
)


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class TestBuildAnalyzer:
    def test_the_choices_constant_matches_the_documented_set(self) -> None:
        assert ANALYZER_CHOICES == ("auto", "hf", "keyword", "sentiment", "fake")
        assert FALLBACK_ORDER == ("keyword", "sentiment")

    @pytest.mark.parametrize("choice", ["auto", "hf"])
    def test_a_model_choice_puts_the_model_first(self, choice: str) -> None:
        chain = build_analyzer(settings(emotion_analyzer=choice))
        assert isinstance(chain, AnalyzerChain)
        assert isinstance(chain.primary, HFEmotionAnalyzer)
        assert [candidate.name for candidate in chain.candidates[1:]] == ["keyword", "sentiment"]

    def test_the_model_settings_reach_the_analyzer(self) -> None:
        chain = build_analyzer(
            settings(
                emotion_model_id="org/some-emotion-model",
                emotion_max_length=128,
                emotion_batch_size=4,
            )
        )
        assert isinstance(chain, AnalyzerChain)
        model = chain.primary
        assert isinstance(model, HFEmotionAnalyzer)
        assert model.model_id == "org/some-emotion-model"
        assert model.max_length == 128
        assert model.batch_size == 4

    def test_nothing_is_loaded_while_building(self) -> None:
        """A bad model id must not stop the app from starting."""
        chain = build_analyzer(settings(emotion_model_id="does/not-exist"))
        assert isinstance(chain, AnalyzerChain)
        assert isinstance(chain.primary, HFEmotionAnalyzer)
        assert chain.primary.is_loaded is False

    def test_the_keyword_choice_keeps_sentiment_behind_it(self) -> None:
        chain = build_analyzer(settings(emotion_analyzer="keyword"))
        assert isinstance(chain, AnalyzerChain)
        assert isinstance(chain.primary, KeywordFallbackAnalyzer)
        assert [candidate.name for candidate in chain.candidates[1:]] == ["sentiment"]

    def test_the_sentiment_choice_keeps_keyword_behind_it(self) -> None:
        chain = build_analyzer(settings(emotion_analyzer="sentiment"))
        assert isinstance(chain, AnalyzerChain)
        assert isinstance(chain.primary, SentimentAnalyzer)
        assert [candidate.name for candidate in chain.candidates[1:]] == ["keyword"]

    def test_the_fake_choice_is_bare_so_tests_are_not_second_guessed(self) -> None:
        analyzer = build_analyzer(settings(emotion_analyzer="fake"))
        assert isinstance(analyzer, FakeEmotionAnalyzer)
        assert not isinstance(analyzer, AnalyzerChain)

    def test_the_cache_size_comes_from_settings(self) -> None:
        chain = build_analyzer(settings(emotion_cache_size=7))
        assert isinstance(chain, AnalyzerChain)
        assert chain.cache is not None
        assert chain.cache.maxsize == 7

    def test_caching_can_be_switched_off(self) -> None:
        chain = build_analyzer(settings(emotion_analyzer="keyword", emotion_cache_size=0))
        assert isinstance(chain, AnalyzerChain)
        assert chain.cache is not None
        assert chain.cache.enabled is False

    def test_a_cache_can_be_injected_and_shared(self) -> None:
        shared = AnalysisCache(3)
        chain = build_analyzer(settings(emotion_analyzer="keyword"), cache=shared)
        assert isinstance(chain, AnalyzerChain)
        assert chain.cache is shared

    def test_an_unknown_choice_is_refused(self) -> None:
        """Defence in depth: Settings validates first, but the factory must too.

        ``model_copy`` skips validation, which is how a bad value could reach
        here at all.
        """
        smuggled = settings().model_copy(update={"emotion_analyzer": "telepathy"})
        with pytest.raises(ValueError, match="EMOTION_ANALYZER"):
            build_analyzer(smuggled)

    def test_the_choice_is_case_insensitive_and_trimmed(self) -> None:
        analyzer = build_analyzer(settings(emotion_analyzer="  KEYWORD  "))
        assert isinstance(analyzer, AnalyzerChain)
        assert isinstance(analyzer.primary, KeywordFallbackAnalyzer)


class TestEndToEndFallback:
    def test_a_broken_model_degrades_to_the_lexicon_not_to_an_error(self) -> None:
        """The Day 6 acceptance path, with no network involved.

        ``EMOTION_ANALYZER=auto`` with an unloadable model must still answer.
        """
        chain = build_analyzer(settings(emotion_model_id="does/not-exist"))
        assert isinstance(chain, AnalyzerChain)
        # Force the model to fail the way a blocked hub does.
        model = chain.primary
        assert isinstance(model, HFEmotionAnalyzer)
        model._pipeline = None
        original_build = model._build_pipeline

        def explode() -> object:
            raise ImportError("no module named transformers")

        model._build_pipeline = explode  # type: ignore[method-assign]
        try:
            result = chain.analyze("I feel so alone lately")
        finally:
            model._build_pipeline = original_build  # type: ignore[method-assign]

        assert result.analyzer == "keyword"
        assert result.primary == "loneliness"
        assert result.valence < 0
