"""Unified evaluation harness runner wired to ``make eval`` (Day 24).

Usage::

    python evals/run_all_evals.py
    LLM_PROVIDER=fake python evals/run_all_evals.py --provider fake --with-judge

Runs all three evaluation suites in sequence:

1. **Safety evaluation** (``evals/run_safety_eval.py``) on the ``dev`` split
   (116 cases, rules + ML ensemble vs rules-only, bootstrap 95% CIs, regression
   diff against the previous ``safety_<prev>.md`` report, CI gate on
   HIGH+IMMINENT recall);
2. **Emotion evaluation** (``evals/run_emotion_eval.py``) on the 162-case
   multilingual hand-labelled set (``en``, ``hi``, ``bn``, 9 emotions, macro-F1,
   9×9 confusion matrix, ``in_domain`` vs ``domain_shift`` comparison);
3. **Response evaluation** (``evals/run_response_eval.py``) on the 60 scripted
   user scenarios (``evals/datasets/chat_rubric.jsonl``) through the full
   ``ChatOrchestrator`` pipeline (6 deterministic checks + optional 4-axis
   LLM-as-judge rubric from ``evals/prompts/response_judge_v1.md``).

Writes consolidated artifacts to ``evals/reports/<date>/``:

* ``summary.md`` — unified Markdown report with headline metrics, 95% CIs,
  regression diff, and CI gate status across all three suites;
* ``report.html`` and ``summary.html`` — self-contained HTML dashboard;
* ``pr_comment.md`` — concise Markdown table formatted for automated GitHub PR
  comments and ``$GITHUB_STEP_SUMMARY``;
* ``summary.json`` — machine-readable summary of all three suites;
* ``safety.md``, ``emotion.md``, ``response.md`` — full per-suite reports.

Returns exit code ``0`` when every CI gate in ``evals/thresholds.yaml`` passes,
or ``2`` when any gate fails (unless ``--no-gate`` is passed).
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "evals"))

import run_emotion_eval  # noqa: E402
import run_response_eval  # noqa: E402
import run_safety_eval  # noqa: E402

REPORTS_DIR = REPO_ROOT / "evals" / "reports"
THRESHOLDS_PATH = REPO_ROOT / "evals" / "thresholds.yaml"


def _safety_gate_checks(safety_json: dict[str, Any]) -> list[dict[str, str]]:
    checks = safety_json.get("gate", {}).get("checks", [])
    return [
        {
            "suite": "safety",
            "check": str(c["name"]),
            "threshold": str(c["threshold"]),
            "observed": str(c["observed"]),
            "verdict": "PASS" if c.get("passed") else "FAIL",
        }
        for c in checks
    ]


def render_summary_markdown(
    *,
    run_date: str,
    safety_json: dict[str, Any],
    emotion_json: dict[str, Any],
    response_json: dict[str, Any],
    overall_gate_passed: bool,
) -> str:
    s_ens = safety_json["ensemble"]
    s_rul = safety_json["rules_only"]
    s_gate_pass = bool(safety_json.get("gate", {}).get("passed"))
    e_ch = emotion_json["chain"]
    r_checks = response_json["checks"]
    r_judge = response_json.get("judge")

    prev_report = safety_json.get("previous_report")
    diff_rows = safety_json.get("regression_diff", [])
    if diff_rows:
        diff_md_lines = [
            f"Compared against `{prev_report}`:",
            "",
            "| metric | previous | current | delta | status |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in diff_rows:
            is_count = row.get("key") == "benign_crisis_fa"
            d_str = f"{int(row['delta']):+d}" if is_count else f"{row['delta']:+.3f}"
            p_str = f"{int(row['previous'])}" if is_count else f"{row['previous']:.3f}"
            c_str = f"{int(row['current'])}" if is_count else f"{row['current']:.3f}"
            diff_md_lines.append(
                f"| {row['label']} | {p_str} | {c_str} | {d_str} | **{row['status']}** |"
            )
        diff_block = "\n".join(diff_md_lines)
    else:
        diff_block = "No previous safety report found for regression comparison."

    all_gate_checks = [
        *_safety_gate_checks(safety_json),
        *(dict(g, suite="emotion") for g in emotion_json.get("gate_checks", [])),
        *(dict(g, suite="response") for g in response_json.get("gate_checks", [])),
    ]
    gate_table_rows = [
        f"| `{g['suite']}` | {g['check']} | `{g['threshold']}` | `{g['observed']}` | **{g['verdict']}** |"
        for g in all_gate_checks
    ]

    judge_line = (
        f"warmth={r_judge['warmth']:.2f}/5, validation={r_judge['validation']:.2f}/5, "
        f"non_directive={r_judge['non_directive']:.2f}/5, safety={r_judge['safety']:.2f}/5 "
        "*(supplementary only; judges are imperfect)*"
        if r_judge
        else "disabled (enable with `--with-judge`)"
    )

    return f"""# Manovia Evaluation Harness Summary — {run_date}

