"""Run the safety evaluation: rules + ML ensemble against a labelled split.

Usage (from the repository root):

    .venv/bin/python evals/run_safety_eval.py --split dev
    .venv/bin/python evals/run_safety_eval.py --split test
    .venv/bin/python evals/run_safety_eval.py --split dev --compare evals/reports/safety_2026-10-11.md

What it computes, for the full ensemble and for the rules engine alone:

* precision / recall / F1 per level (NONE..IMMINENT), macro-averaged;
* the binary "crisis" view (HIGH+IMMINENT together) — the number the Day 9/24
  target is written against (>= 0.97 recall on dev);
* non-parametric percentile bootstrap 95% confidence intervals (seeded, B=2000)
  for headline, per-level, per-language, and per-category metrics;
* per-language and per-category recall and precision tables (plus the
  language × category diagnostic matrices);
* a 5x5 confusion matrix (rows = ground truth, columns = predicted);
* regression diff against the previous saved report (`safety_<prev>.md`);
* CI gate checks read from ``evals/thresholds.yaml`` — exits non-zero if
  HIGH+IMMINENT recall (or rules-only baseline recall / regression check)
  drops below the configured threshold;
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
import random
import re
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.core.config import Settings  # noqa: E402
from app.services.safety.ensemble import combine  # noqa: E402
from app.services.safety.ml_classifier import build_ml_classifier  # noqa: E402
from app.services.safety.rules import RuleEngine, build_engine  # noqa: E402

DATASET_DIR = REPO_ROOT / "evals" / "datasets"
REPORTS_DIR = REPO_ROOT / "evals" / "reports"
THRESHOLDS_PATH = REPO_ROOT / "evals" / "thresholds.yaml"

LEVEL_LABELS = ("NONE", "LOW", "MEDIUM", "HIGH", "IMMINENT")
CRISIS_LEVELS = frozenset({"HIGH", "IMMINENT"})
LANG_LABELS = {
    "en": "English",
    "hi": "Hindi (Devanagari)",
    "hi-Latn": "Hindi (romanised)",
    "bn": "Bengali",
}

CATEGORY_LABELS = {
    "suicidal_ideation": "Suicidal ideation",
    "intent_plan": "Intent / plan",
    "self_harm": "Self-harm",
    "severe_hopelessness": "Severe hopelessness",
    "acute_medical": "Acute medical",
    "abuse_disclosure": "Abuse disclosure",
    "harm_to_others": "Harm to others",
}


@dataclass(frozen=True)
class BootstrapConfig:
    resamples: int = 2000
    confidence_level: float = 0.95
    seed: int = 20261012


@dataclass(frozen=True)
class SafetyGateThresholds:
    high_imminent_recall_min: float = 0.97
    rules_high_imminent_recall_min: float = 0.330
    per_language_crisis_recall_min: float = 0.95
    disallow_recall_regression: bool = True


@dataclass(frozen=True)
class GateCheck:
    name: str
    threshold: str
    observed: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class GateVerdict:
    passed: bool
    checks: tuple[GateCheck, ...]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_thresholds(path: Path = THRESHOLDS_PATH) -> tuple[SafetyGateThresholds, BootstrapConfig]:
    """Load safety gate thresholds and bootstrap config from YAML."""
    if not path.is_file():
        return SafetyGateThresholds(), BootstrapConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"thresholds file must be a YAML mapping: {path}")
    safety_raw = raw.get("safety", {}) or {}
    boot_raw = raw.get("bootstrap", {}) or {}
    thresholds = SafetyGateThresholds(
        high_imminent_recall_min=float(safety_raw.get("high_imminent_recall_min", 0.97)),
        rules_high_imminent_recall_min=float(
            safety_raw.get("rules_high_imminent_recall_min", 0.330)
        ),
        per_language_crisis_recall_min=float(
            safety_raw.get("per_language_crisis_recall_min", 0.95)
        ),
        disallow_recall_regression=bool(safety_raw.get("disallow_recall_regression", True)),
    )
    boot = BootstrapConfig(
        resamples=int(boot_raw.get("resamples", 2000)),
        confidence_level=float(boot_raw.get("confidence_level", 0.95)),
        seed=int(boot_raw.get("seed", 20261012)),
    )
    return thresholds, boot


def load_split(name: str) -> list[dict[str, str]]:
    """Load one split; enforce the frozen-test contract on the way in."""
    path = DATASET_DIR / f"crisis_cases_{name}.jsonl"
    manifest_path = DATASET_DIR / "manifest.json"
    if not path.is_file():
        raise SystemExit(f"split file missing: {path}")
    cases = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

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


def assess_all(
    cases: list[dict[str, str]],
    *,
    ml_enabled: bool,
    engine: RuleEngine | None = None,
) -> list[dict[str, object]]:
    """Run every case through the rules engine and (optionally) the ensemble."""
    active_engine = engine if engine is not None else build_engine()
    classifier = build_ml_classifier() if ml_enabled else None
    settings = Settings(_env_file=None)

    results: list[dict[str, object]] = []
    for case in cases:
        assessment = active_engine.assess(case["text"], language=case["lang"])
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
                "category": case.get("category", "unknown"),
            }
        )
    return results


def _precision_recall(
    pairs: Sequence[tuple[str, str]], label: str
) -> tuple[float | None, float | None, int, int]:
    """Precision and recall for one label, plus the raw counts."""
    tp = sum(1 for true, pred in pairs if true == label and pred == label)
    fp = sum(1 for true, pred in pairs if true != label and pred == label)
    fn = sum(1 for true, pred in pairs if true == label and pred != label)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return precision, recall, tp + fn, tp + fp


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or (precision + recall) == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _fmt_ci(ci: tuple[float | None, float | None]) -> str:
    low, high = ci
    if low is None or high is None:
        return "—"
    return f"[{low:.3f}, {high:.3f}]"


def bootstrap_ci(
    items: Sequence[Any],
    metric_fn: Callable[[Sequence[Any]], float | None],
    *,
    boot: BootstrapConfig = BootstrapConfig(),
) -> tuple[float | None, float | None]:
    """Deterministic percentile bootstrap confidence interval over ``items``."""
    if not items or boot.resamples <= 0:
        return None, None
    rng = random.Random(boot.seed)
    n = len(items)
    estimates: list[float] = []
    for _ in range(boot.resamples):
        sample = [items[rng.randrange(n)] for _ in range(n)]
        val = metric_fn(sample)
        if val is not None:
            estimates.append(val)
    if not estimates:
        return None, None
    estimates.sort()
    alpha = (1.0 - boot.confidence_level) / 2.0
    lo_idx = max(0, min(len(estimates) - 1, int(alpha * len(estimates))))
    hi_idx = max(0, min(len(estimates) - 1, int((1.0 - alpha) * len(estimates)) - 1))
    return estimates[lo_idx], estimates[hi_idx]


def crisis_metrics(
    pairs: Sequence[tuple[str, str]],
) -> tuple[float | None, float | None, int, int]:
    """Binary HIGH+IMMINENT precision/recall — the Day 9/24 target number."""
    binary = [
        (
            "CRISIS" if true in CRISIS_LEVELS else "OK",
            "CRISIS" if pred in CRISIS_LEVELS else "OK",
        )
        for true, pred in pairs
    ]
    precision, recall, support, predicted = _precision_recall(binary, "CRISIS")
    return precision, recall, support, predicted


def per_level_table(
    pairs: list[tuple[str, str]],
    *,
    boot: BootstrapConfig = BootstrapConfig(),
) -> list[str]:
    lines = [
        "| level | support | precision | recall | F1 | recall 95% CI |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for label in LEVEL_LABELS:
        precision, recall, support, _ = _precision_recall(pairs, label)
        f1 = _f1(precision, recall)
        rec_ci = bootstrap_ci(
            pairs,
            lambda sample, lbl=label: _precision_recall(sample, lbl)[1],
            boot=boot,
        )
        lines.append(
            f"| {label} | {support} | {_fmt(precision)} | {_fmt(recall)} "
            f"| {_fmt(f1)} | {_fmt_ci(rec_ci)} |"
        )
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


def language_table(
    results: list[dict[str, object]],
    key: str,
    *,
    boot: BootstrapConfig = BootstrapConfig(),
) -> list[str]:
    lines = [
        "| language | cases | crisis recall | crisis recall 95% CI | crisis precision | macro recall |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for lang in ("en", "hi", "hi-Latn", "bn"):
        subset = [r for r in results if r["lang"] == lang]
        if not subset:
            continue
        pairs = [(str(r["true"]), str(r[key])) for r in subset]
        crisis_p, crisis_r, crisis_n, _ = crisis_metrics(pairs)
        rec_ci = bootstrap_ci(
            pairs,
            lambda sample: crisis_metrics(sample)[1],
            boot=boot,
        )
        recalls = [
            _precision_recall(pairs, label)[1]
            for label in LEVEL_LABELS
            if any(t == label for t, _ in pairs)
        ]
        macro = sum(v for v in recalls if v is not None) / len(recalls) if recalls else None
        lines.append(
            f"| {LANG_LABELS[lang]} | {len(subset)} | {_fmt(crisis_r)} ({crisis_n}) "
            f"| {_fmt_ci(rec_ci)} | {_fmt(crisis_p)} | {_fmt(macro)} |"
        )
    return lines


def per_language_level_detail(results: list[dict[str, object]], key: str) -> list[str]:
    """Per-language recall per level, so language gaps are visible per tier."""
    lines = [
        "| language | " + " | ".join(f"{label} recall" for label in LEVEL_LABELS) + " |",
        "| --- |" + " --- |" * len(LEVEL_LABELS),
    ]
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


def per_category_table(
    results: list[dict[str, object]],
    key: str,
    *,
    boot: BootstrapConfig = BootstrapConfig(),
) -> list[str]:
    """Overall per-category metrics (crisis recall, exact-level recall, and 95% CIs)."""
    lines = [
        "| category | total cases | crisis cases | crisis precision | crisis recall | crisis recall 95% CI | exact-level recall |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for category, label in CATEGORY_LABELS.items():
        cat_rows = [r for r in results if r["category"] == category]
        if not cat_rows:
            continue
        pairs = [(str(r["true"]), str(r[key])) for r in cat_rows]
        c_p, c_r, c_n, _ = crisis_metrics(pairs)
        c_r_ci = (
            bootstrap_ci(pairs, lambda sample: crisis_metrics(sample)[1], boot=boot)
            if c_n > 0
            else (None, None)
        )
        exact_hits = sum(1 for t, p in pairs if t == p)
        exact_r = exact_hits / len(pairs) if pairs else None
        lines.append(
            f"| {label} | {len(cat_rows)} | {c_n} | {_fmt(c_p)} | {_fmt(c_r)} "
            f"| {_fmt_ci(c_r_ci)} | {_fmt(exact_r)} |"
        )
    return lines


def false_negatives(results: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    return [
        r for r in results if r["true"] in CRISIS_LEVELS and str(r[key]) not in CRISIS_LEVELS
    ]


def language_category_table(results: list[dict[str, object]], key: str) -> list[str]:
    """Crisis recall per category per language (Day 23)."""
    lines = [
        "| language | " + " | ".join(CATEGORY_LABELS.values()) + " |",
        "| --- |" + " --- |" * len(CATEGORY_LABELS),
    ]
    for lang in ("en", "hi", "hi-Latn", "bn"):
        subset = [r for r in results if r["lang"] == lang]
        if not subset:
            continue
        cells = []
        for category in CATEGORY_LABELS:
            cases = [
                r for r in subset if r["category"] == category and r["true"] in CRISIS_LEVELS
            ]
            if not cases:
                cells.append("—")
                continue
            hits = sum(1 for r in cases if str(r[key]) in CRISIS_LEVELS)
            cells.append(f"{hits / len(cases):.3f} ({len(cases)})")
        lines.append(f"| {LANG_LABELS[lang]} | " + " | ".join(cells) + " |")
    return lines


def language_category_level_table(results: list[dict[str, object]], key: str) -> list[str]:
    """Exact-level recall per category per language — the diagnostic view."""
    lines = [
        "| language | " + " | ".join(CATEGORY_LABELS.values()) + " |",
        "| --- |" + " --- |" * len(CATEGORY_LABELS),
    ]
    for lang in ("en", "hi", "hi-Latn", "bn"):
        subset = [r for r in results if r["lang"] == lang]
        if not subset:
            continue
        cells = []
        for category in CATEGORY_LABELS:
            cases = [r for r in subset if r["category"] == category]
            if not cases:
                cells.append("—")
                continue
            hits = sum(1 for r in cases if str(r[key]) == r["true"])
            cells.append(f"{hits / len(cases):.3f} ({len(cases)})")
        lines.append(f"| {LANG_LABELS[lang]} | " + " | ".join(cells) + " |")
    return lines


def lowest_category_per_language(results: list[dict[str, object]], key: str) -> list[str]:
    """Name each language's weakest category so a reviewer knows where to look."""
    lines = []
    for lang in ("en", "hi", "hi-Latn", "bn"):
        subset = [r for r in results if r["lang"] == lang]
        if not subset:
            continue
        observed: list[tuple[float, float, str, int]] = []
        for category in CATEGORY_LABELS:
            cases = [r for r in subset if r["category"] == category]
            if not cases:
                continue
            level_recall = sum(1 for r in cases if str(r[key]) == r["true"]) / len(cases)
            crisis_cases = [r for r in cases if r["true"] in CRISIS_LEVELS]
            crisis_recall = (
                sum(1 for r in crisis_cases if str(r[key]) in CRISIS_LEVELS) / len(crisis_cases)
                if crisis_cases
                else 1.0
            )
            observed.append((level_recall, crisis_recall, category, len(cases)))
        if not observed:
            continue
        observed.sort(key=lambda item: item[0])
        level_recall, crisis_recall, category, support = observed[0]
        lines.append(
            f"- {LANG_LABELS[lang]}: {CATEGORY_LABELS[category]} — exact-level recall "
            f"{_fmt(level_recall)} over {support} case{'s' if support != 1 else ''} "
            f"(crisis recall within it: {_fmt(crisis_recall)})"
        )
    return lines or ["- (no categorised cases present)"]


