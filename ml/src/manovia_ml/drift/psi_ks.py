"""PSI and Kolmogorov-Smirnov implementations (no external deps)."""

from __future__ import annotations

import math


def population_stability_index(
    expected_counts: list[int],
    actual_counts: list[int],
    epsilon: float = 1e-4,
) -> float:
    """PSI for binned distributions.  Counts must sum to >0 for each.

    PSI = sum((actual_pct - expected_pct) * ln(actual_pct/expected_pct))
    Standard interpretation: <0.1 no change, 0.1-0.25 moderate, >0.25 large.
    We epsilon-smooth zero bins.
    """
    if len(expected_counts) != len(actual_counts):
        raise ValueError("expected and actual must have same bins")
    exp_total = sum(expected_counts)
    act_total = sum(actual_counts)
    if exp_total == 0 or act_total == 0:
        return 0.0
    psi = 0.0
    for e, a in zip(expected_counts, actual_counts):
        e_pct = max(e / exp_total, epsilon)
        a_pct = max(a / act_total, epsilon)
        psi += (a_pct - e_pct) * math.log(a_pct / e_pct)
    return psi


def kolmogorov_smirnov(
    expected_samples: list[float],
    actual_samples: list[float],
) -> float:
    """Two-sample KS statistic (max absolute CDF difference).

    For binned aggregate use, callers can pass bin centers weighted by counts.
    This is a simple offline implementation — O((n+m) log(n+m)).
    """
    if not expected_samples or not actual_samples:
        return 0.0
    exp = sorted(expected_samples)
    act = sorted(actual_samples)
    # merge sorted unique values
    all_vals = sorted(set(exp + act))
    # CDFs
    n, m = len(exp), len(act)
    i = j = 0
    max_d = 0.0
    for v in all_vals:
        while i < n and exp[i] <= v:
            i += 1
        while j < m and act[j] <= v:
            j += 1
        d = abs(i / n - j / m)
        if d > max_d:
            max_d = d
    return max_d