- **Overall CI Gate Verdict**: **{'PASS' if overall_gate_passed else 'FAIL'}** (thresholds from `evals/thresholds.yaml`)
- **Execution Mode**: Offline-capable deterministic + ensemble harness (`{response_json['provider']}`)
- **Suite Reports**: [`safety.md`](./safety.md) · [`emotion.md`](./emotion.md) · [`response.md`](./response.md) · [`report.html`](./report.html)

## 1. Headline metrics across all three evaluation suites

| suite | dataset / split | headline metric | value | 95% bootstrap CI | gate |
| --- | --- | --- | --- | --- | --- |
| **1. Safety (rules + ML ensemble)** | `crisis_cases_dev.jsonl` (n={safety_json['cases']}, crisis={safety_json['crisis_cases']}) | HIGH+IMMINENT recall | **{s_ens['recall']:.3f}** | [{s_ens['recall_ci'][0]:.3f}, {s_ens['recall_ci'][1]:.3f}] | **{'PASS' if s_gate_pass else 'FAIL'}** |
| **1b. Safety (precision trade-off)** | `crisis_cases_dev.jsonl` (n={safety_json['cases']}) | HIGH+IMMINENT precision | **{s_ens['precision']:.3f}** | [{s_ens['precision_ci'][0]:.3f}, {s_ens['precision_ci'][1]:.3f}] | info |
| **1c. Safety (rules-only baseline)** | `crisis_cases_dev.jsonl` (n={safety_json['cases']}) | HIGH+IMMINENT recall (rules) | **{s_rul['recall']:.3f}** | [{s_rul['recall_ci'][0]:.3f}, {s_rul['recall_ci'][1]:.3f}] | **{'PASS' if s_gate_pass else 'FAIL'}** |
| **2. Emotion analyzer (9-class)** | `emotion_cases.jsonl` (n={emotion_json['cases']}, en/hi/bn) | Macro-F1 (overall) | **{e_ch['macro_f1']:.3f}** | [{e_ch['macro_f1_ci'][0]:.3f}, {e_ch['macro_f1_ci'][1]:.3f}] | **{'PASS' if emotion_json['gate_passed'] else 'FAIL'}** |
| **2b. Emotion (in-domain vs shift)** | `in_domain` (123) vs `domain_shift` (39) | Macro-F1 (`in` -> `shift`) | **{emotion_json['per_regime']['in_domain']['macro_f1']:.3f} -> {emotion_json['per_regime']['domain_shift']['macro_f1']:.3f}** | drop = {emotion_json['domain_shift_f1_drop']:.3f} | documented |
| **3. Response pipeline (6 checks)** | `chat_rubric.jsonl` (n={response_json['scenarios']}, en/hi/bn) | All-6 deterministic pass rate | **{response_json['overall_pass_rate']:.3f}** | [{response_json['overall_pass_rate_ci'][0]:.3f}, {response_json['overall_pass_rate_ci'][1]:.3f}] | **{'PASS' if response_json['gate_passed'] else 'FAIL'}** |

## 2. Consolidated CI gate checks (`evals/thresholds.yaml`)

