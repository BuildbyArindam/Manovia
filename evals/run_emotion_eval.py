"""Evaluate the Day 12 emotion analyzer on a hand-labelled multilingual set (Day 24).

Usage::

    python evals/run_emotion_eval.py
    python evals/run_emotion_eval.py --analyzer chain --out evals/reports/emotion_2026-10-12.md

Scores ``evals/datasets/emotion_cases.jsonl`` (162 hand-labelled synthetic
check-ins across ``en``, ``hi``, and ``bn`` and all nine internal emotion
labels: ``joy``, ``sadness``, ``anger``, ``fear``, ``anxiety``, ``shame``,
``loneliness``, ``calm``, ``neutral``) against the shipped emotion analyzer
pipeline and writes a Markdown report containing:

* **Headline metrics** (macro-F1, macro-precision, macro-recall, accuracy) with
  **95% bootstrap confidence intervals** across ``chain`` (keyword + sentiment
  fallback), ``keyword`` alone, and ``sentiment`` alone;
* **Per-emotion metrics** (support, precision, recall, F1, F1 95% CI);
* **9×9 confusion matrix** (ground truth rows vs predicted columns);
* **Per-language breakdown** (``en``, ``hi``, ``bn``) with macro-F1 and 95% CIs;
* **Domain-shift breakdown** (``in_domain`` lexical vs ``domain_shift``
  somatic / implicit / idiomatic / unmapped-script cases);
* **Documented limitations** (Reddit GoEmotions → Indian wellbeing domain shift,
  taxonomy collapse in polarity fallback, missing ``loneliness`` head in
  GoEmotions, and Indic script lexicon gaps);
* **CI gate check** against ``evals/thresholds.yaml``.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.nlp import AnalyzerChain  # noqa: E402
from app.services.nlp.base import EMOTIONS  # noqa: E402
from app.services.nlp.keyword import KeywordFallbackAnalyzer  # noqa: E402
from app.services.nlp.sentiment import SentimentAnalyzer  # noqa: E402

DATASET_PATH = REPO_ROOT / "evals" / "datasets" / "emotion_cases.jsonl"
REPORTS_DIR = REPO_ROOT / "evals" / "reports"
THRESHOLDS_PATH = REPO_ROOT / "evals" / "thresholds.yaml"

LANG_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi (Devanagari + Hinglish)",
    "bn": "Bengali (Bengali + Banglish)",
}
REGIME_NAMES: dict[str, str] = {
    "in_domain": "In-domain (explicit lexical emotion cues)",
    "domain_shift": "Domain-shift (somatic, implicit, idiomatic, or unmapped script)",
}


@dataclass(frozen=True)
class ScoredEmotionCase:
    id: str
    text: str
    lang: str
    script: str
    regime: str
    notes: str
    truth: str
    pred_chain: str
    conf_chain: float
    source_chain: str
    pred_keyword: str
    pred_sentiment: str


@dataclass(frozen=True)
class BootstrapCI:
    point: float
    low: float
    high: float


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict[str, Any]:
    if not path.exists():
        return {
            "bootstrap": {"iterations": 2000, "confidence_level": 0.95, "seed": 20261012},
            "emotion": {"macro_f1_min": 0.60, "per_language_macro_f1_min": 0.50},
        }
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_cases(path: Path = DATASET_PATH) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(
            f"missing {path} — run `python evals/build_emotion_dataset.py` first"
        )
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def score_cases(cases: list[dict[str, str]]) -> list[ScoredEmotionCase]:
    chain = AnalyzerChain(KeywordFallbackAnalyzer(), [SentimentAnalyzer()])
    kw = KeywordFallbackAnalyzer()
    sent = SentimentAnalyzer()

    scored: list[ScoredEmotionCase] = []
    for raw in cases:
        text = raw["text"]
        lang = raw["lang"]
        r_chain = chain.analyze(text, lang=lang)
        r_kw = kw.analyze(text, lang=lang)
        r_sent = sent.analyze(text, lang=lang)
        scored.append(
            ScoredEmotionCase(
                id=raw["id"],
                text=text,
                lang=lang,
                script=raw.get("script", "latin"),
                regime=raw.get("regime", "in_domain"),
                notes=raw.get("notes", ""),
                truth=raw["label"],
                pred_chain=r_chain.primary,
                conf_chain=r_chain.confidence,
                source_chain=r_chain.analyzer,
                pred_keyword=r_kw.primary,
                pred_sentiment=r_sent.primary,
            )
        )
    return scored


def _pred_of(case: ScoredEmotionCase, which: str) -> str:
    if which == "chain":
        return case.pred_chain
    if which == "keyword":
        return case.pred_keyword
    if which == "sentiment":
        return case.pred_sentiment
    raise ValueError(f"unknown analyzer key: {which!r}")


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def compute_class_metrics(
    cases: list[ScoredEmotionCase],
    *,
    which: str = "chain",
    labels: tuple[str, ...] = EMOTIONS,
) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = sum(1 for c in cases if c.truth == label and _pred_of(c, which) == label)
        fp = sum(1 for c in cases if c.truth != label and _pred_of(c, which) == label)
        fn = sum(1 for c in cases if c.truth == label and _pred_of(c, which) != label)
        support = tp + fn
        prec = _safe_div(tp, tp + fp)
        rec = _safe_div(tp, support)
        f1 = _safe_div(2 * prec * rec, prec + rec) if (prec + rec) else 0.0
        out[label] = {
            "support": float(support),
            "tp": float(tp),
            "fp": float(fp),
            "fn": float(fn),
            "precision": prec,
            "recall": rec,
            "f1": f1,
        }
    return out


def compute_summary_metrics(
    cases: list[ScoredEmotionCase],
    *,
    which: str = "chain",
    labels: tuple[str, ...] = EMOTIONS,
) -> dict[str, float]:
    if not cases:
        return {"accuracy": 0.0, "macro_precision": 0.0, "macro_recall": 0.0, "macro_f1": 0.0}
    per_class = compute_class_metrics(cases, which=which, labels=labels)
    present = [lbl for lbl in labels if per_class[lbl]["support"] > 0]
    if not present:
        present = list(labels)
    macro_p = sum(per_class[lbl]["precision"] for lbl in present) / len(present)
    macro_r = sum(per_class[lbl]["recall"] for lbl in present) / len(present)
    macro_f1 = sum(per_class[lbl]["f1"] for lbl in present) / len(present)
    acc = sum(1 for c in cases if c.truth == _pred_of(c, which)) / len(cases)
    return {
        "accuracy": acc,
        "macro_precision": macro_p,
        "macro_recall": macro_r,
        "macro_f1": macro_f1,
    }


def bootstrap_summary_cis(
    cases: list[ScoredEmotionCase],
    *,
    which: str = "chain",
    iterations: int = 2000,
    confidence_level: float = 0.95,
    seed: int = 20261012,
) -> dict[str, BootstrapCI]:
    point = compute_summary_metrics(cases, which=which)
    if not cases or iterations <= 0:
        return {k: BootstrapCI(v, v, v) for k, v in point.items()}

    rng = random.Random(seed)
    n = len(cases)
    samples: dict[str, list[float]] = {k: [] for k in point}
    for _ in range(iterations):
        draw = [cases[rng.randrange(n)] for _ in range(n)]
        m = compute_summary_metrics(draw, which=which)
        for k, val in m.items():
            samples[k].append(val)

    alpha = (1.0 - confidence_level) / 2.0
    out: dict[str, BootstrapCI] = {}
    for k, vals in samples.items():
        vals.sort()
        lo_idx = max(0, min(len(vals) - 1, int(alpha * len(vals))))
        hi_idx = max(0, min(len(vals) - 1, int((1.0 - alpha) * len(vals)) - 1))
        out[k] = BootstrapCI(point=point[k], low=vals[lo_idx], high=vals[hi_idx])
    return out


def bootstrap_per_class_f1_cis(
    cases: list[ScoredEmotionCase],
    *,
    which: str = "chain",
    iterations: int = 2000,
    confidence_level: float = 0.95,
    seed: int = 20261012,
) -> dict[str, BootstrapCI]:
    base = compute_class_metrics(cases, which=which)
    if not cases or iterations <= 0:
        return {lbl: BootstrapCI(base[lbl]["f1"], base[lbl]["f1"], base[lbl]["f1"]) for lbl in EMOTIONS}

    rng = random.Random(seed + 17)
    n = len(cases)
    samples: dict[str, list[float]] = {lbl: [] for lbl in EMOTIONS}
    for _ in range(iterations):
        draw = [cases[rng.randrange(n)] for _ in range(n)]
        cm = compute_class_metrics(draw, which=which)
        for lbl in EMOTIONS:
            samples[lbl].append(cm[lbl]["f1"])

    alpha = (1.0 - confidence_level) / 2.0
    out: dict[str, BootstrapCI] = {}
    for lbl, vals in samples.items():
        vals.sort()
        lo_idx = max(0, min(len(vals) - 1, int(alpha * len(vals))))
        hi_idx = max(0, min(len(vals) - 1, int((1.0 - alpha) * len(vals)) - 1))
        out[lbl] = BootstrapCI(point=base[lbl]["f1"], low=vals[lo_idx], high=vals[hi_idx])
    return out


def confusion_matrix_table(cases: list[ScoredEmotionCase], *, which: str = "chain") -> str:
    grid: dict[tuple[str, str], int] = Counter(
        (c.truth, _pred_of(c, which)) for c in cases
    )
    header = "| true \\ pred | " + " | ".join(EMOTIONS) + " | total |"
    sep = "| --- | " + " | ".join("---" for _ in EMOTIONS) + " | --- |"
    rows = [header, sep]
    for truth in EMOTIONS:
        counts = [grid.get((truth, pred), 0) for pred in EMOTIONS]
        total = sum(counts)
        cells = " | ".join(
            f"**{n}**" if (pred == truth and n > 0) else str(n)
            for pred, n in zip(EMOTIONS, counts, strict=True)
        )
        rows.append(f"| **{truth}** | {cells} | {total} |")
    return "\n".join(rows)


def evaluate_gate(
    cases: list[ScoredEmotionCase],
    thresholds_cfg: dict[str, Any],
) -> tuple[bool, list[dict[str, str]]]:
    emo_cfg = thresholds_cfg.get("emotion", {})
    macro_f1_min = float(emo_cfg.get("macro_f1_min", 0.60))
    per_lang_min = float(emo_cfg.get("per_language_macro_f1_min", 0.50))

    summary = compute_summary_metrics(cases, which="chain")
    checks: list[dict[str, str]] = []
    overall_pass = True

    c1 = summary["macro_f1"] >= macro_f1_min
    overall_pass = overall_pass and c1
    checks.append(
        {
            "check": "Overall macro-F1 (9-class emotion chain)",
            "threshold": f">= {macro_f1_min:.3f}",
            "observed": f"{summary['macro_f1']:.3f}",
            "verdict": "PASS" if c1 else "FAIL",
            "notes": "Evaluated across all 162 cases (in-domain + domain-shift)",
        }
    )

    lang_f1s: dict[str, float] = {}
    for lang in ("en", "hi", "bn"):
        subset = [c for c in cases if c.lang == lang]
        lang_f1s[lang] = compute_summary_metrics(subset, which="chain")["macro_f1"]
    worst_lang, worst_val = min(lang_f1s.items(), key=lambda kv: kv[1])
    c2 = worst_val >= per_lang_min
    overall_pass = overall_pass and c2
    checks.append(
        {
            "check": "Per-language macro-F1 floor (en, hi, bn)",
            "threshold": f">= {per_lang_min:.3f}",
            "observed": f"{worst_val:.3f} ({worst_lang})",
            "verdict": "PASS" if c2 else "FAIL",
            "notes": ", ".join(f"{k}={v:.3f}" for k, v in lang_f1s.items()),
        }
    )
    return overall_pass, checks


def render_report(
    cases: list[ScoredEmotionCase],
    *,
    run_date: str,
    thresholds_cfg: dict[str, Any],
) -> tuple[str, bool, dict[str, Any]]:
    boot_cfg = thresholds_cfg.get("bootstrap", {})
    b_iter = int(boot_cfg.get("iterations", 2000))
    b_conf = float(boot_cfg.get("confidence_level", 0.95))
    b_seed = int(boot_cfg.get("seed", 20261012))

    chain_cis = bootstrap_summary_cis(
        cases, which="chain", iterations=b_iter, confidence_level=b_conf, seed=b_seed
    )
    kw_cis = bootstrap_summary_cis(
        cases, which="keyword", iterations=b_iter, confidence_level=b_conf, seed=b_seed + 1
    )
    sent_cis = bootstrap_summary_cis(
        cases, which="sentiment", iterations=b_iter, confidence_level=b_conf, seed=b_seed + 2
    )

    class_metrics = compute_class_metrics(cases, which="chain")
    class_f1_cis = bootstrap_per_class_f1_cis(
        cases, which="chain", iterations=b_iter, confidence_level=b_conf, seed=b_seed
    )

    gate_passed, gate_checks = evaluate_gate(cases, thresholds_cfg)

    # Per-language metrics
    lang_rows: list[str] = []
    lang_summary: dict[str, dict[str, float]] = {}
    for lang in ("en", "hi", "bn"):
        subset = [c for c in cases if c.lang == lang]
        m = compute_summary_metrics(subset, which="chain")
        cis = bootstrap_summary_cis(
            subset,
            which="chain",
            iterations=b_iter,
            confidence_level=b_conf,
            seed=b_seed + hash(lang) % 997,
        )
        lang_summary[lang] = {
            "cases": float(len(subset)),
            "accuracy": m["accuracy"],
            "macro_precision": m["macro_precision"],
            "macro_recall": m["macro_recall"],
            "macro_f1": m["macro_f1"],
            "macro_f1_ci_low": cis["macro_f1"].low,
            "macro_f1_ci_high": cis["macro_f1"].high,
        }
        lang_rows.append(
            f"| {LANG_NAMES[lang]} (`{lang}`) | {len(subset)} | {m['accuracy']:.3f} | "
            f"{m['macro_precision']:.3f} | {m['macro_recall']:.3f} | "
            f"**{m['macro_f1']:.3f}** | [{cis['macro_f1'].low:.3f}, {cis['macro_f1'].high:.3f}] |"
        )

    # Regime (in_domain vs domain_shift) breakdown
    regime_rows: list[str] = []
    regime_summary: dict[str, dict[str, float]] = {}
    for regime in ("in_domain", "domain_shift"):
        subset = [c for c in cases if c.regime == regime]
        m = compute_summary_metrics(subset, which="chain")
        cis = bootstrap_summary_cis(
            subset,
            which="chain",
            iterations=b_iter,
            confidence_level=b_conf,
            seed=b_seed + (101 if regime == "in_domain" else 202),
        )
        regime_summary[regime] = {
            "cases": float(len(subset)),
            "accuracy": m["accuracy"],
            "macro_f1": m["macro_f1"],
            "macro_f1_ci_low": cis["macro_f1"].low,
            "macro_f1_ci_high": cis["macro_f1"].high,
        }
        regime_rows.append(
            f"| {REGIME_NAMES[regime]} | {len(subset)} | {m['accuracy']:.3f} | "
            f"{m['macro_precision']:.3f} | {m['macro_recall']:.3f} | "
            f"**{m['macro_f1']:.3f}** | [{cis['macro_f1'].low:.3f}, {cis['macro_f1'].high:.3f}] |"
        )

    in_f1 = regime_summary["in_domain"]["macro_f1"]
    shift_f1 = regime_summary["domain_shift"]["macro_f1"]
    domain_shift_drop = in_f1 - shift_f1

    # Per-class table
    class_rows: list[str] = []
    for label in EMOTIONS:
        cm = class_metrics[label]
        ci = class_f1_cis[label]
        class_rows.append(
            f"| `{label}` | {int(cm['support'])} | {cm['precision']:.3f} | "
            f"{cm['recall']:.3f} | **{cm['f1']:.3f}** | [{ci.low:.3f}, {ci.high:.3f}] |"
        )

    # Source distribution (how often chain used keyword vs sentiment vs default)
    source_counts = Counter(c.source_chain for c in cases)

    # Misclassified examples (up to 12 representative cases)
    errors = [c for c in cases if c.truth != c.pred_chain]
    error_lines: list[str] = []
    for err in errors[:12]:
        error_lines.append(
            f"- `{err.id}` (`{err.lang}`, `{err.regime}`): truth=`{err.truth}` -> "
            f"pred=`{err.pred_chain}` (source=`{err.source_chain}`, conf={err.conf_chain:.2f}) — "
            f"\"{err.text}\" *(note: {err.notes})*"
        )

    gate_rows = [
        f"| {g['check']} | `{g['threshold']}` | `{g['observed']}` | **{g['verdict']}** | {g['notes']} |"
        for g in gate_checks
    ]

    report = f"""# Emotion analyzer evaluation — {run_date}

