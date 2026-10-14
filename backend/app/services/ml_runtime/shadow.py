"""Shadow mode: the candidate scores live traffic and changes nothing.

The candidate runs alongside production on the same message, in the
shadow runner's own executor. Its prediction is compared with the
production prediction and the comparison is recorded as a *category* —
never the text, never either model's raw output beyond the two level
names the audit trail already carries (levels are metadata; the safety
package has carried them since Day 8).

Closed category vocabulary:

* ``agree`` — same argmax level;
* ``candidate_higher`` / ``candidate_lower`` — adjacent-or-further apart,
  candidate more/less severe (a lower candidate is the dangerous
  direction for a safety model and is counted separately on purpose);
* ``candidate_crisis_only`` — candidate says HIGH/IMMINENT, production
  does not;
* ``production_crisis_only`` — production says HIGH/IMMINENT, candidate
  does not;
* ``candidate_timeout`` / ``candidate_error`` — the candidate could not
  answer in budget. Shadow failures are data, not incidents.

The pipeline discards the shadow result by construction (see
``pipeline.run_ensemble``): the response with shadow on is the response
with shadow off, and ``tests/ml_runtime`` asserts exactly that.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "CATEGORIES",
    "CandidatePredictor",
    "ShadowRecord",
    "ShadowRunner",
    "categorise",
    "summarise",
]

#: Every category a record may carry. The report counts exactly these.
CATEGORIES: tuple[str, ...] = (
    "agree",
    "candidate_higher",
    "candidate_lower",
    "candidate_crisis_only",
    "production_crisis_only",
    "candidate_timeout",
    "candidate_error",
)

_SEVERITY = {"none": 0, "low": 1, "medium": 2, "high": 3, "imminent": 4}
_CRISIS = frozenset({"high", "imminent"})


class CandidatePredictor(Protocol):
    """The candidate's one method (an ``OnnxModelRuntime`` satisfies it)."""

    def predict(self, text: str) -> Any: ...


def categorise(production_level: str, candidate_level: str) -> str:
    """The disagreement category for one (production, candidate) pair."""

    production = production_level.strip().lower()
    candidate = candidate_level.strip().lower()
    if production == candidate:
        return "agree"
    if candidate in _CRISIS and production not in _CRISIS:
        return "candidate_crisis_only"
    if production in _CRISIS and candidate not in _CRISIS:
        return "production_crisis_only"
    return (
        "candidate_higher"
        if _SEVERITY.get(candidate, 0) > _SEVERITY.get(production, 0)
        else "candidate_lower"
    )


@dataclass(frozen=True)
class ShadowRecord:
    """One shadow comparison. Metadata only — there is no text field."""

    category: str
    production_level: str
    candidate_level: str | None
    candidate_latency_ms: float | None
    production_version: str
    candidate_version: str


class ShadowRunner:
    """Fire-and-forget candidate scoring with a bounded in-memory record.

    ``observe`` enqueues and returns; the response path never waits for
    the candidate. Records also append to ``log_path`` (JSONL, categories
    only) when one is configured, which is what the report CLI reads.
    """

    def __init__(
        self,
        candidate: CandidatePredictor,
        *,
        candidate_version: str,
        candidate_labels: Sequence[str],
        log_path: str | Path | None = None,
        max_records: int = 10000,
        max_workers: int = 2,
    ) -> None:
        self._candidate = candidate
        self._candidate_version = candidate_version
        self._labels = tuple(candidate_labels)
        self._log_path = Path(log_path) if log_path else None
        self._max_records = max_records
        self._records: list[ShadowRecord] = []
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ml-shadow")
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def warmup(self) -> bool:
        """Warm the candidate session (startup). Best-effort, never raises."""

        warm = getattr(self._candidate, "warmup", None)
        if warm is None:
            return False
        try:
            return bool(warm())
        except Exception:
            return False

    @property
    def records(self) -> list[ShadowRecord]:
        with self._lock:
            return list(self._records)

    def observe(self, *, production_level: str, production_version: str, text: str) -> None:
        """Enqueue one shadow comparison. Never raises, never blocks."""

        # Executor shut down: shadow is best-effort, the response already left.
        with suppress(RuntimeError):
            self._executor.submit(self._run_one, production_level, production_version, text)

    def _run_one(self, production_level: str, production_version: str, text: str) -> None:
        started = time.perf_counter()
        candidate_level: str | None = None
        category: str
        try:
            prediction = self._candidate.predict(text)
            probabilities = tuple(float(value) for value in prediction.probabilities)
            best = max(range(len(probabilities)), key=lambda i: probabilities[i])
            candidate_level = self._labels[best] if best < len(self._labels) else None
            category = (
                categorise(production_level, candidate_level)
                if candidate_level is not None
                else "candidate_error"
            )
        except Exception as exc:  # timeout vs everything else, both categories
            # The runtime folds its timeout into ModelRuntimeUnavailableError
            # with "timed out" in the message; a bare TimeoutError is the
            # other shape a candidate can raise. Both are the same category.
            is_timeout = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
            category = "candidate_timeout" if is_timeout else "candidate_error"
        latency = (time.perf_counter() - started) * 1000.0
        record = ShadowRecord(
            category=category,
            production_level=production_level,
            candidate_level=candidate_level,
            candidate_latency_ms=round(latency, 3),
            production_version=production_version,
            candidate_version=self._candidate_version,
        )
        with self._lock:
            self._records.append(record)
            if len(self._records) > self._max_records:
                del self._records[: len(self._records) - self._max_records]
            if self._log_path is not None:
                with self._log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")

    def flush(self, timeout: float = 10.0) -> None:
        """Wait for queued comparisons (tests and the report CLI)."""

        self._executor.shutdown(wait=True)
        # Recreate so a flushed runner can keep observing in long-lived apps.
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ml-shadow")

    def summary(self) -> dict[str, Any]:
        return summarise(self.records)


def summarise(records: Sequence[ShadowRecord]) -> dict[str, Any]:
    """Counts by category + candidate latency — the CLI report's payload."""

    counts = Counter(record.category for record in records)
    latencies = sorted(
        record.candidate_latency_ms for record in records if record.candidate_latency_ms is not None
    )
    total = len(records)
    disagreements = total - counts.get("agree", 0)
    return {
        "total": total,
        "counts": {category: counts.get(category, 0) for category in CATEGORIES},
        "disagreement_rate": (disagreements / total) if total else 0.0,
        "candidate_latency_ms": {
            "p50": latencies[len(latencies) // 2] if latencies else None,
            "p95": latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
            if latencies
            else None,
        },
    }
