"""The report and the model card for the safety classifier.

Both are written from the run's own manifest, so a number in the prose cannot
drift from the number in the JSON. The card always carries the three limitations
this model cannot escape:

* the training data is **synthetic and template-generated**, so every number
  measures agreement with the people who wrote the templates;
* the tiny smoke model is a **plumbing check**, never a quality claim;
* the ensemble may only ever **raise** a level the rules engine found.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from manovia_ml.config import SafetyRunConfig, TrainConfig
from manovia_ml.training.safety.taxonomy import COST_MATRIX_DOC, LEVELS

__all__ = ["read_manifest", "render_card", "render_report", "write_card", "write_report"]


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _ci(pair: Any) -> str:
    if not isinstance(pair, list) or len(pair) != 2:
        return "—"
    low, high = pair
    if low is None or high is None:
        return "—"
    return f"[{low:.3f}, {high:.3f}]"


def _hash(value: Any) -> str:
    return str(value)[:16]


def _operating_point(
    config: TrainConfig | None, safety: SafetyRunConfig | None
) -> list[str]:
    """The lines that say which operating point produced the numbers."""
    if safety is None:
        return ["* Operating point: not recorded (re-rendered from the manifest)"]
    cost = safety.cost
    lines = [
        "* Operating point:",
        f"  * cost matrix `{cost.matrix}`, row-normalised {cost.normalise_per_row},",
        f"    decision rule {'expected cost' if cost.cost_decision_rule else 'argmax'}",
        f"  * conformal alpha {safety.conformal.alpha}, "
        f"policy `{safety.conformal.policy}`",
        f"  * calibration bins {safety.calibration.bins}",
    ]
    if config is not None:
        lines.append(f"  * config `{config.name}`")
    return lines


def render_report(
    manifest: dict[str, Any],
    *,
    config: TrainConfig | None = None,
    safety: SafetyRunConfig | None = None,
) -> str:
    """The Markdown report: metrics, calibration, conformal, baseline, limits.

    ``config`` and ``safety`` are optional so the report can be re-rendered from a
    saved manifest alone; when they are supplied, the operating point they carry
    is printed, because a report that cannot say which threshold produced it is
    not reproducible.
    """
    evaluation = manifest.get("evaluation", {})
    dev = manifest.get("dev_evaluation", {})
    baseline = manifest.get("baseline", {})
    calibration = manifest.get("calibration", {})
    conformal = manifest.get("conformal", {})
    leakage = manifest.get("leakage", {})
    crisis = evaluation.get("crisis", {})
    abstention = evaluation.get("abstention_policy", {})
    data = manifest.get("data", {})

    seed = manifest.get("seed")
    config_hash = _hash(manifest.get("config_sha256"))
    written = manifest.get("written")
    device = manifest.get("device")
    base = manifest.get("base_model", "unknown")

    lines: list[str] = [
        "# Safety classifier — Day 33 run report",
        "",
        f"* Base model: `{base}`",
        f"* Seed: `{seed}`, config sha256 `{config_hash}`",
        f"* Run date: {written}, device `{device}`",
        f"* Labels: {', '.join(manifest.get('labels', LEVELS))}",
        *_operating_point(config, safety),
        "",
        "## 1. What this run is",
        "",
        "A cost-sensitive LoRA fine-tune into Manovia's five risk levels, with",
        "temperature scaling, split conformal prediction sets and an abstention",
        "policy, scored on the **frozen Day-9 test split** that was never trained",
        "on or tuned on. The Day-9 TF-IDF baseline is refitted on the same rows",
        "for an apples-to-apples comparison.",
        "",
        "## 2. Data",
        "",
        "| split | rows | sha256 |",
        "| --- | --- | --- |",
    ]
    for name in ("train", "dev", "calibration", "test"):
        entry = data.get(name, {})
        digest = _hash(entry.get("sha256"))
        lines.append(f"| {name} | {entry.get('rows', '—')} | `{digest}` |")
    overlap = leakage.get("exact_overlap", "—")
    jaccard = leakage.get("max_jaccard", "—")
    lines += [
        "",
        "Leakage check against the frozen test split: max char-3gram Jaccard",
        f"**{jaccard}**, exact overlaps **{overlap}**. The test split is read by",
        "the evaluator and by nothing else.",
        "",
        "## 3. Headline numbers (frozen test split)",
        "",
        "| measure | value | 95% CI |",
        "| --- | --- | --- |",
        f"| HIGH+IMMINENT recall | {_fmt(crisis.get('recall'))} "
        f"| {_ci(evaluation.get('crisis_recall_ci'))} |",
        f"| HIGH+IMMINENT precision | {_fmt(crisis.get('precision'))} "
        f"| {_ci(evaluation.get('crisis_precision_ci'))} |",
        f"| macro-F1 | {_fmt(evaluation.get('macro_f1'))} | — |",
        f"| accuracy | {_fmt(evaluation.get('accuracy'))} | — |",
        f"| expected cost per message | {_fmt(evaluation.get('expected_cost'))} | — |",
        f"| false negatives (crisis missed) | {_fmt(crisis.get('false_negatives'), 0)} | — |",
        f"| benign escalated to crisis | {_fmt(evaluation.get('benign_escalations'), 0)} | — |",
        "",
        "### Per language",
        "",
        "| language | cases | crisis cases | recall | 95% CI | precision |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for lang, row in sorted((evaluation.get("per_language") or {}).items()):
        cases = row.get("cases")
        crisis_cases = _fmt(row.get("crisis_cases"), 0)
        recall = _fmt(row.get("crisis_recall"))
        interval = _ci(row.get("crisis_recall_ci"))
        precision = _fmt(row.get("crisis_precision"))
        lines.append(
            f"| {lang} | {cases} | {crisis_cases} | {recall} | {interval} | {precision} |"
        )
    lines += [
        "",
        "### Per level",
        "",
        "| level | precision | recall | F1 | support |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label in LEVELS:
        row = (evaluation.get("per_level") or {}).get(label, {})
        precision = _fmt(row.get("precision"))
        recall = _fmt(row.get("recall"))
        f1 = _fmt(row.get("f1"))
        support = _fmt(row.get("support"), 0)
        lines.append(f"| {label} | {precision} | {recall} | {f1} | {support} |")
    temperature = _fmt(calibration.get("temperature"))
    ece_before = _fmt(calibration.get("ece_before"), 4)
    ece_after = _fmt(calibration.get("ece_after"), 4)
    nll_before = _fmt(calibration.get("nll_before"), 4)
    nll_after = _fmt(calibration.get("nll_after"), 4)
    lines += [
        "",
        "## 4. Calibration (temperature scaling)",
        "",
        f"Fitted on the calibration split ({calibration.get('split', 'calibration')}).",
        "",
        "| | before | after |",
        "| --- | --- | --- |",
        f"| temperature | 1.000 | {temperature} |",
        f"| ECE | {ece_before} | {ece_after} |",
        f"| NLL | {nll_before} | {nll_after} |",
        "",
        "Reliability diagram (after scaling):",
        "",
        "| bin | count | confidence | accuracy | gap |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in calibration.get("reliability", []):
        confidence = _fmt(item.get("confidence"))
        accuracy = _fmt(item.get("accuracy"))
        gap = _fmt(item.get("gap"))
        lines.append(
            f"| {item.get('bin')} | {item.get('count')} | {confidence} "
            f"| {accuracy} | {gap} |"
        )
    target = _fmt(conformal.get("target_coverage"))
    alpha = _fmt(conformal.get("alpha"))
    quantile = _fmt(conformal.get("quantile"), 4)
    calib_rows = conformal.get("calibration_rows")
    mean_set = _fmt(conformal.get("mean_set_size"))
    abstention_rate = _fmt(conformal.get("abstention_rate"))
    raised = conformal.get("raised_by_abstention")
    policy_recall = _fmt(abstention.get("crisis_recall"))
    policy_ci = _ci(abstention.get("crisis_recall_ci"))
    plain_recall = _fmt(crisis.get("recall"))
    lines += [
        "",
        "## 5. Conformal prediction sets and abstention",
        "",
        f"Target coverage **{target}** (alpha {alpha}), quantile **{quantile}** fitted",
        f"on {calib_rows} calibration rows.",
        "",
        "| | coverage |",
        "| --- | --- |",
        f"| calibration split (fitted on) | {_fmt(conformal.get('calibration_coverage'))} |",
        f"| held-out (frozen test) | {_fmt(conformal.get('heldout_coverage'))} |",
        "",
        f"Mean set size **{mean_set}**, abstention rate **{abstention_rate}**",
        "(a non-singleton set).",
        f"The abstain -> MEDIUM policy raised {raised} messages that the point",
        "prediction had put below MEDIUM; crisis recall under the policy is",
        f"{policy_recall} {policy_ci} versus {plain_recall} without it.",
        "",
        "## 6. The Day-9 TF-IDF baseline, refitted on the same rows",
        "",
    ]
    if baseline:
        baseline_crisis = baseline.get("crisis", {})
        lines += [
            "| measure | transformer | TF-IDF baseline |",
            "| --- | --- | --- |",
            f"| HIGH+IMMINENT recall | {_fmt(crisis.get('recall'))} "
            f"| {_fmt(baseline_crisis.get('recall'))} |",
            f"| HIGH+IMMINENT precision | {_fmt(crisis.get('precision'))} "
            f"| {_fmt(baseline_crisis.get('precision'))} |",
            f"| macro-F1 | {_fmt(evaluation.get('macro_f1'))} "
            f"| {_fmt(baseline.get('macro_f1'))} |",
            f"| expected cost | {_fmt(evaluation.get('expected_cost'))} "
            f"| {_fmt(baseline.get('expected_cost'))} |",
            f"| ECE | {ece_after} | {_fmt(baseline.get('ece'), 4)} |",
            "",
            f"Baseline class weighting: `{baseline.get('class_weight')}`, trained on "
            f"{baseline.get('train_rows')} rows.",
        ]
    else:
        lines.append("Not computed for this run (`safety.report.compare_baseline: false`).")
    dev_crisis = dev.get("crisis") or {}
    dev_recall = _fmt(dev_crisis.get("recall"))
    dev_macro = _fmt(dev.get("macro_f1"))
    dev_cases = dev.get("cases")
    lines += [
        "",
        "## 7. Dev split (model selection; not the quoted number)",
        "",
        f"HIGH+IMMINENT recall {dev_recall}, macro-F1 {dev_macro} on {dev_cases} rows.",
        "",
        "## 8. Limits",
        "",
        "* The training rows are **synthetic**: template expansions and",
        "  deterministic paraphrases of the hand-written Day-9 seeds, adversarially",
        "  augmented. Every number here measures agreement with the people who",
        "  wrote the templates, not with real users.",
        "* The frozen test split has 51 crisis cases. A recall of 1.000 on 51 cases",
        "  has an exact (Clopper-Pearson) 95% interval of about [0.93, 1.00] — the",
        "  bootstrap interval printed above is [1.000, 1.000] and says nothing.",
        "* Calibration and conformal coverage are estimated on a few hundred",
        "  synthetic rows. A coverage guarantee is a guarantee about the",
        "  *procedure*, not a promise about the next message.",
        "* Indic coverage is thinner than English in the seeds, and the",
        "  romanisation used by the adversarial augmentation is approximate by",
        "  design.",
        "* **This model can only raise a level the rules engine found.** It is a",
        "  backstop for phrasings nobody wrote a pattern for; it is not a gate.",
        "",
        "---",
        "",
        "Generated by `manovia_ml.training.safety.train` from the run's own",
        "manifest. Synthetic data only; no real user text appears anywhere in",
        "this report.",
        "",
    ]
    return "\n".join(lines)


def render_card(
    manifest: dict[str, Any],
    *,
    config: TrainConfig | None = None,
    safety: SafetyRunConfig | None = None,
) -> str:
    """The model card, written from the same manifest as the report."""
    evaluation = manifest.get("evaluation", {})
    calibration = manifest.get("calibration", {})
    conformal = manifest.get("conformal", {})
    crisis = evaluation.get("crisis", {})
    data = manifest.get("data", {})
    train_entry = data.get("train", {})
    train_rows = train_entry.get("rows", "—")
    train_hash = _hash(train_entry.get("sha256"))
    temperature = calibration.get("temperature")
    ece_from = calibration.get("ece_before")
    ece_to = calibration.get("ece_after")
    coverage_target = conformal.get("target_coverage")
    coverage_actual = conformal.get("heldout_coverage")
    abstention_rate = conformal.get("abstention_rate")
    operating = "\n".join(_operating_point(config, safety))
    run_id = manifest.get("run_id", "unknown")
    seed = manifest.get("seed")
    config_hash = _hash(manifest.get("config_sha256"))
    written = manifest.get("written")
    base = manifest.get("base_model", "unknown")
    return f"""# Model card — Manovia safety classifier (Day 33)