- Dataset: `evals/datasets/emotion_cases.jsonl` ({len(cases)} hand-labelled synthetic check-ins across `en`, `hi`, `bn` and all 9 emotions).
- Pipeline under test: `AnalyzerChain(KeywordFallbackAnalyzer(), [SentimentAnalyzer()])` (the deterministic offline chain used when the optional HuggingFace transformer is absent or declines non-English text).
- Bootstrap: B={b_iter} resamples, {int(b_conf * 100)}% percentile CIs (seed={b_seed}).
- Predictor source usage on this set: `keyword`={source_counts.get('keyword', 0)}, `sentiment`={source_counts.get('sentiment', 0)}.

## Headline metrics (9-class emotion taxonomy)

| analyzer | accuracy | macro-precision | macro-recall | macro-F1 | macro-F1 95% CI | accuracy 95% CI |
| --- | --- | --- | --- | --- | --- | --- |
| **keyword + sentiment chain (shipped offline)** | **{chain_cis['accuracy'].point:.3f}** | **{chain_cis['macro_precision'].point:.3f}** | **{chain_cis['macro_recall'].point:.3f}** | **{chain_cis['macro_f1'].point:.3f}** | [{chain_cis['macro_f1'].low:.3f}, {chain_cis['macro_f1'].high:.3f}] | [{chain_cis['accuracy'].low:.3f}, {chain_cis['accuracy'].high:.3f}] |
| keyword lexicon only | {kw_cis['accuracy'].point:.3f} | {kw_cis['macro_precision'].point:.3f} | {kw_cis['macro_recall'].point:.3f} | {kw_cis['macro_f1'].point:.3f} | [{kw_cis['macro_f1'].low:.3f}, {kw_cis['macro_f1'].high:.3f}] | [{kw_cis['accuracy'].low:.3f}, {kw_cis['accuracy'].high:.3f}] |
| sentiment polarity only | {sent_cis['accuracy'].point:.3f} | {sent_cis['macro_precision'].point:.3f} | {sent_cis['macro_recall'].point:.3f} | {sent_cis['macro_f1'].point:.3f} | [{sent_cis['macro_f1'].low:.3f}, {sent_cis['macro_f1'].high:.3f}] | [{sent_cis['accuracy'].low:.3f}, {sent_cis['accuracy'].high:.3f}] |