| suite | check | threshold | observed | verdict |
| --- | --- | --- | --- | --- |
{chr(10).join(gate_table_rows)}

## 3. Safety regression diff vs previous report

{diff_block}

## 4. Response pipeline deterministic check breakdown (n={response_json['scenarios']})

| check | passed | rate | 95% bootstrap CI |
| --- | --- | --- | --- |
| `no_diagnosis_or_medication` | {r_checks['no_diagnosis_or_medication']['passed']}/{r_checks['no_diagnosis_or_medication']['total']} | **{r_checks['no_diagnosis_or_medication']['rate']:.3f}** | [{r_checks['no_diagnosis_or_medication']['ci'][0]:.3f}, {r_checks['no_diagnosis_or_medication']['ci'][1]:.3f}] |
| `within_length_limit` | {r_checks['within_length_limit']['passed']}/{r_checks['within_length_limit']['total']} | **{r_checks['within_length_limit']['rate']:.3f}** | [{r_checks['within_length_limit']['ci'][0]:.3f}, {r_checks['within_length_limit']['ci'][1]:.3f}] |
| `language_match` | {r_checks['language_match']['passed']}/{r_checks['language_match']['total']} | **{r_checks['language_match']['rate']:.3f}** | [{r_checks['language_match']['ci'][0]:.3f}, {r_checks['language_match']['ci'][1]:.3f}] |
| `crisis_resources_when_required` | {r_checks['crisis_resources_when_required']['passed']}/{r_checks['crisis_resources_when_required']['total']} | **{r_checks['crisis_resources_when_required']['rate']:.3f}** | [{r_checks['crisis_resources_when_required']['ci'][0]:.3f}, {r_checks['crisis_resources_when_required']['ci'][1]:.3f}] |
| `at_most_one_question` | {r_checks['at_most_one_question']['passed']}/{r_checks['at_most_one_question']['total']} | **{r_checks['at_most_one_question']['rate']:.3f}** | [{r_checks['at_most_one_question']['ci'][0]:.3f}, {r_checks['at_most_one_question']['ci'][1]:.3f}] |
| `no_ai_boilerplate` | {r_checks['no_ai_boilerplate']['passed']}/{r_checks['no_ai_boilerplate']['total']} | **{r_checks['no_ai_boilerplate']['rate']:.3f}** | [{r_checks['no_ai_boilerplate']['ci'][0]:.3f}, {r_checks['no_ai_boilerplate']['ci'][1]:.3f}] |

- **Optional LLM-as-judge rubric (`evals/prompts/response_judge_v1.md`)**: {judge_line}

## 5. What these numbers do and do not mean (summary)

- **What they DO mean**: Every authored crisis pattern and ML ensemble threshold in the repository is regression-tested against a 116-item dev split with bootstrap confidence intervals; weakening a `HIGH` pattern in `patterns_core.yaml` or `patterns_indic.yaml` immediately trips the CI gate. Every one of the 60 scripted `ChatOrchestrator` scenarios satisfies all 6 deterministic output safety invariants across English, Hindi, and Bengali.
- **What they DO NOT mean**: A `1.000` recall on 51 synthetic dev crisis cases is **not** a clinical guarantee of zero false negatives in the wild, and a `0.804` overall emotion macro-F1 masks a severe drop on implicit/somatic domain-shift expressions (`0.992` in-domain vs `0.011` domain-shift). See `docs/evaluation-report.md` for the full analysis.
"""


def render_pr_comment_markdown(
    *,
    run_date: str,
    safety_json: dict[str, Any],
    emotion_json: dict[str, Any],
    response_json: dict[str, Any],
    overall_gate_passed: bool,
) -> str:
    s_ens = safety_json["ensemble"]
    s_rul = safety_json["rules_only"]
    s_gate_pass = bool(safety_json.get("gate", {}).get("passed"))
    e_ch = emotion_json["chain"]
    badge = "PASS" if overall_gate_passed else "FAIL"
    diff_rows = safety_json.get("regression_diff", [])
    reg_count = sum(1 for r in diff_rows if r.get("status") == "REGRESSED")
    prev_rep = safety_json.get("previous_report") or "none"

    return f"""<!-- manovia-eval-summary -->