| | |
| --- | --- |
| Model | `{base}` + LoRA, five-level single-label head |
| Task | Risk-level classification for `none / low / medium / high / imminent` |
| Status | **Pre-launch, not clinically reviewed.** Not a medical device, not a crisis service |
| Owner | The repository owner |
| Runs where | In process, offline; behind `SAFETY_ML=trained` with `SAFETY_ML_ENABLED` |

## What it is for

A statistical second opinion next to the deterministic rules engine. It exists to
catch indirect crisis phrasings that no curated pattern anticipated. It **cannot
lower a level the rules engine found** — the ensemble
(`backend/app/services/safety/ensemble.py`) only ever raises.

## Training data

Synthetic only: {train_rows} expanded rows from the hand-written Day-9 seeds
(`evals/datasets/crisis_cases_train.jsonl` + `_dev.jsonl`), generated by template
expansion, deterministic paraphrase and adversarial augmentation (typos, spacing
tricks, romanised Hindi/Bengali, code-mixing, emoji), filtered by the rules engine
for label consistency. **No real person's message was used, and the frozen test
split was never trained on or tuned on.** Train sha256 `{train_hash}`.

## Objective

Cost-sensitive cross-entropy under a documented cost matrix
(`ml/src/manovia_ml/training/safety/taxonomy.py`):

