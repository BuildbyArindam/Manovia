"""Day 45 meditation eval: 30 generated scripts, rubric-checked.

Deterministic and offline by construction: the generator runs with the
Fake provider and LLM passages disabled (the curated-only path is the
production default anyway), and every script is re-checked here with an
*independent* pass of the deterministic linter and the rule-based output
guard — the checks the generator performed internally are not trusted on
faith.

Rubric per script
-----------------
* generation succeeds;
* the safety frame is present: preface + stop cue (first sections);
* driving context: eyes-open forced, pull-over warning present;
* forbidden content: 0 trance inductions, 0 medical claims, 0 promises,
  0 eyes-closed instructions while moving (or eyes-open scripts);
* word count within the lint's honest bounds (0.75*budget, or the
  curated-pool maximum when the pool is thinner — the estimate is
  disclosed, never padded);
* estimated duration sane for the requested duration;
* deterministic: same request + seed regenerates the same content_hash;
* the independent output-guard pass comes back clean.

Usage (from the repository root)::

    backend/.venv/bin/python evals/meditation/run_meditation_eval.py
    backend/.venv/bin/python evals/meditation/run_meditation_eval.py --json-only

Exit code 0 = every check passed. A JSON report is written to
``evals/reports/<date>/meditation_eval.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.llm.fake_provider import FakeLLMProvider  # noqa: E402
from app.services.meditation.generate import (  # noqa: E402
    MeditationScriptGenerator,
    _curated_max_words,
    _plan,
)
from app.services.meditation.lint import LintCategory, lint_script  # noqa: E402
from app.services.meditation.types import MeditationRequest  # noqa: E402
from app.services.safety.base import RiskLevel  # noqa: E402
from app.services.safety.output_guard import RuleBasedOutputGuard  # noqa: E402

THEMES = ["morning", "focus", "stress-release", "calm", "sleep", "gratitude"]
LANGUAGES = ["en", "hi", "bn"]

#: The 30-script matrix: themes x languages, a duration sweep, the moving
#: contexts, voice speeds, eyes-closed seated variants, and one auto theme
#: resolved from the hour.
MATRIX: tuple[dict[str, Any], ...] = (
    [
        {
            "theme": theme,
            "language": language,
            "duration_seconds": 180,
            "context": "seated",
            "eyes_open": True,
        }
        for theme in THEMES
        for language in LANGUAGES
    ]
    + [
        {"theme": "calm", "language": "en", "duration_seconds": 120, "context": "seated", "eyes_open": True},
        {"theme": "calm", "language": "en", "duration_seconds": 300, "context": "seated", "eyes_open": True},
        {"theme": "sleep", "language": "en", "duration_seconds": 900, "context": "seated", "eyes_open": False},
        {"theme": "focus", "language": "en", "duration_seconds": 600, "context": "seated", "eyes_open": True},
        {"theme": "focus", "language": "en", "duration_seconds": 180, "context": "driving", "eyes_open": True},
        {"theme": "calm", "language": "en", "duration_seconds": 120, "context": "walking", "eyes_open": True},
        {"theme": "stress-release", "language": "hi", "duration_seconds": 180, "context": "driving", "eyes_open": True},
        {"theme": "calm", "language": "en", "duration_seconds": 180, "voice_speed": 0.85, "context": "seated", "eyes_open": True},
        {"theme": "sleep", "language": "bn", "duration_seconds": 180, "voice_speed": 1.15, "context": "seated", "eyes_open": True},
        {"theme": "sleep", "language": "en", "duration_seconds": 300, "context": "seated", "eyes_open": False, "preferences": ["quiet", "night"]},
        {"theme": "gratitude", "language": "hi", "duration_seconds": 180, "context": "seated", "eyes_open": False, "preferences": ["warmth"]},
        {"theme": "auto", "language": "en", "duration_seconds": 180, "hour_of_day": 8, "context": "seated", "eyes_open": True},
    ]
)

assert len(MATRIX) == 30, "the eval must cover exactly 30 scripts"

SEED = 45  # fixed: the eval is reproducible


@dataclass
class ScriptResult:
    index: int
    request: dict[str, Any]
    passed: bool
    failures: list[str]
    resolved_theme: str
    language: str
    context: str
    eyes_open: bool
    word_count: int
    estimated_duration_s: float
    content_hash: str
    lint_by_category: dict[str, int]
    lint_word_count: int
    sections_first: list[str]


async def evaluate_one(
    generator: MeditationScriptGenerator,
    guard: RuleBasedOutputGuard,
    index: int,
    spec: dict[str, Any],
) -> ScriptResult:
    request = MeditationRequest(seed=SEED, **spec)
    failures: list[str] = []

    script = await generator.generate(request)

    # --- the safety frame ------------------------------------------------
    if not script.safety.safety_preface:
        failures.append("missing safety preface flag")
    if not script.safety.stop_cue:
        failures.append("missing stop cue flag")
    roles = [s.role for s in script.sections]
    if roles[:1] != ["preface"]:
        failures.append(f"preface is not the first section ({roles[:1]})")
    if "stop" not in roles[:3]:
        failures.append("stop cue is not among the first three sections")
    if script.context == "driving":
        if not script.safety.driving_warning:
            failures.append("driving warning missing while context=driving")
        if not script.eyes_open:
            failures.append("driving script is not eyes-open")

    # --- the independent lint pass ----------------------------------------
    # The honest floor (ADR 0045): a script can never be longer than its
    # curated pool, so long offline requests are legitimately shorter; the
    # estimate is disclosed, never padded.
    plan = _plan(request)
    floor_words = min(
        int(plan.budget * 0.75),
        _curated_max_words(request.language, script.resolved_theme, request.context, request.eyes_open),
    )
    report = lint_script(
        script.transcript,
        language=request.language,
        context=request.context,
        eyes_open=request.eyes_open,
        word_budget=plan.budget,
        min_achievable_words=floor_words,
    )
    if not report.passed:
        failures.append(f"independent lint failed: {report.by_category()}")
    by_cat = report.by_category()
    for forbidden in (
        LintCategory.TRANCE_INDUCTION,
        LintCategory.MEDICAL_CLAIM,
        LintCategory.PROMISE,
    ):
        if report.count(forbidden) > 0:
            failures.append(f"forbidden content: {forbidden.value}={report.count(forbidden)}")
    if request.context in {"walking", "driving"} and report.count(LintCategory.EYES_CLOSED) > 0:
        failures.append(f"eyes-closed instructions while {request.context}")
    if request.eyes_open and report.count(LintCategory.EYES_CLOSED) > 0:
        failures.append("eyes-closed instructions in an eyes-open script")

    # --- the independent guard pass ---------------------------------------
    verdict = await guard.check(
        script.transcript, risk=RiskLevel.NONE, expected_language=request.language
    )
    if not verdict.passed:
        failures.append(f"output guard failed: {verdict.category.value if verdict.category else '?'}")

    # --- honest sizing -----------------------------------------------------
    if script.estimated_duration_s > 2.0 * request.duration_seconds:
        failures.append("estimated duration exceeds twice the request (padded?)")
    if script.estimated_duration_s < 0.35 * request.duration_seconds:
        failures.append(
            "estimated duration below 35% of the request even with the honest floor: "
            f"{script.estimated_duration_s:.0f}s for {request.duration_seconds}s"
        )
    if script.word_count < floor_words:
        failures.append(f"word count {script.word_count} under the honest floor {floor_words}")
    if script.word_count != len(script.transcript.split()):
        failures.append("word_count disagrees with the transcript")

    # --- determinism ---------------------------------------------------------
    again = await generator.generate(request)
    if again.content_hash != script.content_hash:
        failures.append("same request + seed produced a different content_hash")

    return ScriptResult(
        index=index,
        request={k: v for k, v in spec.items()},
        passed=not failures,
        failures=failures,
        resolved_theme=script.resolved_theme,
        language=script.language,
        context=script.context,
        eyes_open=script.eyes_open,
        word_count=script.word_count,
        estimated_duration_s=round(script.estimated_duration_s, 1),
        content_hash=script.content_hash[:16],
        lint_by_category=by_cat,
        lint_word_count=report.word_count,
        sections_first=roles[:3],
    )


async def main_async() -> int:
    provider = FakeLLMProvider()
    generator = MeditationScriptGenerator(
        provider,
        allow_llm_passages=False,  # the production default; the curated path
        output_guard=None,
    )
    guard = RuleBasedOutputGuard(max_words=10_000)

    results = [
        await evaluate_one(generator, guard, i, spec)
        for i, spec in enumerate(MATRIX, start=1)
    ]

    total_failures = sum(len(r.failures) for r in results)
    total_forbidden = sum(
        r.lint_by_category.get(cat.value, 0)
        for r in results
        for cat in (
            LintCategory.TRANCE_INDUCTION,
            LintCategory.MEDICAL_CLAIM,
            LintCategory.PROMISE,
            LintCategory.EYES_CLOSED,
        )
    )

    report = {
        "eval": "meditation",
        "day": 45,
        "date": date.today().isoformat(),
        "scripts": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "total_findings": total_failures,
        "forbidden_content_total": total_forbidden,
        "llm_judge": "skipped (offline; deterministic lints only)",
        "results": [asdict(r) for r in results],
    }

    reports_dir = REPO_ROOT / "evals" / "reports" / date.today().isoformat()
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "meditation_eval.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if not sys.argv[1:] or "--json-only" not in sys.argv:
        print(f"Day 45 meditation eval — {report['scripts']} scripts")
        print("=" * 78)
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            print(
                f"[{status}] #{r.index:<2} {r.request.get('theme', '?'):<15} "
                f"{r.language} {r.request.get('duration_seconds', 180):>3}s "
                f"{r.context:<7} words={r.word_count:<4} "
                f"est={r.estimated_duration_s:>5}s  {r.content_hash}"
            )
            for failure in r.failures:
                print(f"       - {failure}")
        print("=" * 78)
        print(
            f"passed {report['passed']}/{report['scripts']} · "
            f"forbidden-content findings: {total_forbidden} · "
            f"report: {reports_dir / 'meditation_eval.json'}"
        )

    return 0 if report["passed"] == report["scripts"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-only", action="store_true", help="write the report, print nothing")
    parser.parse_args()
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