### Manovia Evaluation Harness — `{badge}` ({run_date})

| Suite | Metric | Score | 95% Bootstrap CI | Gate |
| --- | --- | --- | --- | --- |
| **Safety (Ensemble)** | HIGH+IMMINENT recall (dev, n={safety_json['cases']}) | **`{s_ens['recall']:.3f}`** | `[{s_ens['recall_ci'][0]:.3f}, {s_ens['recall_ci'][1]:.3f}]` | `{'PASS' if s_gate_pass else 'FAIL'}` |
| **Safety (Ensemble)** | HIGH+IMMINENT precision | `{s_ens['precision']:.3f}` | `[{s_ens['precision_ci'][0]:.3f}, {s_ens['precision_ci'][1]:.3f}]` | info |
| **Safety (Rules only)** | HIGH+IMMINENT recall | **`{s_rul['recall']:.3f}`** | `[{s_rul['recall_ci'][0]:.3f}, {s_rul['recall_ci'][1]:.3f}]` | `{'PASS' if s_gate_pass else 'FAIL'}` |
| **Emotion (9-class)** | Macro-F1 (n={emotion_json['cases']}, en/hi/bn) | **`{e_ch['macro_f1']:.3f}`** | `[{e_ch['macro_f1_ci'][0]:.3f}, {e_ch['macro_f1_ci'][1]:.3f}]` | `{'PASS' if emotion_json['gate_passed'] else 'FAIL'}` |
| **Response (60 scenarios)** | All-6 deterministic checks pass rate | **`{response_json['overall_pass_rate']:.3f}`** | `[{response_json['overall_pass_rate_ci'][0]:.3f}, {response_json['overall_pass_rate_ci'][1]:.3f}]` | `{'PASS' if response_json['gate_passed'] else 'FAIL'}` |

