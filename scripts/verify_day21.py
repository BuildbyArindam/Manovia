"""Day 21 verification — the daily planner against eight synthetic users.

The planner is pure and deterministic, so the profiles are built in-process
from the same context dataclass the API layer uses: no database and no
network needed. Each profile exercises one planner behaviour; the script
prints every plan and asserts the Day-21 contract:

* never more than three suggestions;
* every suggestion carries a non-empty "why";
* quiet hours suppress every routine suggestion;
* a recent PHQ-9 item-9 answer collapses the plan to the support card.

Run it from the repository root:

    backend/.venv/bin/python scripts/verify_day21.py

Exits non-zero on any violation.
"""

from __future__ import annotations

import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.enums import PlanItemKind
from app.services.plan.daily_plan import (
    MAX_SUGGESTIONS,
    MoodSignal,
    PlannerContext,
    PlanPreferences,
    build_daily_plan,
)

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 11, 9, 30, tzinfo=TZ)  # a Sunday morning
TODAY = NOW.date()


def mood_offsets(
    days_back: int, valence: int, emotions: tuple[str, ...] = ()
) -> tuple[MoodSignal, ...]:
    return tuple(
        MoodSignal(
            local_date=TODAY - timedelta(days=offset),
            valence=valence,
            emotions=emotions,
        )
        for offset in range(days_back)
    )


def mood_at(offset: int, valence: int, emotions: tuple[str, ...] = ()) -> MoodSignal:
    return MoodSignal(
        local_date=TODAY - timedelta(days=offset), valence=valence, emotions=emotions
    )


PROFILES: list[tuple[str, PlannerContext]] = [
    # 1. Brand-new user: nothing recorded anywhere.
    (
        "1. New user (nothing recorded yet)",
        PlannerContext(now=NOW),
    ),
    # 2. Low mood for the past week, checked in this morning.
    (
        "2. Low mood for seven days",
        PlannerContext(
            now=NOW,
            check_ins=mood_offsets(7, valence=1),
            last_check_in_at=NOW - timedelta(hours=2),
        ),
    ),
    # 3. A genuinely great week, and an exercise already done today.
    (
        "3. Great week (high mood, active, already exercised today)",
        PlannerContext(
            now=NOW,
            check_ins=mood_offsets(7, valence=5),
            last_check_in_at=NOW - timedelta(hours=1),
            exercise_days=(TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=2)),
            exercise_ids_recent=("box-breathing", "body-scan"),
        ),
    ),
    # 4. Away for two weeks: last check-in and exercise both fourteen days ago.
    (
        "4. Missed two weeks",
        PlannerContext(
            now=NOW,
            check_ins=tuple(mood_at(offset, valence=3) for offset in (14, 15, 16, 17)),
            last_check_in_at=NOW - timedelta(days=14),
            exercise_days=(TODAY - timedelta(days=14),),
            exercise_ids_recent=("box-breathing",),
        ),
    ),
    # 5. Recent check-ins tagged with anxiety and fear.
    (
        "5. High anxiety tags",
        PlannerContext(
            now=NOW,
            check_ins=mood_offsets(2, valence=3, emotions=("anxiety", "fear")),
            last_check_in_at=NOW - timedelta(hours=4),
        ),
    ),
    # 6. Quiet hours active right now (22:00-07:00 window, it is 23:30 local).
    #    Without quiet hours the stale check-in below would earn a suggestion.
    (
        "6. Quiet hours active (23:30 local, 22:00-07:00 window)",
        PlannerContext(
            now=datetime(2026, 10, 11, 23, 30, tzinfo=TZ),
            check_ins=mood_offsets(3, valence=2),
            last_check_in_at=datetime(2026, 10, 8, 20, 0, tzinfo=TZ),
            preferences=PlanPreferences(
                quiet_hours_enabled=True,
                quiet_start=time(22, 0),
                quiet_end=time(7, 0),
            ),
        ),
    ),
    # 7. A PHQ-9 completed two days ago answered item 9 with "several days".
    #    Other signals are present to prove they are all set aside.
    (
        "7. PHQ-9 item 9 flagged two days ago",
        PlannerContext(
            now=NOW,
            check_ins=mood_offsets(4, valence=1),
            last_check_in_at=NOW - timedelta(days=1),
            phq9_item9_flagged=True,
        ),
    ),
    # 8. Power user: open thought record, a real mood dip, stale check-in,
    #    preferences set. Enough signals to hit the three-item cap.
    (
        "8. Power user (open record + mood dip + stale check-in + preferences)",
        PlannerContext(
            now=NOW,
            check_ins=tuple(mood_at(offset, valence=4) for offset in range(3, 10))
            + mood_offsets(3, valence=1),
            last_check_in_at=NOW - timedelta(days=3),
            open_thought_records=1,
            exercise_days=tuple(TODAY - timedelta(days=offset) for offset in (1, 2, 3)),
            exercise_ids_recent=("grounding-54321", "slow-exhale-478"),
            preferences=PlanPreferences(preferred_exercise_tags=("anxiety",)),
        ),
    ),
]


def render(name: str, context: PlannerContext) -> None:
    plan = build_daily_plan(context)
    print(f"\n=== {name} ===")
    print(
        f"quiet hours active: {plan.quiet_hours_active}"
        + (f" | support only: {plan.support_only}" if plan.support_only else "")
    )
    if not plan.items:
        print("  (no plan today - an empty plan is a valid answer)")
        return
    for index, item in enumerate(plan.items, start=1):
        ref = f" [{item.ref}]" if item.ref else ""
        print(f"  {index}. ({item.kind.value}{ref}) {item.title}")
        print(f"     why: {item.why}")


failures: list[str] = []
for name, context in PROFILES:
    plan = build_daily_plan(context)

    if len(plan.items) > MAX_SUGGESTIONS:
        failures.append(
            f"{name}: {len(plan.items)} suggestions (max is {MAX_SUGGESTIONS})"
        )
    for item in plan.items:
        if not item.why.strip():
            failures.append(f"{name}: suggestion '{item.title}' has no explanation")

    if plan.quiet_hours_active and not plan.support_only and plan.items:
        failures.append(f"{name}: quiet hours active but routine suggestions present")

    if context.phq9_item9_flagged:
        kinds = [item.kind for item in plan.items]
        if kinds != [PlanItemKind.SUPPORT] or not plan.support_only:
            failures.append(
                f"{name}: item-9 flag must produce only the support card, got {kinds}"
            )

    render(name, context)

print()
if failures:
    print("FAILURES:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print(
    f"All {len(PROFILES)} profiles obey the Day-21 contract "
    f"(<= {MAX_SUGGESTIONS} items, every suggestion explained, quiet hours respected, "
    "item 9 -> support only)."
)