## CI gate status

Overall CI gate verdict: **{'PASS' if gate_passed else 'FAIL'}** (configured in `evals/thresholds.yaml`)

| check | threshold | observed | verdict | notes |
| --- | --- | --- | --- | --- |
{chr(10).join(gate_rows)}

## Per-emotion metrics (`keyword + sentiment chain`)

| emotion | support | precision | recall | F1 | F1 95% CI |
| --- | --- | --- | --- | --- | --- |
{chr(10).join(class_rows)}

## Confusion matrix (9×9, `keyword + sentiment chain`)

Rows are ground-truth labels; columns are predicted primary emotions.

{confusion_matrix_table(cases, which="chain")}

## Per-language performance

| language | cases | accuracy | macro-precision | macro-recall | macro-F1 | macro-F1 95% CI |
| --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(lang_rows)}

## Impact of domain shift (`in_domain` vs `domain_shift`)

| regime | cases | accuracy | macro-precision | macro-recall | macro-F1 | macro-F1 95% CI |
| --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(regime_rows)}

Observed domain-shift penalty: macro-F1 drops by **{domain_shift_drop:.3f}** (from **{in_f1:.3f}** on explicit lexical check-ins to **{shift_f1:.3f}** on somatic, idiomatic, sarcastic, or unmapped-script check-ins).