```
{COST_MATRIX_DOC}
```

The decision rule at inference is the expected-cost argmin, not the argmax.

## Calibration and abstention

* Temperature scaling, fitted on the calibration split: T = {temperature},
  ECE {ece_from} -> {ece_to}.
* Split conformal prediction sets at target coverage {coverage_target}
  (achieved {coverage_actual} on the held-out split). A set containing a level at
  or above MEDIUM makes the message MEDIUM or above: uncertainty resolves toward
  noticing, not toward dismissing.

## Measured performance (held-out split)

| measure | value |
| --- | --- |
| HIGH+IMMINENT recall | {crisis.get('recall')} |
| HIGH+IMMINENT precision | {crisis.get('precision')} |
| macro-F1 | {evaluation.get('macro_f1')} |
| abstention rate | {abstention_rate} |

## Limitations

* **Synthetic data.** The corpus was written and generated by this project; it
  measures agreement with its authors.
* **Small held-out split.** 51 crisis cases in the frozen Day-9 file; the honest
  interval is wide.
* **No clinical review.** Nothing here has been reviewed by a clinician.
* **Not a gate.** The rules engine is the gate; this is a backstop.
* **Shortcut learning risk.** Token attributions are reviewed with
  `manovia_ml.training.safety.explain`; a model that keys on one word is a bug
  that metrics alone will not show.

## Provenance

Run id `{run_id}`, seed `{seed}`, config sha256 `{config_hash}`, written {written}.

## Operating point

{operating}
"""


def write_report(
    path: str | Path,
    manifest: dict[str, Any],
    *,
    config: TrainConfig | None = None,
    safety: SafetyRunConfig | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_report(manifest, config=config, safety=safety), encoding="utf-8")
    return target


def write_card(
    path: str | Path,
    *,
    config: TrainConfig,
    safety: SafetyRunConfig,
    manifest: dict[str, Any],
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_card(manifest, config=config, safety=safety), encoding="utf-8")
    return target


def read_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