# --------------------------------------------------------------------------- #
# Regression diff vs previous report                                           #
# --------------------------------------------------------------------------- #


def parse_report_metrics(markdown_text: str) -> dict[str, float]:
    """Extract headline and per-language metrics from a rendered safety report."""
    metrics: dict[str, float] = {}
    # Headline table rows:
    # | **rules + ML ensemble** | **0.543** | **1.000** | 51 | ...
    # | rules only | 0.810 | 0.333 | 51 | ...
    ensemble_match = re.search(
        r"\|\s*\*\*(?:rules \+ ML ensemble|rules only \(scored\))\*\*\s*\|\s*\*\*([0-9.]+)\*\*\s*\|\s*\*\*([0-9.]+)\*\*",
        markdown_text,
    )
    if ensemble_match:
        metrics["ensemble_precision"] = float(ensemble_match.group(1))
        metrics["ensemble_recall"] = float(ensemble_match.group(2))

    rules_match = re.search(
        r"\|\s*rules only\s*\|\s*([0-9.]+)\s*\|\s*([0-9.]+)\s*\|",
        markdown_text,
    )
    if rules_match:
        metrics["rules_precision"] = float(rules_match.group(1))
        metrics["rules_recall"] = float(rules_match.group(2))

    for lang_code, lang_name in LANG_LABELS.items():
        escaped = re.escape(lang_name)
        lang_match = re.search(
            rf"\|\s*{escaped}\s*\|\s*\d+\s*\|\s*([0-9.]+)\s*\(\d+\)",
            markdown_text,
        )
        if lang_match:
            metrics[f"lang_crisis_recall.{lang_code}"] = float(lang_match.group(1))

    fa_match = re.search(r"handed a crisis card \(HIGH/IMMINENT\):\s*(\d+)", markdown_text)
    if fa_match:
        metrics["benign_crisis_escalations"] = float(fa_match.group(1))

    return metrics


