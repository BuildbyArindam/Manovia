"""An in-memory LRU cache for analysis results, keyed by a text **hash**.

AGENTS.md rule 5 is the whole design constraint here: raw message text must
never reach the logs, and a cache is a place text likes to hide - in a key, in
a ``repr()``, in a debug line. So:

* the key is ``sha256(variant | lang | whitespace-collapsed text)``, and the
  plaintext is not stored anywhere in this object;
* :meth:`AnalysisCache.describe` (the only thing logs ever see) reports sizes
  and hit rates, never keys or text;
* :meth:`AnalysisCache.stats` exposes counters so an operator can tell whether
  the cache is doing anything without being able to read what went through it.

Case is preserved in the hash on purpose: "I am FINE" and "i am fine" score
differently (shouting is an emphasis signal), so they must not collide.

This is per-process memory. Behind several workers each has its own cache,
which is correct-but-wasteful; a shared store would have to be keyed the same
way and is in the parking lot.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from dataclasses import dataclass

from app.services.nlp.base import EmotionResult

#: Hex characters kept from the digest for a log-safe fingerprint. 16 chars is
#: 64 bits: enough that two different messages do not collide in a log window.
FINGERPRINT_HEX_LENGTH: int = 16


def _normalize(text: str) -> str:
    """Collapse whitespace runs only - case and punctuation stay significant."""
    return " ".join(text.split())


def text_fingerprint(text: str, length: int = FINGERPRINT_HEX_LENGTH) -> str:
    """A short hex fingerprint of ``text``, safe to log.

    Not reversible in practice at this length and never used as a key on its
    own; it exists so an operator can correlate two log lines about the same
    message without the message being in either.
    """
    return hashlib.sha256(text.encode()).hexdigest()[:length]


@dataclass(frozen=True, slots=True)
class CacheStats:
    """Counters only. No keys, no text."""

    maxsize: int
    size: int
    hits: int
    misses: int
    evictions: int

    @property
    def hit_rate(self) -> float:
        lookups = self.hits + self.misses
        return round(self.hits / lookups, 4) if lookups else 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "maxsize": self.maxsize,
            "size": self.size,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "hit_rate": self.hit_rate,
        }


class AnalysisCache:
    """Thread-safe LRU cache from a hashed key to an :class:`EmotionResult`."""

    def __init__(self, maxsize: int = 512) -> None:
        if maxsize < 0:
            raise ValueError("maxsize must be 0 or greater (0 disables caching)")
        self._maxsize = maxsize
        self._entries: OrderedDict[str, EmotionResult] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def enabled(self) -> bool:
        return self._maxsize > 0

    def key_for(self, text: str, lang: str | None = None, *, variant: str = "") -> str:
        """The cache key for one input: a digest, never the text itself."""
        material = f"{variant}|{lang or ''}|{_normalize(text)}".encode()
        return hashlib.sha256(material).hexdigest()

    def get(self, key: str) -> EmotionResult | None:
        """Return the cached result, marking it most-recently-used."""
        if not self._maxsize:
            return None
        with self._lock:
            result = self._entries.get(key)
            if result is None:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return result

    def set(self, key: str, result: EmotionResult) -> None:
        """Store a result, evicting the least-recently-used entry if full."""
        if not self._maxsize:
            return
        with self._lock:
            self._entries[key] = result
            self._entries.move_to_end(key)
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)
                self._evictions += 1

    def clear(self) -> None:
        """Drop every entry (counters are kept)."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._entries

    def stats(self) -> CacheStats:
        """Counters, for logs and the dev endpoint. Never keys or text."""
        with self._lock:
            return CacheStats(
                maxsize=self._maxsize,
                size=len(self._entries),
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
            )

    def describe(self) -> dict[str, float | int]:
        """Dict form of :meth:`stats` - the only loggable view of this cache."""
        return self.stats().as_dict()
