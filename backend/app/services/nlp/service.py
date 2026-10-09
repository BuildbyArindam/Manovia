"""Bounded inference and a thread-safe hash-only LRU. Never log exception strings."""

import hashlib
from collections import OrderedDict
from concurrent.futures import Future, TimeoutError
from threading import Lock, Thread

import structlog

from app.core.config import Settings
from app.services.nlp.base import EmotionAnalyzer, EmotionResult
from app.services.nlp.fallback import FakeEmotionAnalyzer, KeywordFallbackAnalyzer
from app.services.nlp.hf import HFEmotionAnalyzer
from app.services.nlp.language import detect_language


class EmotionService:
    def __init__(
        self,
        analyzer: EmotionAnalyzer,
        *,
        timeout: float = 1.0,
        cache_size: int = 256,
        fallback: EmotionAnalyzer | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.fallback = fallback or KeywordFallbackAnalyzer()
        self.timeout = timeout
        self.cache_size = cache_size
        self._cache: OrderedDict[str, EmotionResult] = OrderedDict()
        self._cache_lock = Lock()
        self._worker_lock = Lock()

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        key = hashlib.sha256(((lang or "auto") + "\0" + text).encode()).hexdigest()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                structlog.get_logger().debug("emotion_cache_hit", text_hash=key)
                return cached.model_copy(deep=True)
        sample = text[:10000]
        language = lang or detect_language(sample)
        if not sample.strip() or (
            language not in {"en"} and isinstance(self.analyzer, HFEmotionAnalyzer)
        ):
            result = self.fallback.analyze(sample, language)
        elif not self._worker_lock.acquire(blocking=False):
            structlog.get_logger().warning("emotion_fallback", reason="busy")
            result = self.fallback.analyze(sample, language)
        else:
            future: Future[EmotionResult] = Future()

            def run() -> None:
                try:
                    future.set_result(self.analyzer.analyze(sample, language))
                except Exception:
                    # Providers may include input text in errors; never retain/log them.
                    future.set_exception(RuntimeError("emotion_provider_failed"))
                finally:
                    self._worker_lock.release()

            # At most one live daemon per service; timeout does not kill native
            # inference. No unbounded queue, and shutdown is never held hostage.
            Thread(target=run, daemon=True, name="emotion-inference").start()
            try:
                result = future.result(timeout=self.timeout)
            except (TimeoutError, RuntimeError):
                structlog.get_logger().warning("emotion_fallback", reason="unavailable_or_timeout")
                result = self.fallback.analyze(sample, language)
        with self._cache_lock:
            self._cache[key] = result.model_copy(deep=True)
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return result


def build_emotion_service(settings: Settings) -> EmotionService:
    analyzer: EmotionAnalyzer
    if settings.emotion_provider == "fake":
        analyzer = FakeEmotionAnalyzer()
    elif settings.emotion_provider == "hf":
        analyzer = HFEmotionAnalyzer(
            settings.emotion_model_id or "",
            max_length=settings.emotion_max_length,
            batch_size=settings.emotion_batch_size,
        )
    else:
        analyzer = KeywordFallbackAnalyzer()
    return EmotionService(
        analyzer,
        timeout=settings.emotion_timeout_seconds,
        cache_size=settings.emotion_cache_size,
    )