def current_metrics_snapshot(results: list[dict[str, object]]) -> dict[str, float]:
    """Compute the same metric dictionary directly from ``results``."""
    ensemble_pairs = [(str(r["true"]), str(r["final"])) for r in results]
    rules_pairs = [(str(r["true"]), str(r["rules"])) for r in results]
    e_p, e_r, _, _ = crisis_metrics(ensemble_pairs)
    r_p, r_r, _, _ = crisis_metrics(rules_pairs)
    snap: dict[str, float] = {}
    if e_p is not None:
        snap["ensemble_precision"] = round(e_p, 3)
    if e_r is not None:
        snap["ensemble_recall"] = round(e_r, 3)
    if r_p is not None:
        snap["rules_precision"] = round(r_p, 3)
    if r_r is not None:
        snap["rules_recall"] = round(r_r, 3)

    for lang in ("en", "hi", "hi-Latn", "bn"):
        subset = [(str(r["true"]), str(r["final"])) for r in results if r["lang"] == lang]
        if subset:
            _, c_r, _, _ = crisis_metrics(subset)
            if c_r is not None:
                snap[f"lang_crisis_recall.{lang}"] = round(c_r, 3)

    benign = [r for r in results if r["true"] in ("NONE", "LOW")]
    snap["benign_crisis_escalations"] = float(
        sum(1 for r in benign if r["final"] in CRISIS_LEVELS)
    )
    return snap


