#!/usr/bin/env python3
"""Export Python's crisis plans for the combinations the browser must reproduce.

At HIGH and IMMINENT the reply is not written by a model: it is a pre-written
template, rendered with the region's emergency number, next to that region's
verified helplines (AGENTS.md safety rules 1, 4 and 6). Private Mode has to produce
*the same card* from the same content, so the card is compared rather than
eyeballed.

The plan is a pure function of four things — level, whether the message is about
somebody else, locale and region — so this exports the cross product of them:

    4 levels x 2 about-someone-else x 3 locales x 4 regions = 96 plans

Each carries the policy (`actions`, `allow_llm`, `record_event`), the fully rendered
message (every field, placeholders substituted), the helpline ids in display order,
the emergency entry and number, and the two fallback flags.
`frontend/src/features/private/crisis/escalation.test.ts` builds the same 96
assessments and asserts the browser's plan matches field by field.

USAGE
    .venv/bin/python evals/parity/export_crisis_plans.py            # write
    .venv/bin/python evals/parity/export_crisis_plans.py --check     # drift gate
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any, Final

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
BACKEND: Final = REPO_ROOT / "backend"
PARITY_DIR: Final = Path(__file__).resolve().parent
OUTPUT: Final = PARITY_DIR / "python-crisis-plans.json"

FORMAT: Final = "manovia-parity-crisis-plans/1"

#: Levels that render a message. NONE has no template, so it is not part of the
#: matrix; the browser test asserts that separately.
LEVELS: Final = ("low", "medium", "high", "imminent")
LOCALES: Final = ("en", "hi", "bn")
#: IN and US have their own emergency numbers, DEFAULT is the fallback set, and ZZ
#: is deliberately unknown so the region fallback is exercised.
REGIONS: Final = ("IN", "US", "DEFAULT", "ZZ")

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.safety.base import (  # noqa: E402
    AssessmentContext,
    RiskAssessment,
    RiskCategory,
    RiskLevel,
)
from app.services.safety.escalation import build_escalator  # noqa: E402


def assessment(level: str, about_someone_else: bool) -> RiskAssessment:
    """A synthetic assessment with exactly the flags the plan reads.

    `_about_someone_else` needs third person plus an absent speaker (or a quoted or
    fiction frame); everything else is the ordinary first-person crisis case. The
    categories are the ones that make HIGH and IMMINENT distinct, so the cap on a
    third-person message is exercised too.
    """
    categories = (
        RiskCategory.SUICIDAL_IDEATION,
        RiskCategory.INTENT_PLAN,
        RiskCategory.ACCESS_TO_MEANS,
    )
    context = AssessmentContext(
        first_person=not about_someone_else,
        third_person=about_someone_else,
        timeframe=True,
    )
    return RiskAssessment(
        level=RiskLevel[level.upper()],
        matched_categories=categories,
        rationale_codes=("si.want_to_die", "ip.have_a_plan", "am.weapon_in_the_house"),
        context=context,
    )


def plan_record(level: str, about: bool, locale: str, region: str) -> dict[str, Any]:
    plan = build_escalator().plan(
        assessment(level, about),
        region=region,
        locale=locale,
    )
    message = plan.message
    return {
        "id": f"{level}-{'about' if about else 'self'}-{locale}-{region}",
        "level": level,
        "about_someone_else_input": about,
        "locale_requested": locale,
        "region_requested": region,
        "stored_level": plan.stored_level_code,
        "about_someone_else": plan.about_someone_else,
        "policy": {
            "template_id": plan.policy.template_id,
            "actions": [action.value for action in plan.policy.actions],
            "allow_llm": plan.policy.allow_llm,
            "deterministic_reply": plan.policy.deterministic_reply,
            "show_helplines": plan.policy.show_helplines,
            "show_emergency_instruction": plan.policy.show_emergency_instruction,
            "record_event": plan.policy.record_event,
        },
        "locale": plan.locale,
        "locale_fallback_used": plan.locale_fallback_used,
        "region": plan.region,
        "region_fallback_used": plan.region_fallback_used,
        "emergency_number": plan.emergency_number,
        "emergency_id": plan.emergency.id if plan.emergency else None,
        "resource_ids": [item.id for item in plan.resources],
        "needs_verification": [item.id for item in plan.resources if item.needs_verification],
        "message": None
        if message is None
        else {
            "template_id": message.template_id,
            "locale": message.locale,
            "title": message.title,
            "body": list(message.body),
            "helpline_intro": message.helpline_intro,
            "emergency_instruction": message.emergency_instruction,
            "trusted_person": message.trusted_person,
            "safety_steps": list(message.safety_steps),
            "closing": message.closing,
            "disclaimer": message.disclaimer,
        },
    }


def build_payload() -> dict[str, Any]:
    cases = [
        plan_record(level, about, locale, region)
        for level in LEVELS
        for about in (False, True)
        for locale in LOCALES
        for region in REGIONS
    ]
    return {
        "format": FORMAT,
        "generated_by": "evals/parity/export_crisis_plans.py",
        "description": "Python's crisis plans, for the browser escalator to match.",
        "count": len(cases),
        "levels": list(LEVELS),
        "locales": list(LOCALES),
        "regions": list(REGIONS),
        "runtime": {"python": platform.python_version()},
        "cases": cases,
    }


def render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=1, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the artifact is stale")
    args = parser.parse_args()
    payload = build_payload()
    rendered = render(payload)

    if args.check:
        if not OUTPUT.exists():
            print(f"MISSING {OUTPUT}", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != rendered:
            print(f"DRIFT {OUTPUT} — re-run without --check", file=sys.stderr)
            return 1
        print(f"OK {OUTPUT.name}: {payload['count']} plans")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    relative = OUTPUT.relative_to(REPO_ROOT)
    print(f"wrote {relative}: {payload['count']} plans, {len(rendered)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
