"""Unit tests for the analysis cache: LRU behaviour and, above all, no text."""

from __future__ import annotations

import hashlib
import threading

import pytest

from app.services.nlp.base import neutral_result
from app.services.nlp.cache import AnalysisCache, CacheStats, text_fingerprint


def _result(analyzer: str = "test") -> object:
    return neutral_result(analyzer=analyzer)


class TestKeys:
    def test_the_key_is_a_digest_and_never_the_text(self) -> None:
        cache = AnalysisCache(8)
        text = "I feel so alone lately"
        key = cache.key_for(text, "en")
        assert text not in key
        assert key == hashlib.sha256(f"|en|{text}".encode()).hexdigest()
        assert len(key) == 64

    def test_whitespace_is_collapsed_but_case_is_not(self) -> None:
        cache = AnalysisCache(8)
        assert cache.key_for("I  am\nfine", "en") == cache.key_for("I am fine", "en")
        # Case changes meaning (shouting is a signal), so it must not collide.
        assert cache.key_for("I am FINE", "en") != cache.key_for("i am fine", "en")

    def test_language_and_variant_are_part_of_the_key(self) -> None:
        cache = AnalysisCache(8)
        text = "I am fine"
        assert cache.key_for(text, "en") != cache.key_for(text, "hi")
        assert cache.key_for(text, "en") != cache.key_for(text, None)
        assert cache.key_for(text, "en", variant="hf:m1") != cache.key_for(
            text, "en", variant="hf:m2"
        )

    def test_a_model_swap_changes_every_key(self) -> None:
        """The cache must not serve the previous model's answers."""
        cache = AnalysisCache(8)
        cache.set(cache.key_for("hi", "en", variant="hf:old"), _result())  # type: ignore[arg-type]
        assert cache.get(cache.key_for("hi", "en", variant="hf:new")) is None

    def test_the_fingerprint_is_short_and_irreversible(self) -> None:
        text = "exam kal hai, bahut dar lag raha hai"
        fingerprint = text_fingerprint(text)
        assert len(fingerprint) == 16
        assert text not in fingerprint
        assert fingerprint == text_fingerprint(text)
        assert fingerprint != text_fingerprint(text + ".")
        assert len(text_fingerprint(text, length=32)) == 32


class TestLRU:
    def test_a_stored_result_comes_back(self) -> None:
        cache = AnalysisCache(2)
        stored = _result()
        cache.set("k", stored)  # type: ignore[arg-type]
        assert cache.get("k") is stored
        assert len(cache) == 1
        assert "k" in cache

    def test_a_miss_is_reported_as_none(self) -> None:
        cache = AnalysisCache(2)
        assert cache.get("absent") is None
        assert cache.stats().misses == 1

    def test_the_least_recently_used_entry_is_evicted(self) -> None:
        cache = AnalysisCache(2)
        cache.set("a", _result())  # type: ignore[arg-type]
        cache.set("b", _result())  # type: ignore[arg-type]
        assert cache.get("a") is not None  # "a" is now most recent
        cache.set("c", _result())  # type: ignore[arg-type]
        assert "b" not in cache
        assert "a" in cache and "c" in cache
        assert cache.stats().evictions == 1
        assert len(cache) == 2

    def test_size_zero_disables_caching_entirely(self) -> None:
        cache = AnalysisCache(0)
        cache.set("a", _result())  # type: ignore[arg-type]
        assert cache.get("a") is None
        assert len(cache) == 0
        assert cache.enabled is False
        assert cache.key_for("anything", "en")  # keys still compute

    def test_a_negative_size_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="maxsize must be 0 or greater"):
            AnalysisCache(-1)

    def test_clear_drops_entries_but_keeps_counters(self) -> None:
        cache = AnalysisCache(4)
        cache.set("a", _result())  # type: ignore[arg-type]
        cache.get("a")
        cache.clear()
        assert len(cache) == 0
        assert cache.stats().hits == 1

    def test_stats_report_counters_only(self) -> None:
        cache = AnalysisCache(4)
        text = "I got the job and I can't stop smiling"
        cache.set(cache.key_for(text, "en"), _result())  # type: ignore[arg-type]
        cache.get(cache.key_for(text, "en"))
        cache.get(cache.key_for("never stored", "en"))
        stats = cache.stats()
        assert isinstance(stats, CacheStats)
        assert (stats.hits, stats.misses, stats.size, stats.maxsize) == (1, 1, 1, 4)
        assert stats.hit_rate == 0.5
        described = cache.describe()
        assert described["hit_rate"] == 0.5
        # The one thing this must never do: leak the text or the key.
        assert text not in str(described)
        assert cache.key_for(text, "en") not in str(described)

    def test_hit_rate_is_zero_before_any_lookup(self) -> None:
        assert AnalysisCache(2).stats().hit_rate == 0.0


class TestConcurrency:
    def test_parallel_writers_do_not_lose_the_invariant(self) -> None:
        """Many threads, one small cache: size must never exceed maxsize."""
        cache = AnalysisCache(16)
        errors: list[BaseException] = []

        def worker(start: int) -> None:
            try:
                for index in range(start, start + 200):
                    key = cache.key_for(f"message number {index}", "en")
                    cache.set(key, neutral_result(analyzer="test"))
                    cache.get(key)
            except BaseException as exc:  # pragma: no cover - only on a real bug
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(offset,)) for offset in range(0, 1600, 200)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert len(cache) <= 16
        stats = cache.stats()
        assert stats.evictions > 0
        assert stats.hits + stats.misses == 1600