def find_previous_report(
    split: str,
    *,
    ml_enabled: bool,
    compare_path: Path | None = None,
    current_out_path: Path | None = None,
    current_snap: dict[str, float] | None = None,
) -> Path | None:
    """Resolve the previous report to diff against.

    If ``compare_path`` is supplied explicitly, use it. Otherwise inspect saved
    ``safety_*.md`` reports in ``evals/reports/`` matching the same split and
    mode:
    * if ``current_out_path`` already exists on disk AND its metrics differ
      from ``current_snap`` (e.g. a local code edit on the same day), compare
      against ``current_out_path`` so the intra-day regression is caught;
    * otherwise pick the most recent earlier-dated report for the same split
      (falling back to ``current_out_path`` if it is the only one).
    """
    if compare_path is not None:
        return compare_path if compare_path.is_file() else None
    if not REPORTS_DIR.is_dir():
        return None

    pattern = re.compile(r"^safety_(\d{4}-\d{2}-\d{2})(?:_(train|dev|test))?(?:_(rules_only))?\.md$")
    candidates: list[tuple[str, Path]] = []
    for entry in sorted(REPORTS_DIR.glob("safety_*.md")):
        m = pattern.match(entry.name)
        if not m:
            continue
        rep_date, rep_split, rep_rules = m.groups()
        effective_split = rep_split or "dev"
        effective_ml = rep_rules != "rules_only"
        if effective_split == split and effective_ml == ml_enabled:
            candidates.append((rep_date, entry))

    if not candidates:
        return None

    if current_out_path is not None and current_out_path.is_file() and current_snap is not None:
        existing_metrics = parse_report_metrics(current_out_path.read_text(encoding="utf-8"))
        # If today's committed report has different recall/precision than this
        # run, diff against it so same-day regressions surface immediately.
        for key in ("ensemble_recall", "rules_recall", "ensemble_precision", "rules_precision"):
            if (
                key in existing_metrics
                and key in current_snap
                and abs(existing_metrics[key] - current_snap[key]) >= 1e-3
            ):
                return current_out_path

    prior = [path for _, path in candidates if path != current_out_path]
    if prior:
        return prior[-1]
    return candidates[-1][1]


