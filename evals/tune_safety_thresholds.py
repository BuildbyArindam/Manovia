"""Choose the ensemble thresholds on the DEV split only.

This script exists to make the split discipline auditable: the two knobs the
ensemble reads from Settings — ``safety_ml_min_confidence`` and
``safety_ml_suspicion_floor`` — are picked here by grid search over
``crisis_cases_dev.jsonl``. It never opens the test file; the test split is
frozen (see ``evals/datasets/manifest.json``).

Objective, in order of priority:

1. **crisis recall** — P(predicted HIGH/IMMINENT | true HIGH/IMMINENT) on dev.
   This is the number the Day 9 target is written against (>= 0.97).
2. **fewest crisis false alarms** — among threshold triples at the best recall,
   prefer the one that hands a crisis card (HIGH/IMMINENT) to the fewest
   true-NONE/LOW cases. A crisis card on an ordinary bad day is the expensive
   error here; the soft MEDIUM check-in the ensemble gives uncertain cases is
   cheap by design and is tracked separately, not minimised.

The winner is printed and written to ``evals/reports/threshold_tuning.md``;
its values are then copied into ``app.core.config`` defaults with a comment
pointing here.

Run from the repository root:

    .venv/bin/python evals/tune_safety_thresholds.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from itertools import product
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.safety.base import RiskLevel  # noqa: E402
from app.services.safety.ensemble import combine  # noqa: E402
from app.services.safety.ml_classifier import build_ml_classifier  # noqa: E402
from app.services.safety.rules import build_engine  # noqa: E402

DATASET_DIR = REPO_ROOT / "evals" / "datasets"
REPORTS_DIR = REPO_ROOT / "evals" / "reports"

CONFIDENCE_GRID = [round(0.30 + 0.05 * i, 2) for i in range(9)]  # 0.30..0.70
MASS_GRID = [round(0.30 + 0.05 * i, 2) for i in range(7)]  # 0.30..0.60
FLOOR_GRID = [round(0.10 + 0.05 * i, 2) for i in range(7)]  # 0.10..0.40

CRISIS_LEVELS = {"HIGH", "IMMINENT"}


def load_dev() -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    with (DATASET_DIR / "crisis_cases_dev.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def main() -> None:
    engine = build_engine()
    classifier = build_ml_classifier()
    if not classifier.enabled:
        raise SystemExit("classifier artifact missing; run evals/train_safety_classifier.py")

    cases = load_dev()
    # Score every case once; only the thresholds vary below.
    scored = []
    for case in cases:
        assessment = engine.assess(case["text"], language=case["lang"])
        prediction = classifier.predict(case["text"])
        scored.append((case, assessment, prediction))

    results = []
    for min_confidence, crisis_mass_floor, suspicion_floor in product(
        CONFIDENCE_GRID, MASS_GRID, FLOOR_GRID
    ):
        if crisis_mass_floor <= suspicion_floor:
            # Equal floors would empty the uncertain-check-in band
            # [suspicion_floor, crisis_mass_floor), silently disabling the
            # gentle MEDIUM policy — keep the band non-empty.
            continue
        crisis_hit = crisis_total = 0
        crisis_false_alarms = 0
        checkin_false_alarms = 0
        for case, assessment, prediction in scored:
            decision = combine(
                assessment,
                prediction,
                min_confidence=min_confidence,
                crisis_mass_floor=crisis_mass_floor,
                suspicion_floor=suspicion_floor,
            )
            predicted_crisis = decision.level >= RiskLevel.HIGH
            if case["label"] in CRISIS_LEVELS:
                crisis_total += 1
                crisis_hit += predicted_crisis
            elif case["label"] in ("NONE", "LOW"):
                if predicted_crisis:
                    crisis_false_alarms += 1
                elif decision.level >= RiskLevel.MEDIUM:
                    checkin_false_alarms += 1
        recall = crisis_hit / crisis_total if crisis_total else 1.0
        results.append(
            (
                min_confidence,
                crisis_mass_floor,
                suspicion_floor,
                recall,
                crisis_false_alarms,
                checkin_false_alarms,
            )
        )

    best_recall = max(row[3] for row in results)
    winners = [row for row in results if row[3] == best_recall]
    # Fewest crisis-card false alarms, then fewest check-ins, then the most
    # conservative confidence (a higher bar for "confident" raises).
    winners.sort(key=lambda row: (row[4], row[5], -row[0]))
    chosen = winners[0]

    lines = [
        "# Ensemble threshold tuning (Day 9)",
        "",
        f"- Date: {date.today().isoformat()}",
        f"- Split used: **dev only** ({len(cases)} cases). The test split was never opened.",
        f"- Crisis cases in dev (HIGH+IMMINENT): "
        f"{sum(1 for case in cases if case['label'] in CRISIS_LEVELS)}",
        f"- Grid: min_confidence {CONFIDENCE_GRID[0]}..{CONFIDENCE_GRID[-1]}, "
        f"crisis_mass_floor {MASS_GRID[0]}..{MASS_GRID[-1]}, "
        f"suspicion_floor {FLOOR_GRID[0]}..{FLOOR_GRID[-1]}",
        "",
        f"**Chosen:** `safety_ml_min_confidence = {chosen[0]:.2f}`, "
        f"`safety_ml_crisis_mass_floor = {chosen[1]:.2f}`, "
        f"`safety_ml_suspicion_floor = {chosen[2]:.2f}`",
        "",
        f"Dev crisis recall at the chosen thresholds: **{chosen[3]:.4f}**; "
        f"NONE/LOW cases handed a crisis card (HIGH+): **{chosen[4]}**; "
        f"NONE/LOW cases softened to a MEDIUM check-in: **{chosen[5]}**.",
        "",
        "Selection rule: maximise crisis recall; break ties by fewest crisis-card false alarms.",
        "",
        "| min_confidence | crisis_mass_floor | suspicion_floor | dev crisis recall | crisis FAs | check-in FAs |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in sorted(results, key=lambda r: (-r[3], r[4], r[5]))[:15]:
        marker = " **(chosen)**" if row[:3] == chosen[:3] else ""
        lines.append(
            f"| {row[0]:.2f} | {row[1]:.2f} | {row[2]:.2f} | {row[3]:.4f} | {row[4]} | {row[5]} |{marker}"
        )
    lines.append("")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "threshold_tuning.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
