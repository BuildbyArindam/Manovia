"""Manovia's NLP service: emotion and sentiment, behind one interface.

Public surface (everything else in this package is an implementation detail):

* :class:`EmotionAnalyzer` / :class:`EmotionResult` - the contract.
* :func:`build_analyzer` - construct the analyzer an app should use, from
  :class:`~app.core.config.Settings`. This is the only entry point the API
  layer should call.
* :func:`detect_language` - ``en`` / ``hi`` / ``bn`` / ``other`` + Hinglish.
* The concrete analyzers, for tests and for explicit configuration.

The chain :func:`build_analyzer` returns never raises and never blocks on a
missing model: if the Hugging Face model cannot load, a warning is logged once
and the lexicon analyzers answer instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from app.services.nlp.base import (
    EMOTION_DIMENSIONS,
    EMOTIONS,
    NEUTRAL,
    EmotionAnalyzer,
    EmotionResult,
    build_result,
    neutral_result,
    normalize_scores,
)
from app.services.nlp.cache import AnalysisCache, CacheStats, text_fingerprint
from app.services.nlp.chain import AnalyzerChain, ModelHealth
from app.services.nlp.fake import FakeEmotionAnalyzer
from app.services.nlp.hf import HFEmotionAnalyzer, ModelUnavailableError
from app.services.nlp.keyword import KeywordFallbackAnalyzer
from app.services.nlp.language import (
    HINGLISH_THRESHOLD,
    SUPPORTED_LANGUAGES,
    LanguageInfo,
    detect_language,
    hinglish_score,
)
from app.services.nlp.sentiment import SentimentAnalyzer, polarity
from app.services.nlp.trained import TrainedEmotionAnalyzer

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from app.core.config import Settings

#: Values accepted by ``EMOTION_ANALYZER``.
#:
#: ``auto``/``hf``/``baseline`` all lead with the off-the-shelf checkpoint in
#: ``EMOTION_MODEL_ID``; ``baseline`` is the name a report uses for it when
#: comparing against a fine-tuned model. ``trained`` leads with the packaged
#: Day-32 model in ``EMOTION_TRAINED_DIR`` (ADR 0032).
ANALYZER_CHOICES: tuple[str, ...] = (
    "auto",
    "hf",
    "baseline",
    "trained",
    "keyword",
    "sentiment",
    "fake",
)

#: Deterministic fallbacks, in the order they are tried.
#:
#: Keyword first because it can name nine emotions; sentiment second because it
#: can only band polarity but still recognises words the emotion lexicon misses.
FALLBACK_ORDER: tuple[str, ...] = ("keyword", "sentiment")


def _lexicon_analyzer(choice: str) -> EmotionAnalyzer:
    if choice == "keyword":
        return KeywordFallbackAnalyzer()
    return SentimentAnalyzer()


def build_analyzer(settings: Settings, *, cache: AnalysisCache | None = None) -> EmotionAnalyzer:
    """Build the analyzer this deployment should use.

    ``EMOTION_ANALYZER`` selects the *primary*; the lexicon fallbacks are always
    attached (except for ``fake``, which tests use precisely because it must not
    be second-guessed). Nothing here loads a model: the HF analyzer loads on
    first use, so application startup stays fast and cannot fail on a bad
    model id.
    """
    choice = settings.emotion_analyzer.strip().casefold()
    if choice not in ANALYZER_CHOICES:
        raise ValueError(f"EMOTION_ANALYZER must be one of {ANALYZER_CHOICES}, got {choice!r}")

    if choice == "fake":
        return FakeEmotionAnalyzer()

    primary: EmotionAnalyzer
    if choice in {"auto", "hf", "baseline"}:
        primary = HFEmotionAnalyzer(
            settings.emotion_model_id,
            max_length=settings.emotion_max_length,
            batch_size=settings.emotion_batch_size,
            device=settings.emotion_device,
        )
    elif choice == "trained":
        primary = TrainedEmotionAnalyzer(
            settings.emotion_trained_path,
            max_length=settings.emotion_trained_max_length,
            batch_size=settings.emotion_batch_size,
            device=settings.emotion_device,
        )
        if not primary.artifact_exists():
            # Not a startup failure: the chain falls back on first use. Saying it
            # at build time as well means an operator sees the reason in the boot
            # logs rather than in a request log line an hour later.
            structlog.get_logger().warning(
                "trained_emotion_artifact_missing",
                directory=str(primary.directory),
                fallback="lexicon",
            )
    else:
        primary = _lexicon_analyzer(choice)

    fallbacks = [_lexicon_analyzer(name) for name in FALLBACK_ORDER if name != primary.name]
    health = ModelHealth(
        failure_threshold=settings.emotion_failure_threshold,
        cooldown_seconds=settings.emotion_cooldown_seconds,
        slow_ms=settings.emotion_slow_ms,
    )
    analyzer = AnalyzerChain(
        primary,
        fallbacks,
        cache=cache if cache is not None else AnalysisCache(settings.emotion_cache_size),
        health=health,
    )
    structlog.get_logger().info(
        "emotion_analyzer_configured",
        primary=primary.name,
        model_id=primary.model_id,
        fallbacks=[candidate.name for candidate in fallbacks],
        cache_size=settings.emotion_cache_size,
    )
    return analyzer


__all__ = [
    "ANALYZER_CHOICES",
    "EMOTIONS",
    "EMOTION_DIMENSIONS",
    "FALLBACK_ORDER",
    "HINGLISH_THRESHOLD",
    "NEUTRAL",
    "SUPPORTED_LANGUAGES",
    "AnalysisCache",
    "AnalyzerChain",
    "CacheStats",
    "EmotionAnalyzer",
    "EmotionResult",
    "FakeEmotionAnalyzer",
    "HFEmotionAnalyzer",
    "KeywordFallbackAnalyzer",
    "LanguageInfo",
    "ModelHealth",
    "ModelUnavailableError",
    "SentimentAnalyzer",
    "TrainedEmotionAnalyzer",
    "build_analyzer",
    "build_result",
    "detect_language",
    "hinglish_score",
    "neutral_result",
    "normalize_scores",
    "polarity",
    "text_fingerprint",
]