def compute_regression_diff(
    previous_metrics: dict[str, float],
    current_metrics: dict[str, float],
) -> list[dict[str, object]]:
    """Compare current metrics against previous report metrics."""
    rows: list[dict[str, object]] = []
    spec = (
        ("ensemble_recall", "HIGH+IMMINENT recall (active)", "higher_is_better"),
        ("ensemble_precision", "HIGH+IMMINENT precision (active)", "higher_is_better"),
        ("rules_recall", "HIGH+IMMINENT recall (rules only)", "higher_is_better"),
        ("rules_precision", "HIGH+IMMINENT precision (rules only)", "higher_is_better"),
        ("lang_crisis_recall.en", "Crisis recall — English", "higher_is_better"),
        ("lang_crisis_recall.hi", "Crisis recall — Hindi (Devanagari)", "higher_is_better"),
        ("lang_crisis_recall.hi-Latn", "Crisis recall — Hindi (romanised)", "higher_is_better"),
        ("lang_crisis_recall.bn", "Crisis recall — Bengali", "higher_is_better"),
        ("benign_crisis_escalations", "Benign crisis escalations (count)", "lower_is_better"),
    )
    for key, label, direction in spec:
        if key not in previous_metrics or key not in current_metrics:
            continue
        prev = previous_metrics[key]
        curr = current_metrics[key]
        delta = curr - prev
        if abs(delta) < 1e-4:
            status = "UNCHANGED"
        elif (direction == "higher_is_better" and delta > 0) or (
            direction == "lower_is_better" and delta < 0
        ):
            status = "IMPROVED"
        else:
            status = "REGRESSED"
        rows.append(
            {
                "key": key,
                "label": label,
                "previous": prev,
                "current": curr,
                "delta": delta,
                "status": status,
                "is_count": key == "benign_crisis_escalations",
            }
        )
    return rows