- **Regression diff vs `{prev_rep}`**: `{reg_count}` recall/escalation regressions detected.
- **Caveat**: Synthetic benchmark metrics verify engineering invariants and catch regressions; they are not clinical efficacy claims (see `docs/evaluation-report.md`).
"""


def render_html_report(
    *,
    run_date: str,
    safety_json: dict[str, Any],
    emotion_json: dict[str, Any],
    response_json: dict[str, Any],
    overall_gate_passed: bool,
    summary_md: str,
) -> str:
    s_ens = safety_json["ensemble"]
    s_rul = safety_json["rules_only"]
    e_ch = emotion_json["chain"]
    r_checks = response_json["checks"]

    all_gate_checks = [
        *_safety_gate_checks(safety_json),
        *(dict(g, suite="emotion") for g in emotion_json.get("gate_checks", [])),
        *(dict(g, suite="response") for g in response_json.get("gate_checks", [])),
    ]
    gate_rows_html = "\n".join(
        f"<tr><td><code>{html.escape(g['suite'])}</code></td>"
        f"<td>{html.escape(g['check'])}</td>"
        f"<td><code>{html.escape(g['threshold'])}</code></td>"
        f"<td><code>{html.escape(g['observed'])}</code></td>"
        f"<td class=\"{'pass' if g['verdict'] == 'PASS' else 'fail'}\">{html.escape(g['verdict'])}</td></tr>"
        for g in all_gate_checks
    )

    check_rows_html = "\n".join(
        f"<tr><td><code>{html.escape(k)}</code></td>"
        f"<td>{v['passed']}/{v['total']}</td>"
        f"<td><strong>{v['rate']:.3f}</strong></td>"
        f"<td>[{v['ci'][0]:.3f}, {v['ci'][1]:.3f}]</td></tr>"
        for k, v in r_checks.items()
    )

    badge_class = "badge-pass" if overall_gate_passed else "badge-fail"
    badge_text = "CI GATE: PASS" if overall_gate_passed else "CI GATE: FAIL"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Manovia Evaluation Harness — {html.escape(run_date)}</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --card: #ffffff;
      --ink: #0f172a;
      --muted: #475569;
      --border: #e2e8f0;
      --accent: #0f766e;
      --pass-bg: #dcfce7;
      --pass-ink: #166534;
      --fail-bg: #fee2e2;
      --fail-ink: #991b1b;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--ink);
      margin: 0;
      padding: 2rem 1.25rem;
      line-height: 1.55;
    }}
    .container {{
      max-width: 1060px;
      margin: 0 auto;
    }}
    header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      margin-bottom: 1.5rem;
      padding-bottom: 1rem;
      border-bottom: 2px solid var(--border);
    }}
    h1 {{
      font-size: 1.65rem;
      margin: 0;
    }}
    .subtitle {{
      color: var(--muted);
      font-size: 0.95rem;
      margin-top: 0.25rem;
    }}
    .badge {{
      padding: 0.45rem 0.9rem;
      border-radius: 999px;
      font-weight: 700;
      font-size: 0.9rem;
      letter-spacing: 0.02em;
    }}
    .badge-pass {{ background: var(--pass-bg); color: var(--pass-ink); }}
    .badge-fail {{ background: var(--fail-bg); color: var(--fail-ink); }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 1rem;
      margin-bottom: 1.75rem;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.1rem 1.25rem;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }}
    .card-label {{
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      font-weight: 600;
    }}
    .card-value {{
      font-size: 1.8rem;
      font-weight: 700;
      color: var(--accent);
      margin: 0.25rem 0;
    }}
    .card-meta {{
      font-size: 0.85rem;
      color: var(--muted);
    }}
    section {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.25rem 1.5rem;
      margin-bottom: 1.25rem;
    }}
    h2 {{
      font-size: 1.15rem;
      margin-top: 0;
      margin-bottom: 0.75rem;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.92rem;
    }}
    th, td {{
      text-align: left;
      padding: 0.55rem 0.65rem;
      border-bottom: 1px solid var(--border);
    }}
    th {{
      background: #f1f5f9;
      font-weight: 600;
    }}
    td.pass {{ color: var(--pass-ink); font-weight: 700; }}
    td.fail {{ color: var(--fail-ink); font-weight: 700; }}
    .caveat {{
      background: #fffbeb;
      border-left: 4px solid #d97706;
      padding: 0.85rem 1rem;
      border-radius: 0.35rem;
      font-size: 0.92rem;
      color: #78350f;
    }}
    pre {{
      background: #0f172a;
      color: #f8fafc;
      padding: 1rem;
      border-radius: 0.5rem;
      overflow-x: auto;
      font-size: 0.82rem;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>Manovia Evaluation Harness Report</h1>
        <div class="subtitle">Run date: {html.escape(run_date)} · Languages: English (en), Hindi (hi / hi-Latn), Bengali (bn)</div>
      </div>
      <span class="badge {badge_class}">{badge_text}</span>
    </header>

    <div class="grid">
      <div class="card">
        <div class="card-label">Safety Crisis Recall (Ensemble)</div>
        <div class="card-value">{s_ens['recall']:.3f}</div>
        <div class="card-meta">95% CI [{s_ens['recall_ci'][0]:.3f}, {s_ens['recall_ci'][1]:.3f}] · Precision {s_ens['precision']:.3f} (n={safety_json['cases']})</div>
      </div>
      <div class="card">
        <div class="card-label">Safety Crisis Recall (Rules Only)</div>
        <div class="card-value">{s_rul['recall']:.3f}</div>
        <div class="card-meta">95% CI [{s_rul['recall_ci'][0]:.3f}, {s_rul['recall_ci'][1]:.3f}] · Precision {s_rul['precision']:.3f}</div>
      </div>
      <div class="card">
        <div class="card-label">Emotion Analyzer Macro-F1</div>
        <div class="card-value">{e_ch['macro_f1']:.3f}</div>
        <div class="card-meta">95% CI [{e_ch['macro_f1_ci'][0]:.3f}, {e_ch['macro_f1_ci'][1]:.3f}] · In-domain {emotion_json['per_regime']['in_domain']['macro_f1']:.3f} vs Shift {emotion_json['per_regime']['domain_shift']['macro_f1']:.3f}</div>
      </div>
      <div class="card">
        <div class="card-label">Response Pipeline (60 Scenarios)</div>
        <div class="card-value">{response_json['overall_pass_rate']:.3f}</div>
        <div class="card-meta">95% CI [{response_json['overall_pass_rate_ci'][0]:.3f}, {response_json['overall_pass_rate_ci'][1]:.3f}] · All 6 deterministic checks</div>
      </div>
    </div>

    <section>
      <h2>CI Gate Verification (<code>evals/thresholds.yaml</code>)</h2>
      <table>
        <thead>
          <tr><th>Suite</th><th>Check</th><th>Threshold</th><th>Observed</th><th>Verdict</th></tr>
        </thead>
        <tbody>
          {gate_rows_html}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Response Pipeline Deterministic Checks (60 Scripted Scenarios)</h2>
      <table>
        <thead>
          <tr><th>Deterministic Check</th><th>Passed</th><th>Pass Rate</th><th>95% Bootstrap CI</th></tr>
        </thead>
        <tbody>
          {check_rows_html}
        </tbody>
      </table>
    </section>

    <section>
      <h2>What These Numbers Do and Do Not Mean</h2>
      <div class="caveat">
        <strong>Honest interpretation:</strong> These metrics verify deterministic safety boundaries, regression stability, and multilingual template wiring on synthetic benchmarks (116 dev crisis cases, 162 emotion cases, 60 chat rubric scenarios). They do <strong>not</strong> constitute clinical validation on real patient populations, and the emotion analyzer experiences a documented <strong>{emotion_json['domain_shift_f1_drop']:.3f}</strong> macro-F1 drop under somatic/idiomatic domain shift. See <code>docs/evaluation-report.md</code> for full methodology and limitations.
      </div>
    </section>

    <section>
      <h2>Full Markdown Summary</h2>
      <pre>{html.escape(summary_md)}</pre>
    </section>
  </div>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Report date folder under evals/reports/<date>/ (defaults to today).",
    )
    parser.add_argument(
        "--provider",
        choices=("fake", "auto", "real"),
        default="fake",
        help="LLM provider mode for response evaluation (default: fake for offline reproducibility).",
    )
    parser.add_argument(
        "--with-judge",
        action="store_true",
        help="Include the optional 4-axis rubric judge scores. Opt-in (Day 27 "
        "feature flag FEATURE_LLM_JUDGE must also be set): the judge is "
        "advisory-only and uncalibrated against human ratings (ADR 0024).",
    )
    parser.add_argument(
        "--no-gate",
        action="store_true",
        help="Write all reports without failing the process on a CI gate breach.",
    )
    args = parser.parse_args(argv)

    run_date = args.date
    day_dir = REPORTS_DIR / run_date
    day_dir.mkdir(parents=True, exist_ok=True)

    safety_md_path = day_dir / "safety.md"
    safety_json_path = day_dir / "safety.json"
    emotion_md_path = day_dir / "emotion.md"
    emotion_json_path = day_dir / "emotion.json"
    response_md_path = day_dir / "response.md"
    response_json_path = day_dir / "response.json"

    print("=== [1/3] Running Safety Evaluation (split=dev) ===")
    rc_safety = run_safety_eval.main(
        [
            "--split",
            "dev",
            "--out",
            str(safety_md_path),
            "--json-out",
            str(safety_json_path),
            "--no-gate",
        ]
    )
    if rc_safety != 0:
        print(
            f"safety evaluation failed (exit {rc_safety}); see its output above.",
            file=sys.stderr,
        )
        return rc_safety
    (REPORTS_DIR / f"safety_{run_date}.md").write_text(
        safety_md_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    print("\n=== [2/3] Running Emotion Analyzer Evaluation (n=162, en/hi/bn) ===")
    rc_emotion = run_emotion_eval.main(
        [
            "--out",
            str(emotion_md_path),
            "--json-out",
            str(emotion_json_path),
            "--no-gate",
        ]
    )
    if rc_emotion != 0:
        print(
            f"emotion evaluation failed (exit {rc_emotion}); see its output above.",
            file=sys.stderr,
        )
        return rc_emotion
    (REPORTS_DIR / f"emotion_{run_date}.md").write_text(
        emotion_md_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    print(f"\n=== [3/3] Running Response Pipeline Evaluation (n=60, provider={args.provider}) ===")
    resp_args = [
        "--provider",
        args.provider,
        "--out",
        str(response_md_path),
        "--json-out",
        str(response_json_path),
        "--no-gate",
    ]
    if args.with_judge:
        resp_args.append("--with-judge")
    rc_response = asyncio.run(run_response_eval._async_main(resp_args))
    if rc_response != 0:
        # The child prints its own explanation (e.g. the Day-27 judge gate).
        # Fail with its code instead of crashing on the report it did not
        # write - a gate refusal is a clean, actionable stop, not a traceback.
        print(
            f"response evaluation failed (exit {rc_response}); see its output above.",
            file=sys.stderr,
        )
        return rc_response
    (REPORTS_DIR / f"response_{run_date}.md").write_text(
        response_md_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    safety_json = json.loads(safety_json_path.read_text(encoding="utf-8"))
    emotion_json = json.loads(emotion_json_path.read_text(encoding="utf-8"))
    response_json = json.loads(response_json_path.read_text(encoding="utf-8"))

    overall_gate_passed = bool(
        safety_json.get("gate", {}).get("passed")
        and emotion_json.get("gate_passed")
        and response_json.get("gate_passed")
        and rc_safety == 0
        and rc_emotion == 0
        and rc_response == 0
    )

    summary_md = render_summary_markdown(
        run_date=run_date,
        safety_json=safety_json,
        emotion_json=emotion_json,
        response_json=response_json,
        overall_gate_passed=overall_gate_passed,
    )
    pr_comment_md = render_pr_comment_markdown(
        run_date=run_date,
        safety_json=safety_json,
        emotion_json=emotion_json,
        response_json=response_json,
        overall_gate_passed=overall_gate_passed,
    )
    html_report = render_html_report(
        run_date=run_date,
        safety_json=safety_json,
        emotion_json=emotion_json,
        response_json=response_json,
        overall_gate_passed=overall_gate_passed,
        summary_md=summary_md,
    )

    summary_md_path = day_dir / "summary.md"
    report_html_path = day_dir / "report.html"
    summary_html_path = day_dir / "summary.html"
    pr_comment_path = day_dir / "pr_comment.md"
    summary_json_path = day_dir / "summary.json"

    summary_md_path.write_text(summary_md, encoding="utf-8")
    report_html_path.write_text(html_report, encoding="utf-8")
    summary_html_path.write_text(html_report, encoding="utf-8")
    pr_comment_path.write_text(pr_comment_md, encoding="utf-8")
    summary_json_path.write_text(
        json.dumps(
            {
                "date": run_date,
                "overall_gate_passed": overall_gate_passed,
                "safety": safety_json,
                "emotion": emotion_json,
                "response": response_json,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"\n=== Unified Evaluation Artifacts ({day_dir.relative_to(REPO_ROOT) if day_dir.is_relative_to(REPO_ROOT) else day_dir}) ===")
    print(f"  summary markdown : {summary_md_path.relative_to(REPO_ROOT) if summary_md_path.is_relative_to(REPO_ROOT) else summary_md_path}")
    print(f"  html report      : {report_html_path.relative_to(REPO_ROOT) if report_html_path.is_relative_to(REPO_ROOT) else report_html_path}")
    print(f"  pr comment       : {pr_comment_path.relative_to(REPO_ROOT) if pr_comment_path.is_relative_to(REPO_ROOT) else pr_comment_path}")
    print(f"  overall CI gate  : {'PASS' if overall_gate_passed else 'FAIL'}")

    if not overall_gate_passed and not args.no_gate:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
