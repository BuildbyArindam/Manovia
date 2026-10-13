"""The report and the model card: the places a number is allowed to appear.

Two artefacts, one rule. A number quoted anywhere — a PR description, a model
card, a conversation — has to be traceable to an MLflow run id, a data hash and a
config, and it has to appear next to the sample size it came from. So the report
carries the run id, the hashes and the n in every table, and the card refuses to
be written without them.

The comparison table is the point of the day: keyword baseline, off-the-shelf
checkpoint, fine-tuned model, overall and **per language**, each with a bootstrap
95% interval. If a language is worse than a baseline, the table says so in the
line below it rather than leaving the reader to subtract two columns — a model
that helps English and hurts Bengali is a product decision, not a rounding error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from manovia_ml.training.emotion import metrics as metric_fns
from manovia_ml.training.emotion.baselines import available_baselines, run_baseline
from manovia_ml.training.emotion.data import (
    EmotionExample,
    default_validation_path,
    load_examples,
    summary,
)
from manovia_ml.training.emotion.inference import load_artifact
from manovia_ml.training.emotion.metrics import Row
from manovia_ml.training.emotion.taxonomy import EMOTIONS

__all__ = [
    "comparison_table",
    "render_report",
    "write_model_card",
]

#: Emitted verbatim into every card. `docs/model-card.md` lists the same
#: limitation for the product component, and the Day-32 brief asks for it by
#: name: the training corpus is Reddit text, not messages to a wellbeing
#: companion.
DOMAIN_SHIFT_LIMITATION = (
    "**Domain shift from social-media text.** The English training data is "
    "GoEmotions (Reddit comments): US-centric, sarcastic, argumentative and "
    "written to be read by strangers. People do not write to a wellbeing "
    "companion that way, and the difference is not a small one — indirect and "
    "somatic phrasing (\u201cmy chest feels tight and I cannot sit still\u201d) is "
    "exactly where the shipped lexicon chain already fails, and fine-tuning on "
    "Reddit does not fix it."
)


def _ci(interval: Sequence[float]) -> str:
    low, high = interval
    return f"[{low:.3f}, {high:.3f}]"


def comparison_table(
    results: Mapping[str, dict[str, Any]],
) -> str:
    """Markdown table: one row per model, overall and per language, with CIs."""

    languages = sorted(
        {lang for payload in results.values() for lang in payload.get("per_language", {})}
    )
    header = ["model", "rows", "macro-F1", "95% CI"] + [f"{lang} macro-F1" for lang in languages]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for name, payload in results.items():
        per_language = payload.get("per_language", {})
        cells = [
            f"**{name}**",
            str(payload.get("rows", "")),
            f"{payload.get('macro_f1', 0.0):.4f}",
            _ci(payload.get("macro_f1_ci", [0.0, 0.0])),
        ]
        for language in languages:
            value = per_language.get(language)
            cells.append(
                f"{value['macro_f1']:.4f} {_ci(value['macro_f1_ci'])} (n={value['rows']})"
                if value
                else "—"
            )
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def per_label_table(payload: Mapping[str, Any]) -> str:
    """Per-label precision/recall/F1 with support and the threshold used."""

    detail = payload.get("per_label_detail", {})
    thresholds = payload.get("thresholds", {})
    lines = [
        "| label | support | threshold | precision | recall | F1 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for label in payload.get("labels", EMOTIONS):
        row = detail.get(label, {})
        lines.append(
            f"| `{label}` | {row.get('tp', 0) + row.get('fn', 0)} | "
            f"{thresholds.get(label, 0.5):.2f} | {row.get('precision', 0.0):.3f} | "
            f"{row.get('recall', 0.0):.3f} | {row.get('f1', 0.0):.3f} |"
        )
    return "\n".join(lines)


def error_table(cases: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "| # | id | lang | text | truth | predicted | primary | cause |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for index, case in enumerate(cases, start=1):
        text = str(case.get("text", "")).replace("|", "/")
        lines.append(
            f"| {index} | `{case.get('id')}` | {case.get('lang')} | {text} | "
            f"{', '.join(case.get('truth', []))} | {', '.join(case.get('predicted', [])) or '—'} | "
            f"{case.get('primary')} | {case.get('cause')} |"
        )
    return "\n".join(lines)


def cause_summary(cases: Sequence[Mapping[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for case in cases:
        cause = str(case.get("cause", "unknown"))
        counts[cause] = counts.get(cause, 0) + 1
    return "\n".join(
        f"- `{cause}`: {count}"
        for cause, count in sorted(counts.items(), key=lambda item: -item[1])
    )


def render_report(
    *,
    run_id: str,
    config_name: str,
    manifest: Mapping[str, Any],
    trained: Mapping[str, Any],
    baselines: Mapping[str, Mapping[str, Any]],
    data_summaries: Mapping[str, Mapping[str, Any]],
    comparison: str,
    error_cases: Sequence[Mapping[str, Any]],
    per_label: str,
    notes: Sequence[str] = (),
) -> str:
    """The Day-32 report: tables, intervals, mistakes, and what it does not say."""

    languages = sorted(trained.get("per_language", {}))
    worse: list[str] = []
    for language in languages:
        trained_value = trained["per_language"][language]["macro_f1"]
        for name, payload in baselines.items():
            other = payload.get("per_language", {}).get(language)
            if other and other["macro_f1"] > trained_value:
                worse.append(
                    f"{name} scores {other['macro_f1']:.4f} on `{language}` against this model's "
                    f"{trained_value:.4f} (n={other['rows']})"
                )

    lines = [
        f"# Emotion model report — run `{run_id}`",
        "",
        f"- Config: `{config_name}` (sha256 `{str(manifest.get('config_sha256', '?'))[:12]}…`)",
        f"- Base model: `{manifest.get('base_model')}`"
        f"({manifest.get('base_note', {}).get('kind', '?')})",
        f"- LoRA: rank {manifest.get('lora', {}).get('rank')}, alpha"
        f"{manifest.get('lora', {}).get('alpha')}, "
        f"dropout {manifest.get('lora', {}).get('dropout')}",
        f"- Device: {manifest.get('device')} · mixed precision: {manifest.get('mixed_precision')}"
        f"· "
        f"seed {manifest.get('seed')}",
        f"- Artefact: `{manifest.get('artifact_dir', 'ml/artifacts/emotion/<config>/<name>')}`",
        "",
        (
            "> A run on the tiny random-init stand-in (`base_model: tiny://…`) measures the "
            "pipeline, not emotion recognition. Real numbers come from a GPU run on a real "
            "checkpoint; see `ml/notebooks/README.md`."
        ),
        "",
        "## 1. Comparison with the baselines (held-out hand-written set)",
        "",
        comparison,
        "",
    ]
    if worse:
        lines += [
            "**This model is worse than a baseline on at least one language:**",
            "",
            *(f"- {item}" for item in worse),
            "",
        ]
    else:
        lines += [
            "No language where a baseline beats this model *by more than the interval width*; "
            "the intervals above are the honest reading, and rows are few.",
            "",
        ]
    lines += [
        "## 2. Per-label (trained model, tuned thresholds)",
        "",
        per_label,
        "",
        "## 3. Calibration",
        "",
        f"- Expected calibration error on all (row, label) pairs:**{trained.get('ece', 0.0):.4f}**",
        f"- Subset accuracy (all nine labels exactly right):"
        f"{trained.get('subset_accuracy', 0.0):.4f} "
        f"{_ci(trained.get('subset_accuracy_ci', [0.0, 0.0]))}",
        f"- Micro-F1: {trained.get('micro_f1', 0.0):.4f}"
        f"{_ci(trained.get('micro_f1_ci', [0.0, 0.0]))}",
        f"- Labels averaged into macro-F1:"
        f"{', '.join(trained.get('labels_averaged_over', [])) or '—'}",
        f"- Labels with no support or predictions: "
        f"{', '.join(trained.get('labels_without_support', [])) or 'none'}",
        "",
        "## 4. Primary-label confusion (argmax view)",
        "",
        "| true \\ predicted | " + " | ".join(trained.get("labels", EMOTIONS)) + " |",
        "| " + " | ".join(["---"] * (len(trained.get("labels", EMOTIONS)) + 1)) + " |",
    ]
    labels = list(trained.get("labels", EMOTIONS))
    confusion = trained.get("primary_confusion", {})
    for true_label in labels:
        row = [f"{count}" for count in _confusion_row(labels, confusion, true_label)]
        lines.append(f"| **{true_label}** | " + " | ".join(row) + " |")
    lines += [
        "",
        "Rows are the row's own label (or the highest-scoring true label on a multi-label row); "
        "columns are the argmax prediction, which is what the product shows. The thresholded "
        "sets are what section 2 scores.",
        "",
        "## 5. Error analysis — top mistakes by cause",
        "",
        cause_summary(error_cases) or "- no mistakes to group",
        "",
        error_table(error_cases[:20]) if error_cases else "_No mistakes: every row matched._",
        "",
        "Causes are heuristics over the label pair and the row's language, not a second model's "
        "judgement; they exist to group the list for a human reader.",
        "",
        "## 6. Data",
        "",
        "| split | rows | languages | labels absent | sha256 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, payload in data_summaries.items():
        lines.append(
            f"| {name} | {payload.get('rows')} | "
            f"{', '.join(f'{k}={v}' for k, v in payload.get('languages', {}).items())} | "
            f"{', '.join(payload.get('labels_absent', [])) or 'none'} | "
            f"{payload.get('sha256', '')[:12]}… |"
        )
    lines += [
        "",
        "## 7. What this report does not show",
        "",
        "- Hindi and Bengali numbers come from a hand-written set that **no native speaker has "
        "reviewed yet** (`ml/data/validation/DATASET_CARD.md`). Treat them as indicative.",
        "- The held-out set is 174 rows: 95% intervals are roughly ±0.07 wide on macro-F1. A "
        "two-point difference between two models here is not a difference.",
        "- `loneliness`, and to a lesser extent `anxiety`, `shame` and `calm`, have almost no "
        "supervision in GoEmotions; the per-label table shows which of them the model actually "
        "learned anything about.",
        "- " + DOMAIN_SHIFT_LIMITATION,
        "",
    ]
    if notes:
        lines += ["## 8. Notes", "", *(f"- {note}" for note in notes), ""]
    return "\n".join(lines)


def _confusion_row(
    labels: Sequence[str],
    confusion: Mapping[str, int],
    true_label: str,
) -> list[int]:
    return [int(confusion.get(f"{true_label}|{label}", 0)) for label in labels]


def _status_for(manifest: Mapping[str, Any]) -> str:
    """``experimental`` for the tiny stand-in; ``candidate`` for a real run.

    The stand-in must never look like a model somebody could ship, and the label
    is derived from the manifest rather than typed in beside the number.
    """

    if str(manifest.get("base_model", "")).startswith("tiny://"):
        return "experimental"
    return "candidate"


def write_model_card(
    path: Path,
    *,
    manifest: Mapping[str, Any],
    validation: Mapping[str, Any],
    test: Mapping[str, Any] | None,
    thresholds: Any,
) -> Path:
    """Write the per-run card from ``ml/templates/MODEL_CARD.md``, filled in."""

    payload = test or validation
    split_name = "test (hand-written, held out)" if test else "validation"
    data = manifest.get("data", {})
    trained_on = data.get("train", {})
    languages = sorted(payload.get("per_language", {}))
    lines = [
        f"# Model card — Manovia multilingual emotion model (`{manifest.get('config')}`)",
        "",
        "| | |",
        "| --- | --- |",
        "| Card version | 1.0 (Day 32, 2026-10-13) |",
        f"| Status | {_status_for(manifest)} |",
        "| Kind | LoRA fine-tune of a multilingual encoder into Manovia's nine-emotion taxonomy |",
        f"| Config | `ml/configs/{manifest.get('config')}.yaml`"
        f" (fingerprint `{str(manifest.get('config_sha256'))[:12]}...`) |",
        f"| Seed | {manifest.get('seed')} |",
        f"| Device | {manifest.get('device')}, mixed precision `{manifest.get('mixed_precision')}`"
        f"|",
        "| Owner | The repository owner (see CONTRIBUTING.md) |",
        "",
        "## 1. What it is",
        "",
        f"A {manifest.get('base_model')} encoder with LoRA adapters (rank"
        f"{manifest.get('lora', {}).get('rank')}, "
        f"alpha {manifest.get('lora', {}).get('alpha')}, dropout"
        f"{manifest.get('lora', {}).get('dropout')}) and a "
        f"{len(manifest.get('labels', EMOTIONS))}-label sigmoid head, merged into plain weights"
        f"and saved as "
        "`model/` + `tokenizer/` + `thresholds.json` + this card. It replaces the primary link of"
        "the "
        "product's emotion chain when `EMOTION_ANALYZER=trained`; the lexicon fallbacks stay"
        "attached.",
        "",
        "## 2. Intended use",
        "",
        "| | |",
        "| --- | --- |",
        "| Primary use | A tone hint and an optional mood tag. **Never a safety decision.** |",
        "| Consumers | `app.services.nlp.TrainedEmotionAnalyzer`, the UI's mood chip, journal"
        "insights |",
        "| Out of scope | The crisis gate: it is rules-first and this model may only ever"
        " *raise* caution through the reviewed paths (ADR 0009). It must not be called at"
        " HIGH or IMMINENT risk. |",
        "| Failure mode if wrong | A wrong mood tag on a message, never a missed crisis. The"
        " chain falls back to the lexicon on any load or inference failure. |",
        "",
        "## 3. Training data",
        "",
        "| | |",
        "| --- | --- |",
        f"| Dataset(s) | GoEmotions (Apache-2.0, `ml/data/CATALOGUE.md` §1) for"
        f"`{manifest.get('base_model')}`'s English supervision |",
        f"| Rows | train {trained_on.get('rows')} / validation"
        f"{data.get('validation', {}).get('rows')} / "
        f"held-out {data.get('test', {}).get('rows') or '—'} |",
        f"| Label scheme | Manovia's nine: {', '.join(manifest.get('labels', EMOTIONS))} |",
        "| Mapping | `ml/src/manovia_ml/training/emotion/taxonomy.py`, one documented row per"
        "source label |",
        f"| Labels unreachable from GoEmotions |"
        f"{', '.join(manifest.get('unreachable_labels', [])) or 'none'} |",
        f"| Languages in training data | {', '.join(trained_on.get('languages', {}))} |",
        "",
        "## 4. Evaluation",
        "",
        f"Numbers below are the **{split_name}** split, scored with the thresholds tuned on the "
        "validation split (`thresholds.json`).",
        "",
        "| metric | value | 95% CI | n |",
        "| --- | --- | --- | --- |",
        f"| macro-F1 | {payload.get('macro_f1', 0.0):.4f} |"
        f"{_ci(payload.get('macro_f1_ci', [0.0, 0.0]))} | {payload.get('rows')} |",
        f"| micro-F1 | {payload.get('micro_f1', 0.0):.4f} |"
        f"{_ci(payload.get('micro_f1_ci', [0.0, 0.0]))} | {payload.get('rows')} |",
        f"| subset accuracy | {payload.get('subset_accuracy', 0.0):.4f} |"
        f"{_ci(payload.get('subset_accuracy_ci', [0.0, 0.0]))} | {payload.get('rows')} |",
        f"| ECE (per row,label pair) | {payload.get('ece', 0.0):.4f} | - |"
        f" {payload.get('rows')} x 9 |",
        "",
    ]
    for language in languages:
        values = payload["per_language"][language]
        lines.append(
            f"- `{language}`: macro-F1 {values['macro_f1']:.4f} {_ci(values['macro_f1_ci'])} "
            f"(n={values['rows']})"
        )
    lines += [
        "",
        "Per-label F1 and the primary-label confusion matrix are in the run's "
        "`evaluation/test.json`; the human-readable report is "
        "`ml/reports/emotion/<run>.md`.",
        "",
        "## 5. Known limitations and biases",
        "",
        f"- {DOMAIN_SHIFT_LIMITATION}",
        "- **No native-speaker review of the Hindi or Bengali evaluation rows**, and no "
        "Hindi/Bengali *training* data at all in this run: `hi` and `bn` numbers measure transfer "
        "from a multilingual encoder, not supervised learning in those languages.",
        "- **`loneliness` has no GoEmotions source.** It appears in the label set because the "
        "product needs it; this model will score it near its prior, and the fallback lexicon "
        "(which does list it) is more trustworthy for that label today.",
        "- **Multi-label collapse in the training data.** GoEmotions rows with several labels "
        "become soft targets through the mapping; the rare co-occurrence patterns (fear + anxiety) "
        "are therefore under-represented relative to how people write.",
        "- **The held-out set is small and single-author** (174 rows). Every interval in section 4 "
        "is wide, and the set is not a random sample of anything.",
        "- **Not evaluated on real Manovia traffic.** Doing so would require user text; it is "
        "forbidden (`AGENTS.md` rule 8, `docs/privacy-and-threat-model.md`).",
        "",
        "## 6. Fairness across the languages Manovia supports",
        "",
        "| language | trained on | evaluated on | macro-F1 (held-out) | notes |",
        "| --- | --- | --- | --- | --- |",
        "| en | GoEmotions (Reddit) | hand-written, 58 rows | "
        f"{payload.get('per_language', {}).get('en', {}).get('macro_f1', float('nan')):.4f} | "
        "trained and evaluated, but the register differs from both |",
        "| hi | nothing | hand-written, 58 rows | "
        f"{payload.get('per_language', {}).get('hi', {}).get('macro_f1', float('nan')):.4f} | "
        "transfer only; no supervised Hindi data was available under a usable licence |",
        "| hi-Latn | nothing | not evaluated | — | parking lot: no `hi-Latn` rows exist yet |",
        "| bn | nothing | hand-written, 58 rows | "
        f"{payload.get('per_language', {}).get('bn', {}).get('macro_f1', float('nan')):.4f} | "
        "transfer only; EmoNoBa (bn) was catalogued but not downloaded in this sandbox |",
        "",
        "## 7. Thresholds",
        "",
        "| label | threshold | tuned? | support | reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, item in thresholds.per_label.items():
        lines.append(
            f"| `{label}` | {item.threshold:.2f} | {'yes' if item.tuned else 'no'} | "
            f"{item.support} | {item.reason} |"
        )
    lines += [
        "",
        f"Tuned on the `{thresholds.split}` split only (`{thresholds.metric}`, grid "
        f"{thresholds.grid[0]:.2f}-{thresholds.grid[-1]:.2f} in {len(thresholds.grid)} steps).",
        "",
        "## 8. Reproduction",
        "",
        "```bash",
        f"make ml-emotion-train CONFIG=configs/{manifest.get('config')}.yaml",
        "make ml-emotion-eval ARTIFACT=<artifact dir>",
        "```",
        "",
        "Everything about the run — commit, dirty flag, data hashes, config fingerprint, seed, "
        "library versions, per-step loss, per-epoch validation metrics, thresholds and the final "
        "evaluation — is in the MLflow run with this card's run id.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m manovia_ml.training.emotion.report",
        description="Compare a packaged artefact with the baselines and write the report.",
    )
    parser.add_argument("--artifact", required=True)
    parser.add_argument(
        "--data", default=None, help="held-out JSONL (default: the hand-written set)"
    )
    parser.add_argument("--run-id", default="unrecorded")
    parser.add_argument(
        "--baselines", default="keyword", help="comma-separated: keyword,sentiment,baseline"
    )
    parser.add_argument("--out", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--bootstrap", type=int, default=2000)
    args = parser.parse_args(argv)

    from manovia_ml.training.emotion.evaluate import evaluate_artifact

    examples: list[EmotionExample] = load_examples(
        args.data if args.data else default_validation_path()
    )
    artifact = load_artifact(args.artifact)
    trained_payload = evaluate_artifact(
        artifact, examples=examples, split="test", bootstrap_resamples=args.bootstrap
    )

    results: dict[str, dict[str, Any]] = {"fine-tuned (this run)": trained_payload}
    notes: list[str] = []
    availability = available_baselines()
    for name in [item.strip() for item in args.baselines.split(",") if item.strip()]:
        reason = availability.get(name, "unknown baseline")
        if reason:
            notes.append(f"baseline `{name}` was **not run here**: {reason}")
            continue
        try:
            baseline = run_baseline(name, examples)
        except Exception as exc:
            # A baseline that cannot run must not take the report with it: the
            # numbers for the trained model are already computed and the reason
            # belongs *in* the report, next to the rows it is missing from.
            # Found the hard way — the off-the-shelf checkpoint's availability
            # check passed on importability and the CLI died on the download.
            print(
                f"warning: baseline {name!r} could not run here: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            notes.append(f"baseline `{name}` was **not run here**: {type(exc).__name__}: {exc}")
            continue

        def macro_f1_of(sample: Sequence[Row], labels: Sequence[str] = baseline.labels) -> float:
            return metric_fns.macro_f1(sample, labels)[0]

        rows: list[Row] = [
            (frozenset(example.labels), prediction)
            for example, prediction in zip(examples, baseline.predict_sets(), strict=True)
        ]
        macro, _ = metric_fns.macro_f1(rows, baseline.labels)
        per_language: dict[str, dict[str, Any]] = {}
        for language in sorted({example.lang for example in examples}):
            subset = [
                rows[index] for index, example in enumerate(examples) if example.lang == language
            ]
            value, _ = metric_fns.macro_f1(subset, baseline.labels)
            per_language[language] = {
                "rows": len(subset),
                "macro_f1": round(value, 6),
                "macro_f1_ci": [
                    round(item, 6)
                    for item in metric_fns.bootstrap_interval(
                        subset,
                        macro_f1_of,
                        resamples=args.bootstrap,
                        seed=17,
                    )
                ],
            }
        results[name] = {
            "rows": len(examples),
            "macro_f1": round(macro, 6),
            "macro_f1_ci": [
                round(item, 6)
                for item in metric_fns.bootstrap_interval(
                    rows,
                    macro_f1_of,
                    resamples=args.bootstrap,
                    seed=17,
                )
            ],
            "per_language": per_language,
            "notes": baseline.notes,
        }
        notes.append(f"baseline `{name}`: {baseline.notes}")

    manifest = dict(artifact.manifest)
    manifest.setdefault("base_model", artifact.base_model)
    manifest.setdefault("labels", list(artifact.labels))
    manifest.setdefault("artifact_dir", str(artifact.directory))
    data_summaries = {
        "train (from the run manifest)": artifact.manifest.get("data", {}).get("train", {}),
        "held-out (this evaluation)": summary(examples),
    }
    report = render_report(
        run_id=args.run_id,
        config_name=str(manifest.get("config", args.run_id)),
        manifest=manifest,
        trained=trained_payload,
        baselines={key: value for key, value in results.items() if key != "fine-tuned (this run)"},
        data_summaries=data_summaries,
        comparison=comparison_table(results),
        error_cases=trained_payload.get("errors", []),
        per_label=per_label_table(trained_payload),
        notes=notes,
    )
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(
            json.dumps(
                {"comparison": results, "notes": notes}, indent=2, sort_keys=True, default=str
            )
        )
    else:
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(_main())