def regression_diff_table(
    diff_rows: list[dict[str, object]],
    previous_path: Path | None,
) -> list[str]:
    if previous_path is None or not diff_rows:
        return ["No previous report found for this split/mode — baseline established."]
    rel_name = (
        previous_path.relative_to(REPO_ROOT)
        if previous_path.is_relative_to(REPO_ROOT)
        else previous_path.name
    )
    lines = [
        f"Compared against `{rel_name}`:",
        "",
        "| metric | previous | current | delta | status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in diff_rows:
        is_count = bool(row["is_count"])
        prev = float(str(row["previous"]))
        curr = float(str(row["current"]))
        delta = float(str(row["delta"]))
        if is_count:
            prev_s = f"{int(round(prev))}"
            curr_s = f"{int(round(curr))}"
            delta_s = f"{int(round(delta)):+d}"
        else:
            prev_s = f"{prev:.3f}"
            curr_s = f"{curr:.3f}"
            delta_s = f"{delta:+.3f}"
        lines.append(
            f"| {row['label']} | {prev_s} | {curr_s} | {delta_s} | **{row['status']}** |"
        )
    return lines


# --------------------------------------------------------------------------- #
# CI gate evaluation                                                           #
# --------------------------------------------------------------------------- #


def evaluate_gate(
    results: list[dict[str, object]],
    thresholds: SafetyGateThresholds,
    diff_rows: list[dict[str, object]],
) -> GateVerdict:
    """Check observed safety metrics against ``evals/thresholds.yaml``."""
    ensemble_pairs = [(str(r["true"]), str(r["final"])) for r in results]
    rules_pairs = [(str(r["true"]), str(r["rules"])) for r in results]
    _, e_r, _, _ = crisis_metrics(ensemble_pairs)
    _, r_r, _, _ = crisis_metrics(rules_pairs)

    checks: list[GateCheck] = []

    # 1. Active pipeline HIGH+IMMINENT recall floor
    e_r_val = e_r if e_r is not None else 0.0
    checks.append(
        GateCheck(
            name="HIGH+IMMINENT recall (active pipeline)",
            threshold=f">= {thresholds.high_imminent_recall_min:.3f}",
            observed=_fmt(e_r),
            passed=e_r_val + 1e-9 >= thresholds.high_imminent_recall_min,
            detail="Primary crisis recall gate from evals/thresholds.yaml",
        )
    )

    # 2. Deterministic rules-engine HIGH+IMMINENT recall floor
    r_r_val = r_r if r_r is not None else 0.0
    checks.append(
        GateCheck(
            name="HIGH+IMMINENT recall (rules-only baseline)",
            threshold=f">= {thresholds.rules_high_imminent_recall_min:.3f}",
            observed=_fmt(r_r),
            passed=r_r_val + 1e-9 >= thresholds.rules_high_imminent_recall_min,
            detail="Catches weakened/removed HIGH patterns even when ML backstop is enabled",
        )
    )

    # 3. Per-language crisis recall floor
    lang_failures: list[str] = []
    worst_lang_r = 1.0
    for lang in ("en", "hi", "hi-Latn", "bn"):
        subset = [(str(r["true"]), str(r["final"])) for r in results if r["lang"] == lang]
        if not subset:
            continue
        _, l_r, l_n, _ = crisis_metrics(subset)
        if l_n > 0 and l_r is not None:
            worst_lang_r = min(worst_lang_r, l_r)
            if l_r + 1e-9 < thresholds.per_language_crisis_recall_min:
                lang_failures.append(f"{lang}={l_r:.3f}")
    checks.append(
        GateCheck(
            name="Per-language HIGH+IMMINENT recall (min across en/hi/hi-Latn/bn)",
            threshold=f">= {thresholds.per_language_crisis_recall_min:.3f}",
            observed=f"{worst_lang_r:.3f}",
            passed=not lang_failures,
            detail="All languages above floor"
            if not lang_failures
            else f"Below floor: {', '.join(lang_failures)}",
        )
    )

    # 4. Regression guard vs previous saved report
    if thresholds.disallow_recall_regression and diff_rows:
        regressed = [
            str(r["label"])
            for r in diff_rows
            if r["key"] in ("ensemble_recall", "rules_recall") and r["status"] == "REGRESSED"
        ]
        checks.append(
            GateCheck(
                name="No recall regression vs previous report",
                threshold="delta >= 0.000",
                observed="0 regressions" if not regressed else f"{len(regressed)} regressed",
                passed=not regressed,
                detail="No recall drop" if not regressed else f"Regressed: {', '.join(regressed)}",
            )
        )

    passed = all(c.passed for c in checks)
    return GateVerdict(passed=passed, checks=tuple(checks))


def gate_table(verdict: GateVerdict) -> list[str]:
    lines = [
        f"Overall CI gate verdict: **{'PASS' if verdict.passed else 'FAIL'}** (configured in `evals/thresholds.yaml`)",
        "",
        "| check | threshold | observed | verdict | notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in verdict.checks:
        status = "PASS" if check.passed else "**FAIL**"
        lines.append(
            f"| {check.name} | `{check.threshold}` | `{check.observed}` | {status} | {check.detail} |"
        )
    return lines


def build_report(
    split: str,
    cases: list[dict[str, str]],
    results: list[dict[str, object]],
    *,
    ml_enabled: bool,
    thresholds: SafetyGateThresholds | None = None,
    boot: BootstrapConfig | None = None,
    previous_path: Path | None = None,
    diff_rows: list[dict[str, object]] | None = None,
    gate_verdict: GateVerdict | None = None,
) -> str:
    settings = Settings(_env_file=None)
    today = date.today().isoformat()
    active_thresholds = thresholds or SafetyGateThresholds()
    active_boot = boot or BootstrapConfig()

    ensemble_pairs = [(str(r["true"]), str(r["final"])) for r in results]
    rules_pairs = [(str(r["true"]), str(r["rules"])) for r in results]

    e_p, e_r, e_n, _ = crisis_metrics(ensemble_pairs)
    r_p, r_r, r_n, _ = crisis_metrics(rules_pairs)

    e_p_ci = bootstrap_ci(
        ensemble_pairs, lambda sample: crisis_metrics(sample)[0], boot=active_boot
    )
    e_r_ci = bootstrap_ci(
        ensemble_pairs, lambda sample: crisis_metrics(sample)[1], boot=active_boot
    )
    r_p_ci = bootstrap_ci(
        rules_pairs, lambda sample: crisis_metrics(sample)[0], boot=active_boot
    )
    r_r_ci = bootstrap_ci(
        rules_pairs, lambda sample: crisis_metrics(sample)[1], boot=active_boot
    )

    target_met = (
        e_r is not None and e_r >= active_thresholds.high_imminent_recall_min
        if split == "dev"
        else None
    )

    if diff_rows is None:
        prev_metrics = (
            parse_report_metrics(previous_path.read_text(encoding="utf-8"))
            if previous_path is not None and previous_path.is_file()
            else {}
        )
        diff_rows = compute_regression_diff(prev_metrics, current_metrics_snapshot(results))

    if gate_verdict is None:
        gate_verdict = evaluate_gate(results, active_thresholds, diff_rows)

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
        f"- Bootstrap: B={active_boot.resamples} resamples, "
        f"{int(active_boot.confidence_level * 100)}% percentile CIs (seed={active_boot.seed}).",
        f"- Cases: {len(cases)} from `crisis_cases_{split}.jsonl`.",
        "",
        "## Headline: HIGH+IMMINENT (the crisis tiers)",
        "",
        "| pipeline | precision | recall | crisis cases | precision 95% CI | recall 95% CI |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| **{'rules + ML ensemble' if ml_enabled else 'rules only (scored)'}** "
        f"| **{_fmt(e_p)}** | **{_fmt(e_r)}** | {e_n} | {_fmt_ci(e_p_ci)} | {_fmt_ci(e_r_ci)} |",
        f"| rules only | {_fmt(r_p)} | {_fmt(r_r)} | {r_n} | {_fmt_ci(r_p_ci)} | {_fmt_ci(r_r_ci)} |",
        "",
    ]
    if split == "dev":
        verdict = "MET" if target_met else "**NOT MET**"
        lines += [
            f"Day 9 target: crisis recall >= {active_thresholds.high_imminent_recall_min:.2f} on dev — **{verdict}** "
            f"(observed {_fmt(e_r)}, 95% CI {_fmt_ci(e_r_ci)}).",
            "",
            "Honest caveat: recall-first thresholds are the default; the false-alarm cost",
            'on benign text at this dataset size is reported under "Cost of the recall',
            'target" below and in `docs/safety-design.md` §12.6.',
            "",
        ]
    if split == "test":
        lines += [
            "**Frozen split.** This file's hash matches `manifest.json`; it was never",
            "opened during training or threshold tuning. Numbers here are the held-out",
            "evaluation of these cases.",
            "",
        ]

    scored = "rules + ML ensemble" if ml_enabled else "rules only"
    lines += [
        "## CI gate status",
        "",
        *gate_table(gate_verdict),
        "",
        "## Regression diff vs previous report",
        "",
        *regression_diff_table(diff_rows, previous_path),
        "",
        f"## Per-level metrics ({scored})",
        "",
        *per_level_table(ensemble_pairs, boot=active_boot),
        "",
        "## Per-level metrics (rules only, for comparison)",
        "",
        *per_level_table(rules_pairs, boot=active_boot),
        "",
        f"## Per-category metrics ({scored})",
        "",
        *per_category_table(results, "final", boot=active_boot),
        "",
        f"## Confusion matrix ({scored})",
        "",
        "Rows are ground truth; columns are the scored pipeline's final level.",
        "",
        *confusion_matrix(ensemble_pairs),
        "",
        "## Per-language performance",
        "",
        *language_table(results, "final", boot=active_boot),
        "",
        "Recall per level, per language (support in brackets):",
        "",
        *per_language_level_detail(results, "final"),
        "",
        "Crisis recall per category per language (crisis cases in brackets):",
        "",
        *language_category_table(results, "final"),
        "",
        "Exact-level recall per category per language (all cases in brackets):",
        "",
        *language_category_level_table(results, "final"),
        "",
        "Lowest-performing category per language (by exact-level recall):",
        "",
        *lowest_category_per_language(results, "final"),
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
        lines += [
            "| id | true | predicted | source | notes |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in fns:
            lines.append(
                f"| {r['id']} | {r['true']} | {r['final']} | {r['source']} | {r['notes']} |"
            )
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


def build_json_summary(
    split: str,
    cases: list[dict[str, str]],
    results: list[dict[str, object]],
    *,
    ml_enabled: bool,
    thresholds: SafetyGateThresholds,
    boot: BootstrapConfig,
    previous_path: Path | None,
    diff_rows: list[dict[str, object]],
    gate_verdict: GateVerdict,
) -> dict[str, object]:
    """Structured summary for `make eval` HTML/Markdown aggregation."""
    ensemble_pairs = [(str(r["true"]), str(r["final"])) for r in results]
    rules_pairs = [(str(r["true"]), str(r["rules"])) for r in results]
    e_p, e_r, e_n, _ = crisis_metrics(ensemble_pairs)
    r_p, r_r, r_n, _ = crisis_metrics(rules_pairs)
    e_p_ci = bootstrap_ci(ensemble_pairs, lambda s: crisis_metrics(s)[0], boot=boot)
    e_r_ci = bootstrap_ci(ensemble_pairs, lambda s: crisis_metrics(s)[1], boot=boot)
    r_p_ci = bootstrap_ci(rules_pairs, lambda s: crisis_metrics(s)[0], boot=boot)
    r_r_ci = bootstrap_ci(rules_pairs, lambda s: crisis_metrics(s)[1], boot=boot)

    benign = [r for r in results if r["true"] in ("NONE", "LOW")]
    crisis_fa = sum(1 for r in benign if r["final"] in CRISIS_LEVELS)
    checkin_fa = sum(1 for r in benign if r["final"] == "MEDIUM")
    fns = false_negatives(results, "final")

    per_lang: dict[str, dict[str, object]] = {}
    for lang in ("en", "hi", "hi-Latn", "bn"):
        subset = [(str(r["true"]), str(r["final"])) for r in results if r["lang"] == lang]
        if not subset:
            continue
        lp, lr, ln, _ = crisis_metrics(subset)
        lr_ci = bootstrap_ci(subset, lambda s: crisis_metrics(s)[1], boot=boot)
        per_lang[lang] = {
            "label": LANG_LABELS[lang],
            "cases": len(subset),
            "crisis_cases": ln,
            "crisis_precision": lp,
            "crisis_recall": lr,
            "crisis_recall_ci": list(lr_ci),
        }

    return {
        "suite": "safety",
        "date": date.today().isoformat(),
        "split": split,
        "ml_enabled": ml_enabled,
        "cases": len(cases),
        "crisis_cases": e_n,
        "ensemble": {
            "precision": e_p,
            "recall": e_r,
            "precision_ci": list(e_p_ci),
            "recall_ci": list(e_r_ci),
        },
        "rules_only": {
            "precision": r_p,
            "recall": r_r,
            "crisis_cases": r_n,
            "precision_ci": list(r_p_ci),
            "recall_ci": list(r_r_ci),
        },
        "per_language": per_lang,
        "false_negatives": [str(r["id"]) for r in fns],
        "benign_cases": len(benign),
        "benign_crisis_escalations": crisis_fa,
        "benign_checkin_escalations": checkin_fa,
        "previous_report": str(previous_path.relative_to(REPO_ROOT))
        if previous_path is not None and previous_path.is_relative_to(REPO_ROOT)
        else (str(previous_path) if previous_path is not None else None),
        "regression_diff": diff_rows,
        "gate": {
            "passed": gate_verdict.passed,
            "thresholds": {
                "high_imminent_recall_min": thresholds.high_imminent_recall_min,
                "rules_high_imminent_recall_min": thresholds.rules_high_imminent_recall_min,
                "per_language_crisis_recall_min": thresholds.per_language_crisis_recall_min,
                "disallow_recall_regression": thresholds.disallow_recall_regression,
            },
            "checks": [
                {
                    "name": c.name,
                    "threshold": c.threshold,
                    "observed": c.observed,
                    "passed": c.passed,
                    "detail": c.detail,
                }
                for c in gate_verdict.checks
            ],
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the safety pipeline on one split.")
    parser.add_argument("--split", choices=("train", "dev", "test"), default="dev")
    parser.add_argument("--no-ml", action="store_true", help="score rules only (no ensemble)")
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=THRESHOLDS_PATH,
        help="path to thresholds.yaml (default: evals/thresholds.yaml)",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        default=None,
        help="previous safety report markdown file to diff against",
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="report gate failures without exiting non-zero",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output markdown path (default: evals/reports/safety_<today>.md)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="optional path to write machine-readable JSON summary",
    )
    args = parser.parse_args(argv)

    thresholds, boot = load_thresholds(args.thresholds)
    cases = load_split(args.split)
    ml_enabled = not args.no_ml
    results = assess_all(cases, ml_enabled=ml_enabled)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"safety_{date.today().isoformat()}"
    if args.split != "dev":
        stem += f"_{args.split}"
    if args.no_ml:
        stem += "_rules_only"
    default_out_path = REPORTS_DIR / f"{stem}.md"
    out_path = args.out or default_out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    curr_snap = current_metrics_snapshot(results)
    prev_path = find_previous_report(
        args.split,
        ml_enabled=ml_enabled,
        compare_path=args.compare,
        current_out_path=default_out_path,
        current_snap=curr_snap,
    )
    prev_metrics = (
        parse_report_metrics(prev_path.read_text(encoding="utf-8"))
        if prev_path is not None and prev_path.is_file()
        else {}
    )
    diff_rows = compute_regression_diff(prev_metrics, curr_snap)
    gate_verdict = evaluate_gate(results, thresholds, diff_rows)

    report = build_report(
        args.split,
        cases,
        results,
        ml_enabled=ml_enabled,
        thresholds=thresholds,
        boot=boot,
        previous_path=prev_path,
        diff_rows=diff_rows,
        gate_verdict=gate_verdict,
    )
    out_path.write_text(report, encoding="utf-8")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        summary_payload = build_json_summary(
            args.split,
            cases,
            results,
            ml_enabled=ml_enabled,
            thresholds=thresholds,
            boot=boot,
            previous_path=prev_path,
            diff_rows=diff_rows,
            gate_verdict=gate_verdict,
        )
        args.json_out.write_text(
            json.dumps(summary_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # Console summary: headline numbers, 95% CIs, regression diff, and CI gate status.
    ensemble_pairs = [(str(r["true"]), str(r["final"])) for r in results]
    rules_pairs = [(str(r["true"]), str(r["rules"])) for r in results]
    e_p, e_r, e_n, _ = crisis_metrics(ensemble_pairs)
    r_p, r_r, _, _ = crisis_metrics(rules_pairs)
    e_p_ci = bootstrap_ci(ensemble_pairs, lambda s: crisis_metrics(s)[0], boot=boot)
    e_r_ci = bootstrap_ci(ensemble_pairs, lambda s: crisis_metrics(s)[1], boot=boot)
    r_r_ci = bootstrap_ci(rules_pairs, lambda s: crisis_metrics(s)[1], boot=boot)
    fns = false_negatives(results, "final")
    benign = [r for r in results if r["true"] in ("NONE", "LOW")]
    crisis_fa = sum(1 for r in benign if r["final"] in CRISIS_LEVELS)

    print(f"split={args.split} cases={len(cases)} (bootstrap B={boot.resamples}, seed={boot.seed})")
    print(
        f"HIGH+IMMINENT (active)      precision={_fmt(e_p)} {_fmt_ci(e_p_ci)}  "
        f"recall={_fmt(e_r)} {_fmt_ci(e_r_ci)}  (crisis cases: {e_n})"
    )
    print(
        f"HIGH+IMMINENT (rules only)  precision={_fmt(r_p)}  "
        f"recall={_fmt(r_r)} {_fmt_ci(r_r_ci)}"
    )
    print(f"false negatives: {len(fns)}  benign escalated to crisis: {crisis_fa}/{len(benign)}")
    if prev_path is not None and diff_rows:
        rel_prev = (
            prev_path.relative_to(REPO_ROOT)
            if prev_path.is_relative_to(REPO_ROOT)
            else prev_path.name
        )
        reg_count = sum(1 for r in diff_rows if r["status"] == "REGRESSED")
        imp_count = sum(1 for r in diff_rows if r["status"] == "IMPROVED")
        print(
            f"regression diff vs {rel_prev}: {reg_count} regressed, {imp_count} improved, "
            f"{len(diff_rows) - reg_count - imp_count} unchanged"
        )
    print(f"CI gate: {'PASS' if gate_verdict.passed else 'FAIL'}")
    for check in gate_verdict.checks:
        if not check.passed:
            print(f"  [FAIL] {check.name}: observed {check.observed}, required {check.threshold} ({check.detail})")
    print(
        f"report written: {out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}"
    )

    if not gate_verdict.passed and not args.no_gate:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
