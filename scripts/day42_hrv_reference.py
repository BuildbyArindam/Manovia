#!/usr/bin/env python3
"""Day 42 — fixture generator and NumPy reference for the HRV signal pipeline.

The TypeScript signal pipeline (frontend/src/features/biofeedback/signal) is
compared against this script, which re-implements the documented algorithm
(docs/adr/0041-biofeedback-hrv.md) independently with NumPy. The unit tests
assert agreement to 1e-9 ms and report the actual error.

What this script writes, under frontend/src/features/biofeedback/signal/fixtures/:

  clean-rsa.json                 deterministic respiratory-sinus-arrhythmia series
  noisy-ectopic-dropouts.json    motion burst, ectopics, range errors, dropout,
                                 and a suspiciously flat sensor segment
  reference.json                 NumPy-computed RMSSD / mean HR / rejection /
                                 gap / quality / per-window metrics

The fixtures are synthetic and deterministic (seeded), recorded-style data:
no personal data, no recordings of a real person (AGENTS.md rule 8).

Usage:
    python scripts/day42_hrv_reference.py           # regenerate fixtures + reference
    python scripts/day42_hrv_reference.py --check   # regenerate in memory, diff
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

FIXTURES = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "src"
    / "features"
    / "biofeedback"
    / "signal"
    / "fixtures"
)

# --- The algorithm under test, as documented in ADR 0041 ------------------
MIN_RR_MS = 300.0
MAX_RR_MS = 2000.0
ECTOPIC_REL_THRESHOLD = 0.2
MEDIAN_RADIUS = 2
GAP_FACTOR = 1.75
GAP_SLACK_MS = 250.0

WINDOW_MS = 60_000.0
STEP_MS = 30_000.0
MIN_BEATS = 15
MIN_SUCCESSIVE_PAIRS = 10
QUALITY_GOOD = 0.85
QUALITY_FAIR = 0.65
CONTACT_LOSS_CAP = 0.35
FLATLINE_DELTA_MS = 1.0
FLATLINE_CAP_FRACTION = 0.8


class Lcg:
    """Small seeded LCG so fixtures are byte-stable across machines."""

    def __init__(self, seed: int) -> None:
        self.state = seed % (2**31 - 1) or 1

    def uniform(self) -> float:
        self.state = (48271 * self.state) % (2**31 - 1)
        return self.state / (2**31 - 1)

    def normal(self) -> float:
        # Box-Muller from two uniforms; deterministic and dependency-free.
        u1 = max(self.uniform(), 1e-12)
        u2 = self.uniform()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def rsa_series(
    n: int,
    *,
    seed: int,
    base_ms: float = 800.0,
    rsa_ms: float = 55.0,
    breaths_per_min: float = 6.0,
    mayer_ms: float = 6.0,
    noise_ms: float = 2.5,
) -> list[dict]:
    """RR intervals with respiratory sinus arrhythmia + a slow Mayer component."""
    rng = Lcg(seed)
    samples: list[dict] = []
    t = 0.0
    for _ in range(n):
        t_seconds = t / 1000.0
        rr = (
            base_ms
            + rsa_ms * math.sin(2.0 * math.pi * breaths_per_min * t_seconds / 60.0)
            + mayer_ms * math.sin(2.0 * math.pi * 0.1 * t_seconds + 1.0)
            + noise_ms * rng.normal()
        )
        rr = round(rr, 3)
        t += rr
        samples.append({"rrMs": rr, "tMs": round(t, 3)})
    return samples


def build_clean() -> dict:
    return {
        "name": "clean-rsa",
        "description": (
            "Deterministic respiratory-sinus-arrhythmia series: 240 intervals at "
            "~6 breaths/min on an 800 ms base, small Mayer component and 2.5 ms "
            "noise. No artefacts. Synthetic, seeded; not a real person."
        ),
        "samples": rsa_series(240, seed=42),
    }


def build_noisy() -> dict:
    """Three segments: clean, motion burst + dropout, then a flat sensor."""
    base = rsa_series(240, seed=4242)
    rrs = []
    for i, sample in enumerate(base):
        rr = sample["rrMs"]
        if 80 <= i < 140:
            # Motion burst: every third beat reads as an ectopic pair member,
            # plus two implausible extremes.
            if i % 3 == 0:
                rr = round(rr * 1.28, 3)
            elif i % 3 == 1:
                rr = round(rr * 0.78, 3)
            if i == 95:
                rr = 250.0
            if i == 110:
                rr = 2300.0
        if 140 <= i < 240:
            # Suspiciously flat sensor output: identical values for 100 beats
            # (~80 s), long enough that a full 60 s window sits inside it.
            rr = 800.0
        rrs.append(rr)

    # Timestamps are the beat timeline of the *modified* series (what a real
    # recording carries: detection times, so ectopics shift the clock too).
    samples = []
    t = 0.0
    for i, rr in enumerate(rrs):
        t += rr
        t_ms = t
        if i > 125:
            # Dropout: ~3.8 s of beats missing between index 125 and 126. The
            # clock runs on (the gap is real time), exactly as in a CSV export
            # where a slice of the recording was lost.
            t_ms += 3800.0
        samples.append({"rrMs": rr, "tMs": round(t_ms, 3)})

    return {
        "name": "noisy-ectopic-dropouts",
        "description": (
            "Deterministic synthetic series with a motion burst (ectopic pairs "
            "every third beat), two range errors (250 ms, 2300 ms), a 3.8 s "
            "dropout, and 100 flat identical intervals. Synthetic, seeded; not "
            "a real person."
        ),
        "samples": samples,
    }


# --- The algorithm under test, mirrored ----------------------------------


def detect_gaps(samples: list[dict]) -> tuple[list[dict], set[int]]:
    gaps: list[dict] = []
    break_after: set[int] = set()
    rrs = np.array([s["rrMs"] for s in samples], dtype=np.float64)
    median_rr = float(np.median(rrs)) if len(rrs) else 0.0
    for i in range(len(samples) - 1):
        current, nxt = samples[i], samples[i + 1]
        spacing = nxt["tMs"] - current["tMs"]
        limit = nxt["rrMs"] * GAP_FACTOR
        explicit = bool(nxt.get("breakBefore", False))
        if explicit or spacing > limit + GAP_SLACK_MS:
            gap_ms = max(0.0, spacing - nxt["rrMs"])
            missing = (
                max(0, int(round(gap_ms / median_rr)) - 1) if median_rr > 0 else 0
            )
            gaps.append(
                {
                    "afterRawIndex": i,
                    "tBeforeMs": current["tMs"],
                    "tAfterMs": nxt["tMs"],
                    "gapMs": round(gap_ms, 3),
                    "estimatedMissingBeats": missing,
                }
            )
            break_after.add(i + 1)
    return gaps, break_after


def reject_artefacts(samples: list[dict]) -> dict:
    gaps, break_after = detect_gaps(samples)
    rrs = [s["rrMs"] for s in samples]
    in_range = [MIN_RR_MS <= rr <= MAX_RR_MS for rr in rrs]

    rejected: list[dict] = []
    is_rejected: list[bool] = []
    for i, rr in enumerate(rrs):
        if not in_range[i]:
            rejected.append(
                {"rawIndex": i, "rrMs": rr, "tMs": samples[i]["tMs"], "reason": "out-of-range"}
            )
            is_rejected.append(True)
            continue
        window = [
            rrs[k]
            for k in range(i - MEDIAN_RADIUS, i + MEDIAN_RADIUS + 1)
            if 0 <= k < len(rrs) and in_range[k]
        ]
        local_median = float(np.median(window)) if window else rr
        deviation = abs(rr - local_median) / local_median
        if deviation > ECTOPIC_REL_THRESHOLD:
            rejected.append(
                {"rawIndex": i, "rrMs": rr, "tMs": samples[i]["tMs"], "reason": "ectopic"}
            )
            is_rejected.append(True)
        else:
            is_rejected.append(False)

    accepted: list[dict] = []
    previous: int | None = None
    for i, sample in enumerate(samples):
        if is_rejected[i]:
            continue
        break_before = previous is not None and (
            i != previous + 1 or i in break_after
        )
        accepted.append(
            {
                "rrMs": sample["rrMs"],
                "tMs": sample["tMs"],
                "rawIndex": i,
                "breakBefore": break_before,
            }
        )
        previous = i

    return {"accepted": accepted, "rejected": rejected, "gaps": gaps, "total": len(samples)}


def rmssd(accepted: list[dict], min_pairs: int = MIN_SUCCESSIVE_PAIRS) -> float | None:
    diffs = [
        accepted[i + 1]["rrMs"] - accepted[i]["rrMs"]
        for i in range(len(accepted) - 1)
        if not accepted[i + 1]["breakBefore"]
    ]
    if len(diffs) < min_pairs:
        return None
    arr = np.asarray(diffs, dtype=np.float64)
    return float(np.sqrt(np.mean(arr * arr)))


def mean_hr(accepted: list[dict]) -> float | None:
    if not accepted:
        return None
    arr = np.asarray([s["rrMs"] for s in accepted], dtype=np.float64)
    return float(60_000.0 / np.mean(arr))


def flatline_fraction(accepted: list[dict]) -> float:
    pairs = [
        1.0
        if abs(accepted[i + 1]["rrMs"] - accepted[i]["rrMs"]) < FLATLINE_DELTA_MS
        else 0.0
        for i in range(len(accepted) - 1)
        if not accepted[i + 1]["breakBefore"]
    ]
    if not pairs:
        return 0.0
    return float(np.mean(np.asarray(pairs)))


def quality(accepted_n: int, rejected_n: int, gap_ms: float, window_ms: float,
            flat: float, contact: bool | None = None) -> dict:
    total = accepted_n + rejected_n
    artefact_score = 0.0 if total == 0 else accepted_n / total
    gap_score = 0.0 if window_ms <= 0 else 1.0 - min(1.0, max(0.0, gap_ms / window_ms))
    score = 0.65 * artefact_score + 0.35 * gap_score
    if contact is False:
        score = min(score, CONTACT_LOSS_CAP)
    if flat > FLATLINE_CAP_FRACTION:
        score = min(score, CONTACT_LOSS_CAP)
    score = min(1.0, max(0.0, score))
    if accepted_n < MIN_BEATS:
        level = "poor"
    elif score >= QUALITY_GOOD:
        level = "good"
    elif score >= QUALITY_FAIR:
        level = "fair"
    else:
        level = "poor"
    return {"level": level, "score": score}


def windows(series: dict) -> list[dict]:
    accepted = series["accepted"]
    rejected = series["rejected"]
    gaps = series["gaps"]
    if not accepted:
        return []
    first_t = accepted[0]["tMs"]
    last_t = accepted[-1]["tMs"]
    out = []
    start = first_t - WINDOW_MS
    while start < last_t:
        end = start + WINDOW_MS
        in_window = [s for s in accepted if start < s["tMs"] <= end]
        if not in_window:
            start += STEP_MS
            continue
        rejected_in = [r for r in rejected if start < r["tMs"] <= end]
        gap_ms = sum(g["gapMs"] for g in gaps if start < g["tAfterMs"] <= end)
        flat = flatline_fraction(in_window)
        q = quality(len(in_window), len(rejected_in), gap_ms, WINDOW_MS, flat)
        withheld = q["level"] == "poor"
        pairs = sum(
            1
            for i in range(len(in_window) - 1)
            if not in_window[i + 1]["breakBefore"]
        )
        r = rmssd(in_window, MIN_SUCCESSIVE_PAIRS)
        h = mean_hr(in_window)
        out.append(
            {
                "startMs": round(start, 3),
                "endMs": round(end, 3),
                "acceptedBeats": len(in_window),
                "rejectedBeats": len(rejected_in),
                "gapMs": round(gap_ms, 3),
                "successivePairs": pairs,
                "flatlineFraction": round(flat, 6),
                "quality": q["level"],
                "qualityScore": round(q["score"], 9),
                "rmssdMs": None if withheld else (None if r is None else round(r, 9)),
                "meanHrBpm": None if withheld else (None if h is None else round(h, 9)),
            }
        )
        start += STEP_MS
    return out


def reference_for(fixture: dict) -> dict:
    series = reject_artefacts(fixture["samples"])
    accepted = series["accepted"]
    rejected = series["rejected"]
    gaps = series["gaps"]
    duration = (accepted[-1]["tMs"] - accepted[0]["tMs"]) if accepted else 0.0
    flat = flatline_fraction(accepted)
    q = quality(len(accepted), len(rejected), sum(g["gapMs"] for g in gaps), duration, flat)
    withheld = q["level"] == "poor"
    r = rmssd(accepted, MIN_SUCCESSIVE_PAIRS)
    h = mean_hr(accepted)
    return {
        "acceptedCount": len(accepted),
        "rejectedCount": len(rejected),
        "rejected": [
            {"rawIndex": rj["rawIndex"], "reason": rj["reason"]} for rj in rejected
        ],
        "gaps": [
            {
                "afterRawIndex": g["afterRawIndex"],
                "gapMs": g["gapMs"],
                "estimatedMissingBeats": g["estimatedMissingBeats"],
            }
            for g in gaps
        ],
        "flatlineFraction": round(flat, 6),
        "fullSeries": {
            "durationMs": round(duration, 3),
            "quality": q["level"],
            "qualityScore": round(q["score"], 9),
            "rmssdMs": None if withheld or r is None else round(r, 9),
            "meanHrBpm": None if withheld or h is None else round(h, 9),
        },
        "windows": windows(series),
    }


def build_csv_fixture(clean: dict) -> str:
    """A small health-app-style export: timestamp + rr_ms, with 2 artefacts."""
    rows = [
        "# Manovia Day 42 sample export (synthetic, seeded); not a real person",
        "timestamp,rr_ms",
    ]
    samples = clean["samples"][:40]
    # Timestamps follow the *exported* intervals (consistent beat timeline):
    # an ectopic beat shifts the clock in the file exactly as it does in life.
    t = 0.0
    for i, sample in enumerate(samples):
        rr = sample["rrMs"]
        if i == 10:
            rr = 250.0  # out-of-range
        if i == 20:
            rr = round(rr * 1.3, 3)  # ectopic
        t += rr
        offset_ms = int(round(t))
        seconds = offset_ms // 1000
        millis = offset_ms % 1000
        stamp = f"2026-10-16T18:{seconds // 60:02d}:{seconds % 60:02d}.{millis:03d}Z"
        rows.append(f"{stamp},{rr}")
    return "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if files would change")
    args = parser.parse_args()

    clean = build_clean()
    noisy = build_noisy()
    reference = {
        "generator": "scripts/day42_hrv_reference.py",
        "numpyVersion": np.__version__,
        "algorithm": {
            "minRrMs": MIN_RR_MS,
            "maxRrMs": MAX_RR_MS,
            "ectopicRelThreshold": ECTOPIC_REL_THRESHOLD,
            "medianRadius": MEDIAN_RADIUS,
            "gapFactor": GAP_FACTOR,
            "gapSlackMs": GAP_SLACK_MS,
            "windowMs": WINDOW_MS,
            "stepMs": STEP_MS,
            "minBeats": MIN_BEATS,
            "minSuccessivePairs": MIN_SUCCESSIVE_PAIRS,
        },
        clean["name"]: reference_for(clean),
        noisy["name"]: reference_for(noisy),
    }

    files = {
        FIXTURES / "clean-rsa.json": json.dumps(clean, indent=2) + "\n",
        FIXTURES / "noisy-ectopic-dropouts.json": json.dumps(noisy, indent=2) + "\n",
        FIXTURES / "reference.json": json.dumps(reference, indent=2) + "\n",
        FIXTURES.parent.parent / "import" / "fixtures" / "health-export-sample.csv":
            build_csv_fixture(clean),
    }

    if args.check:
        changed = []
        for path, content in files.items():
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                changed.append(str(path))
        if changed:
            print("would change:", *changed, sep="\n  ")
            return 1
        print("fixtures and reference are up to date")
        return 0

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"wrote {path.relative_to(Path(__file__).resolve().parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