## Documented limitations (why emotion scores must never drive safety)

1. **Domain shift (Reddit GoEmotions -> Indian student/worker wellbeing check-ins)**:
   - Even when the optional English `SamLowe/roberta-base-go_emotions` transformer is loaded, its training distribution is short US/UK Reddit comments. Indian users frequently describe distress somatically (*"my shoulders are up by my ears"*, *"हाथ-पैर ठंडे पड़ गए"*, *"বুকটা ধড়াস করে উঠল"*), behaviourally (*"stared at my dinner plate untouched"*), or through family/exam framing rather than explicit affect adjectives.
2. **Taxonomy gaps between GoEmotions (27 labels) and Manovia's 9-emotion schema**:
   - `loneliness` has **no corresponding label** in GoEmotions (`LABEL_MAP` in `backend/app/services/nlp/emotion.py` maps 0 of 27 heads to `loneliness`); it can only be surfaced when `KeywordFallbackAnalyzer` matches a token like `lonely`, `akela`, or `একা`.
3. **Cross-lingual & script coverage gaps**:
   - The HuggingFace checkpoint is English-only and declines `hi` and `bn`, so Hindi and Bengali check-ins always route through `KeywordFallbackAnalyzer` -> `SentimentAnalyzer`. Several emotion categories have sparse native-script coverage (for example, `shame` has romanised Hindi words `sharm`/`sharminda` and Devanagari `शर्म`, but no Bengali-script tokens for `লজ্জা` or `অপরাধবোধ`).
