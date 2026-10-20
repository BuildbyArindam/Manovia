"""Privacy-preserving drift monitor (Day 48).

Tracks only aggregate statistics — never raw text, never embeddings, never
user ids.  All inputs are bucketed on the fly; the store holds counts,
sums and binned histograms.

Monitored signals (per brief):
- input length distributions
- language mix
- embedding centroid shift (computed on-the-fly, storing only aggregate stats)
- model confidence distributions
- safety-level rates
- abstention rate
- guard block rate

Alerts via PSI/KS against baselines; weekly drift report via metrics.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# ---- binning helpers ----


def _length_bin(length: int) -> str:
    if length < 20:
        return "0-20"
    if length < 50:
        return "20-50"
    if length < 100:
        return "50-100"
    if length < 200:
        return "100-200"
    if length < 400:
        return "200-400"
    return "400+"


def _confidence_bin(conf: float) -> str:
    # 5 bins over [0,1]
    if conf < 0.2:
        return "0.0-0.2"
    if conf < 0.4:
        return "0.2-0.4"
    if conf < 0.6:
        return "0.4-0.6"
    if conf < 0.8:
        return "0.6-0.8"
    return "0.8-1.0"


LENGTH_BINS = ["0-20", "20-50", "50-100", "100-200", "200-400", "400+"]
CONF_BINS = ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
SAFETY_LEVELS = ["none", "low", "medium", "high", "imminent"]
LANGUAGES = ["en", "hi", "hi-Latn", "bn", "other"]


def _psi(expected: list[int], actual: list[int], epsilon: float = 1e-4) -> float:
    import math as _math

    exp_total = sum(expected)
    act_total = sum(actual)
    if exp_total == 0 or act_total == 0:
        return 0.0
    psi = 0.0
    for e, a in zip(expected, actual, strict=False):
        e_pct = max(e / exp_total, epsilon)
        a_pct = max(a / act_total, epsilon)
        psi += (a_pct - e_pct) * _math.log(a_pct / e_pct)
    return psi


def _ks_from_binned(expected_counts: dict[str, int], actual_counts: dict[str, int]) -> float:
    """KS over binned categorical distribution via cumulative proportions.

    For privacy we never store samples; we approximate KS as max CDF delta
    over ordered bins.
    """
    # Order bins by key (works for our named bins if we define order)
    # For categorical like language, order alphabetically — still meaningful.
    all_keys = sorted(set(expected_counts) | set(actual_counts))
    exp_total = sum(expected_counts.values()) or 1
    act_total = sum(actual_counts.values()) or 1
    cum_e = cum_a = 0.0
    max_d = 0.0
    for k in all_keys:
        cum_e += expected_counts.get(k, 0) / exp_total
        cum_a += actual_counts.get(k, 0) / act_total
        d = abs(cum_e - cum_a)
        if d > max_d:
            max_d = d
    return max_d


# ---- data structures ----


@dataclass
class AggregateSnapshot:
    window: str  # e.g. "baseline" or "2026-W42"
    total_inputs: int = 0
    length_hist: dict[str, int] = field(default_factory=lambda: Counter())
    language_hist: dict[str, int] = field(default_factory=lambda: Counter())
    confidence_hist: dict[str, int] = field(default_factory=lambda: Counter())
    safety_hist: dict[str, int] = field(default_factory=lambda: Counter())
    # embedding centroid: we store only the running mean vector (privacy: no raw embeddings)
    # For this sandbox we store a 4-dim fake centroid summary (mean of hashed dims)
    embedding_centroid: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    embedding_count: int = 0
    abstained: int = 0
    guard_blocked: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "total_inputs": self.total_inputs,
            "length_hist": dict(self.length_hist),
            "language_hist": dict(self.language_hist),
            "confidence_hist": dict(self.confidence_hist),
            "safety_hist": dict(self.safety_hist),
            "embedding_centroid": list(self.embedding_centroid),
            "embedding_count": self.embedding_count,
            "abstained": self.abstained,
            "guard_blocked": self.guard_blocked,
            "abstention_rate": (self.abstained / self.total_inputs) if self.total_inputs else 0.0,
            "guard_block_rate": (self.guard_blocked / self.total_inputs)
            if self.total_inputs
            else 0.0,
        }


@dataclass
class DriftAlert:
    metric: str
    psi: float
    ks: float
    baseline_window: str
    current_window: str
    severity: str  # "none" | "warning" | "critical"
    message: str


PSI_WARNING = 0.10
PSI_CRITICAL = 0.25
KS_WARNING = 0.15
KS_CRITICAL = 0.30
CENTROID_SHIFT_WARNING = 0.10
CENTROID_SHIFT_CRITICAL = 0.25


def _severity(psi: float, ks: float) -> str:
    if psi >= PSI_CRITICAL or ks >= KS_CRITICAL:
        return "critical"
    if psi >= PSI_WARNING or ks >= KS_WARNING:
        return "warning"
    return "none"


class DriftMonitor:
    """Privacy-preserving monitor: aggregates only, no text stored."""

    def __init__(self) -> None:
        # window -> snapshot
        self.snapshots: dict[str, AggregateSnapshot] = {}
        # baseline is the first snapshot or explicitly set
        self.baseline_window: str | None = None

    # ---- recording ----

    def record(
        self,
        window: str,
        *,
        text_length: int,
        language: str,
        confidence: float | None = None,
        safety_level: str | None = None,
        abstained: bool = False,
        guard_blocked: bool = False,
        embedding: list[float] | None = None,
    ) -> None:
        """Record one input's aggregates into ``window``.

        Privacy: ``text_length`` is a number derived from length, not text.
        ``embedding`` is consumed to update centroid then discarded.
        """
        snap = self.snapshots.get(window)
        if snap is None:
            snap = AggregateSnapshot(window=window)
            self.snapshots[window] = snap
            if self.baseline_window is None:
                self.baseline_window = window

        snap.total_inputs += 1
        snap.length_hist[_length_bin(text_length)] += 1
        lang_key = language if language in LANGUAGES else "other"
        snap.language_hist[lang_key] += 1
        if confidence is not None:
            snap.confidence_hist[_confidence_bin(confidence)] += 1
        if safety_level is not None:
            lvl = safety_level if safety_level in SAFETY_LEVELS else "none"
            snap.safety_hist[lvl] += 1
        if abstained:
            snap.abstained += 1
        if guard_blocked:
            snap.guard_blocked += 1
        if embedding is not None:
            # running mean update, only storing centroid
            n = snap.embedding_count
            # extend centroid if embedding longer
            dim = len(embedding)
            if len(snap.embedding_centroid) < dim:
                snap.embedding_centroid.extend([0.0] * (dim - len(snap.embedding_centroid)))
            for i, v in enumerate(embedding):
                snap.embedding_centroid[i] = (snap.embedding_centroid[i] * n + v) / (n + 1)
            snap.embedding_count += 1

    def set_baseline(self, window: str) -> None:
        if window not in self.snapshots:
            raise ValueError(f"window {window!r} not found")
        self.baseline_window = window

    # ---- drift computation ----

    def _centroid_shift(self, base: AggregateSnapshot, cur: AggregateSnapshot) -> float:
        if base.embedding_count == 0 or cur.embedding_count == 0:
            return 0.0
        # Euclidean distance between centroids
        dim = min(len(base.embedding_centroid), len(cur.embedding_centroid))
        s = 0.0
        for i in range(dim):
            d = cur.embedding_centroid[i] - base.embedding_centroid[i]
            s += d * d
        return math.sqrt(s)

    def check_drift(
        self,
        current_window: str,
        baseline_window: str | None = None,
    ) -> list[DriftAlert]:
        """Compute PSI/KS alerts for current vs baseline."""
        base_win = baseline_window or self.baseline_window
        if base_win is None or base_win not in self.snapshots:
            return []
        if current_window not in self.snapshots:
            return []
        base = (
            self.snapshots[current_window] if False else self.snapshots[base_win]
        )  # keep mypy happy
        cur = self.snapshots[current_window]
        base = self.snapshots[base_win]

        alerts: list[DriftAlert] = []

        # helper to make alert per metric
        def check_metric(
            name: str,
            base_hist: dict[str, int],
            cur_hist: dict[str, int],
            bins: list[str] | None = None,
        ) -> None:
            if bins is not None:
                exp = [base_hist.get(b, 0) for b in bins]
                act = [cur_hist.get(b, 0) for b in bins]
                psi = _psi(exp, act)
                ks = _ks_from_binned(base_hist, cur_hist)
            else:
                psi = _psi(
                    [base_hist.get(k, 0) for k in sorted(set(base_hist) | set(cur_hist))],
                    [cur_hist.get(k, 0) for k in sorted(set(base_hist) | set(cur_hist))],
                )
                ks = _ks_from_binned(base_hist, cur_hist)
            sev = _severity(psi, ks)
            if sev != "none":
                alerts.append(
                    DriftAlert(
                        metric=name,
                        psi=psi,
                        ks=ks,
                        baseline_window=base_win,
                        current_window=current_window,
                        severity=sev,
                        message=f"{name} drift: PSI={psi:.3f} KS={ks:.3f} ({sev})",
                    )
                )
            else:
                # still emit info alert if we want to show it passed? For now only warnings.
                pass

        check_metric("input_length", dict(base.length_hist), dict(cur.length_hist), LENGTH_BINS)
        check_metric("language_mix", dict(base.language_hist), dict(cur.language_hist), LANGUAGES)
        check_metric("confidence", dict(base.confidence_hist), dict(cur.confidence_hist), CONF_BINS)
        check_metric("safety_level", dict(base.safety_hist), dict(cur.safety_hist), SAFETY_LEVELS)

        # abstention rate drift: difference in rates
        base_rate = (base.abstained / base.total_inputs) if base.total_inputs else 0.0
        cur_rate = (cur.abstained / cur.total_inputs) if cur.total_inputs else 0.0
        rate_diff = abs(cur_rate - base_rate)
        if rate_diff >= 0.05:  # 5pp shift is warning
            sev = "critical" if rate_diff >= 0.10 else "warning"
            alerts.append(
                DriftAlert(
                    metric="abstention_rate",
                    psi=rate_diff,
                    ks=rate_diff,
                    baseline_window=base_win,
                    current_window=current_window,
                    severity=sev,
                    message=f"abstention_rate {base_rate:.2%} -> {cur_rate:.2%} (Δ={rate_diff:.2%}, {sev})",  # noqa: E501
                )
            )

        base_guard = (base.guard_blocked / base.total_inputs) if base.total_inputs else 0.0
        cur_guard = (cur.guard_blocked / cur.total_inputs) if cur.total_inputs else 0.0
        guard_diff = abs(cur_guard - base_guard)
        if guard_diff >= 0.03:
            sev = "critical" if guard_diff >= 0.08 else "warning"
            alerts.append(
                DriftAlert(
                    metric="guard_block_rate",
                    psi=guard_diff,
                    ks=guard_diff,
                    baseline_window=base_win,
                    current_window=current_window,
                    severity=sev,
                    message=f"guard_block_rate {base_guard:.2%} -> {cur_guard:.2%} (Δ={guard_diff:.2%}, {sev})",  # noqa: E501
                )
            )

        # embedding centroid shift
        shift = self._centroid_shift(base, cur)
        if shift >= CENTROID_SHIFT_WARNING:
            sev = "critical" if shift >= CENTROID_SHIFT_CRITICAL else "warning"
            alerts.append(
                DriftAlert(
                    metric="embedding_centroid_shift",
                    psi=shift,
                    ks=shift,
                    baseline_window=base_win,
                    current_window=current_window,
                    severity=sev,
                    message=f"embedding_centroid_shift={shift:.3f} ({sev})",
                )
            )

        return alerts

    def weekly_report(
        self, current_window: str, baseline_window: str | None = None
    ) -> dict[str, Any]:
        base_win = baseline_window or self.baseline_window or ""
        cur_snap = self.snapshots.get(current_window)
        base_snap = self.snapshots.get(base_win) if base_win else None
        alerts = (
            self.check_drift(current_window, baseline_window=base_win)
            if base_win and cur_snap
            else []
        )
        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "baseline_window": base_win,
            "current_window": current_window,
            "baseline": base_snap.to_dict() if base_snap else None,
            "current": cur_snap.to_dict() if cur_snap else None,
            "alerts": [
                {
                    "metric": a.metric,
                    "psi": a.psi,
                    "ks": a.ks,
                    "severity": a.severity,
                    "message": a.message,
                }
                for a in alerts
            ],
            "alert_count": len(alerts),
            "has_critical": any(a.severity == "critical" for a in alerts),
        }
