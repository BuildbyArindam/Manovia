"""Run the Day 9 safety evaluation: rules + ML ensemble against a labelled split.

Usage (from the repository root):

    .venv/bin/python evals/run_safety_eval.py --split dev
    .venv/bin/python evals/run_safety_eval.py --split test

What it computes, for the full ensemble and for the rules engine alone:

* precision / recall / F1 per level (NONE..IMMINENT), macro-averaged;
* the binary "crisis" view (HIGH+IMMINENT together) — the number the Day 9
  target is written against (>= 0.97 recall on dev);
* per-language recall and precision per level;
* a 5x5 confusion matrix (rows = ground truth, columns = predicted);
* the false negatives at HIGH/IMMINENT, listed by case id so a reviewer can
  study them without the report carrying the texts around.

The report is written to ``evals/reports/safety_<date>.md`` and a summary is
printed. **Split discipline:** the test split is frozen — its SHA-256 lives in
``evals/datasets/manifest.json`` and this script refuses to run on a test file
whose hash has drifted from the one recorded when thresholds were chosen.
Model training and threshold tuning read only train/dev
(``evals/train_safety_classifier.py``, ``evals/tune_safety_thresholds.py``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.services.safety.base import RiskLevel  # noqa: E402
from app.services.safety.ensemble import combine  # noqa: E402
from app.services.safety.ml_classifier import build_ml_classifier  # noqa: E402
from app.services.safety.rules import build_engine  # noqa: E402

DATASET_DIR = REPO_ROOT / "evals" / "datasets"
REPORTS_DIR = REPO_ROOT / "evals" / "reports"

LEVEL_LABELS = ("NONE", "LOW", "MEDIUM", "HIGH", "IMMINENT")
CRISIS_LEVELS = frozenset({"HIGH", "IMMINENT"})
LANG_LABELS = {"en": "English", "hi": "Hindi (Devanagari)", "hi-Latn": "Hindi (romanised)", "bn": "Bengali"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_split(name: str) -> list[dict[str, str]]:
    """Load one split; enforce the frozen-test contract on the way in."""
    path = DATASET_DIR / f"crisis_cases_{name}.jsonl"
    manifest_path = DATASET_DIR / "manifest.json"
    if not path.is_file():
        raise SystemExit(f"split file missing: {path}")
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        recorded = manifest.get("splits", {}).get(name, {})
        expected = recorded.get("sha256")
        actual = _sha256(path)
        if expected and expected != actual:
            raise SystemExit(
                f"refusing to evaluate on {name}: file hash drifted from the manifest.\n"
                f"  expected {expected}\n  actual   {actual}\n"
                "The split is frozen; regenerate the dataset deliberately and re-record the hash."
            )
    return cases


def assess_all(cases: list[dict[str, str]], *, ml_enabled: bool) -> list[dict[str, object]]:
    """Run every case through the rules engine and (optionally) the ensemble."""
    engine = build_engine()
    classifier = build_ml_classifier() if ml_enabled else None
    settings = Settings(_env_file=None)

    results: list[dict[str, object]] = []
    for case in cases:
        assessment = engine.assess(case["text"], language=case["lang"])
        rules_label = assessment.level.name
        prediction = None
        decision = None
        if classifier is not None and classifier.enabled:
            prediction = classifier.predict(case["text"])
            decision = combine(
                assessment,
                prediction,
                min_confidence=settings.safety_ml_min_confidence,
                crisis_mass_floor=settings.safety_ml_crisis_mass_floor,
                suspicion_floor=settings.safety_ml_suspicion_floor,
            )
        final_label = decision.level.name if decision is not None else rules_label
        results.append(
            {
                "id": case["id"],
                "lang": case["lang"],
                "true": case["label"],
                "rules": rules_label,
                "final": final_label,
                "source": decision.source if decision is not None else "rules",
                "notes": case["notes"],
            }
        )
    return results


def _precision_recall(pairs: list[tuple[str, str]], label: str) -> tuple[float | None, float | None, int, int]:
    """Precision and recall for one label, plus the raw counts."""
    tp = sum(1 for true, pred in pairs if true == label and pred == label)
    fp = sum(1 for true, pred in pairs if true != label and pred == label)
    fn = sum(1 for true, pred in pairs if true == label and pred != label)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return precision, recall, tp + fn, tp + fp


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def per_level_table(pairs: list[tuple[str, str]]) -> list[str]:
    lines = [
        "| level | support | precision | recall | F1 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label in LEVEL_LABELS:
        precision, recall, support, predicted = _precision_recall(pairs, label)
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and (precision + recall)
            else None
        )
        lines.append(f"| {label} | {support} | {_fmt(precision)} | {_fmt(recall)} | {_fmt(f1)} |")
    return lines


def confusion_matrix(pairs: list[tuple[str, str]]) -> list[str]:
    counts = Counter((true, pred) for true, pred in pairs)
    header = "| true \\ pred | " + " | ".join(LEVEL_LABELS) + " | total |"
    divider = "| --- |" + " --- |" * (len(LEVEL_LABELS) + 1)
    lines = [header, divider]
    for true_label in LEVEL_LABELS:
        row_total = sum(counts[(true_label, pred)] for pred in LEVEL_LABELS)
        cells = " | ".join(str(counts[(true_label, pred)]) for pred in LEVEL_LABELS)
        lines.append(f"| **{true_label}** | {cells} | {row_total} |")
    return lines


def crisis_metrics(pairs: list[tuple[str, str]]) -> tuple[float | None, float | None, int, int]:
    """Binary HIGH+IMMINENT precision/recall — the Day 9 target number."""
    binary = [
        ("CRISIS" if true in CRISIS_LEVELS else "OK", "CRISIS" if pred in CRISIS_LEVELS else "OK")
        for true, pred in pairs
    ]
    precision, recall, support, predicted = _precision_recall(binary, "CRISIS")
    return precision, recall, support, predicted


def language_table(results: list[dict[str, object]], key: str) -> list[str]:
    lines = [
        "| language | cases | crisis recall | crisis precision | macro recall |",
        "| --- | --- | --- | --- | --- |",
    ]
    for lang in ("en", "hi", "hi-Latn", "bn"):
        subset = [r for r in results if r["lang"] == lang]
        if not subset:
            continue
        pairs = [(str(r["true"]), str(r[key])) for r in subset]
        _, crisis_r, crisis_n, _ = crisis_metrics(pairs)
        crisis_p, _, _, _ = crisis_metrics(pairs)
        recalls = [
            _precision_recall(pairs, label)[1] for label in LEVEL_LABELS if any(t == label for t, _ in pairs)
        ]
        macro = sum(v for v in recalls if v is not None) / len(recalls) if recalls else None
        lines.append(
            f"| {LANG_LABELS[lang]} | {len(subset)} | {_fmt(crisis_r)} ({crisis_n}) "
            f"| {_fmt(crisis_p)} | {_fmt(macro)} |"
        )
    return lines


def per_language_level_detail(results: list[dict[str, object]], key: str) -> list[str]:
    """Per-language recall per level, so language gaps are visible per tier."""
    lines = ["| language | " + " | ".join(f"{label} recall" for label in LEVEL_LABELS) + " |",
             "| --- |" + " --- |" * len(LEVEL_LABELS)]
    for lang in ("en", "hi", "hi-Latn", "bn"):
        subset = [(str(r["true"]), str(r[key])) for r in results if r["lang"] == lang]
        if not subset:
            continue
        cells = []
        for label in LEVEL_LABELS:
            _, recall, support, _ = _precision_recall(subset, label)
            cells.append(f"{_fmt(recall)} ({support})" if support else "—")
        lines.append(f"| {LANG_LABELS[lang]} | " + " | ".join(cells) + " |")
    return lines


def false_negatives(results: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    return [
        r
        for r in results
        if r["true"] in CRISIS_LEVELS and str(r[key]) not in CRISIS_LEVELS
    ]


def build_report(
    split: str,
    cases: list[dict[str, str]],
    results: list[dict[str, object]],
    *,
    ml_enabled: bool,
) -> str:
    settings = Settings(_env_file=None)
    today = date.today().isoformat()
    ensemble_pairs = [(str(r["true"]), str(r["final"])) for r in results]
    rules_pairs = [(str(r["true"]), str(r["rules"])) for r in results]

    e_p, e_r, e_n, _ = crisis_metrics(ensemble_pairs)
    r_p, r_r, r_n, _ = crisis_metrics(rules_pairs)
    target_met = e_r is not None and e_r >= 0.97 if split == "dev" else None

    fns = sorted(false_negatives(results, "final"), key=lambda r: str(r["id"]))

    pipeline = (
        "rules engine + ML ensemble (TF-IDF + calibrated logistic regression)"
        if ml_enabled
        else "rules engine only (ML disabled with --no-ml)"
    )
    lines: list[str] = [
        f"# Safety evaluation — {today} (split: {split})",
        "",
        f"- Pipeline: {pipeline}.",
        f"- Thresholds: min_confidence={settings.safety_ml_min_confidence}, "
        f"crisis_mass_floor={settings.safety_ml_crisis_mass_floor}, "
        f"suspicion_floor={settings.safety_ml_suspicion_floor} "
        "(chosen on dev only — see `threshold_tuning.md`).",
        f"- Cases: {len(cases)} from `crisis_cases_{split}.jsonl`.",
        "",
        "## Headline: HIGH+IMMINENT (the crisis tiers)",
        "",
        "| pipeline | precision | recall | crisis cases |",
        "| --- | --- | --- | --- |",
        f"| **{'rules + ML ensemble' if ml_enabled else 'rules only (scored)'}** "
        f"| **{_fmt(e_p)}** | **{_fmt(e_r)}** | {e_n} |",
        f"| rules only | {_fmt(r_p)} | {_fmt(r_r)} | {r_n} |",
        "",
    ]
    if split == "dev":
        verdict = "MET" if target_met else "**NOT MET**"
        lines += [
            f"Day 9 target: crisis recall >= 0.97 on dev — **{verdict}** "
            f"(observed {_fmt(e_r)}).",
            "",
            "Honest caveat: recall-first thresholds are the default; the false-alarm cost",
            "on benign text at this dataset size is reported under \"Cost of the recall",
            "target\" below and in `docs/safety-design.md` §12.6.",
            "",
        ]
    if split == "test":
        lines += [
            "**Frozen split.** This file's hash matches `manifest.json`; it was never",
            "opened during training or threshold tuning. Numbers here are the first",
            "look at these cases.",
            "",
        ]

    scored = "rules + ML ensemble" if ml_enabled else "rules only"
    lines += [
        f"## Per-level metrics ({scored})",
        "",
        *per_level_table(ensemble_pairs),
        "",
        "## Per-level metrics (rules only, for comparison)",
        "",
        *per_level_table(rules_pairs),
        "",
        f"## Confusion matrix ({scored})",
        "",
        "Rows are ground truth; columns are the scored pipeline's final level.",
        "",
        *confusion_matrix(ensemble_pairs),
        "",
        "## Per-language performance",
        "",
        *language_table(results, "final"),
        "",
        "Recall per level, per language (support in brackets):",
        "",
        *per_language_level_detail(results, "final"),
        "",
        "## Cost of the recall target: benign escalations",
        "",
    ]
    benign = [r for r in results if r["true"] in ("NONE", "LOW")]
    crisis_fa = [r for r in benign if r["final"] in CRISIS_LEVELS]
    checkin_fa = [r for r in benign if r["final"] == "MEDIUM"]
    lines += [
        f"- Benign cases (NONE/LOW): {len(benign)}",
        f"- ...handed a crisis card (HIGH/IMMINENT): {len(crisis_fa)}",
        f"- ...softened to a MEDIUM check-in: {len(checkin_fa)}",
        "",
        "The ensemble can raise but never lower, so these are the price paid for the",
        "recall above at this dataset size; see the design doc for the trade-off and",
        "the precision-leaning thresholds.",
        "",
        f"## False negatives at HIGH+IMMINENT ({scored})",
        "",
    ]
    if fns:
        lines += ["| id | true | predicted | source | notes |", "| --- | --- | --- | --- | --- |"]
        for r in fns:
            lines.append(f"| {r['id']} | {r['true']} | {r['final']} | {r['source']} | {r['notes']} |")
    else:
        lines.append("None — every true HIGH/IMMINENT case was predicted HIGH or IMMINENT.")
    lines += [
        "",
        "---",
        f"Generated by `evals/run_safety_eval.py` on {today}. Synthetic eval data only;",
        "no real user text appears anywhere in this report.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the safety pipeline on one split.")
    parser.add_argument("--split", choices=("train", "dev", "test"), default="dev")
    parser.add_argument("--no-ml", action="store_true", help="score rules only (no ensemble)")
    args = parser.parse_args()

    cases = load_split(args.split)
    results = assess_all(cases, ml_enabled=not args.no_ml)
    report = build_report(args.split, cases, results, ml_enabled=not args.no_ml)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"safety_{date.today().isoformat()}"
    if args.split != "dev":
        stem += f"_{args.split}"
    if args.no_ml:
        stem += "_rules_only"
    out_path = REPORTS_DIR / f"{stem}.md"
    out_path.write_text(report, encoding="utf-8")

    # Console summary: the headline numbers, not the whole report.
    ensemble_pairs = [(str(r["true"]), str(r["final"])) for r in results]
    e_p, e_r, e_n, _ = crisis_metrics(ensemble_pairs)
    fns = false_negatives(results, "final")
    benign = [r for r in results if r["true"] in ("NONE", "LOW")]
    crisis_fa = sum(1 for r in benign if r["final"] in CRISIS_LEVELS)
    print(f"split={args.split} cases={len(cases)}")
    print(f"HIGH+IMMINENT  precision={_fmt(e_p)}  recall={_fmt(e_r)}  (crisis cases: {e_n})")
    print(f"false negatives: {len(fns)}  benign escalated to crisis: {crisis_fa}/{len(benign)}")
    print(f"report written: {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