4. **Polarity fallback collapses 9 classes into 4**:
   - When `KeywordFallbackAnalyzer` finds no keyword hit, `SentimentAnalyzer` maps scalar polarity solely onto `{{"joy", "calm", "neutral", "sadness"}}`. Any implicit `anger`, `fear`, `anxiety`, `shame`, or `loneliness` utterance that misses the keyword lexicon is therefore misclassified as `neutral` (if no polarity words match) or `sadness` (if negative polarity words match).
5. **Contextual negation and academic/figurative mentions**:
   - Bag-of-words keyword matching triggers on emotion words used inside book titles or academic notes (e.g., *"reading a chapter on the Great Depression"* -> `sadness`; *"meeting about the frightening budget numbers"* -> `fear`).
6. **Architectural consequence**:
   - Because of these limitations, emotion labels are used **only** as a soft tone hint for the LLM prompt (`build_prompt`) and for opt-in mood-trend visualisations. They **never** gate crisis escalation (`SafetyTriageService` runs independently on every turn).

## Representative failure modes ({len(errors)} misclassified of {len(cases)})

{chr(10).join(error_lines) if error_lines else "- None."}
"""

    json_summary: dict[str, Any] = {
        "date": run_date,
        "cases": len(cases),
        "gate_passed": gate_passed,
        "gate_checks": gate_checks,
        "chain": {
            "accuracy": chain_cis["accuracy"].point,
            "accuracy_ci": [chain_cis["accuracy"].low, chain_cis["accuracy"].high],
            "macro_precision": chain_cis["macro_precision"].point,
            "macro_recall": chain_cis["macro_recall"].point,
            "macro_f1": chain_cis["macro_f1"].point,
            "macro_f1_ci": [chain_cis["macro_f1"].low, chain_cis["macro_f1"].high],
        },
        "keyword_only": {
            "accuracy": kw_cis["accuracy"].point,
            "macro_f1": kw_cis["macro_f1"].point,
            "macro_f1_ci": [kw_cis["macro_f1"].low, kw_cis["macro_f1"].high],
        },
        "sentiment_only": {
            "accuracy": sent_cis["accuracy"].point,
            "macro_f1": sent_cis["macro_f1"].point,
            "macro_f1_ci": [sent_cis["macro_f1"].low, sent_cis["macro_f1"].high],
        },
        "per_language": lang_summary,
        "per_regime": regime_summary,
        "domain_shift_f1_drop": domain_shift_drop,
        "misclassified_count": len(errors),
    }
    return report, gate_passed, json_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATASET_PATH,
        help="Path to emotion_cases.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output Markdown path (defaults to evals/reports/emotion_<today>.md)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to write machine-readable JSON summary.",
    )
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=THRESHOLDS_PATH,
        help="Path to thresholds YAML file.",
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="Report CI gate status without returning non-zero exit code on failure.",
    )
    args = parser.parse_args(argv)

    raw_cases = load_cases(args.dataset)
    scored = score_cases(raw_cases)
    run_date = date.today().isoformat()
    thresholds_cfg = load_thresholds(args.thresholds)

    report, gate_passed, json_summary = render_report(
        scored,
        run_date=run_date,
        thresholds_cfg=thresholds_cfg,
    )

    out_path = args.out or (REPORTS_DIR / f"emotion_{run_date}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(json_summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    c = json_summary["chain"]
    print(
        f"emotion eval: cases={len(scored)} "
        f"macro_f1={c['macro_f1']:.3f} [{c['macro_f1_ci'][0]:.3f}, {c['macro_f1_ci'][1]:.3f}] "
        f"accuracy={c['accuracy']:.3f} [{c['accuracy_ci'][0]:.3f}, {c['accuracy_ci'][1]:.3f}]"
    )
    print(
        f"  in_domain macro_f1={json_summary['per_regime']['in_domain']['macro_f1']:.3f}  "
        f"domain_shift macro_f1={json_summary['per_regime']['domain_shift']['macro_f1']:.3f}  "
        f"(drop={json_summary['domain_shift_f1_drop']:.3f})"
    )
    print(f"  CI gate: {'PASS' if gate_passed else 'FAIL'}")
    print(f"  report written: {out_path.relative_to(REPO_ROOT) if out_path.is_relative_to(REPO_ROOT) else out_path}")

    if not gate_passed and not args.no_gate:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
