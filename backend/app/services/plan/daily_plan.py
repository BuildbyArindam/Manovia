"""Daily plan: a transparent, rule-based planner.

The planner answers one question — *what, if anything, should we suggest
today?* — with no model, no LLM and no network. It is a table of named rules
(:data:`RULES`), each a pure function from a :class:`PlannerContext` to an
optional :class:`PlanSuggestion`, evaluated in priority order. Everything the
planner knows is in the context dataclass; everything it can say is in the
rule table. A reviewer can read both in five minutes, which is the point: a
suggestion a person cannot audit is a suggestion they cannot trust.

Since Day 23 the wording lives in ``content/i18n/*.json`` (``plan.*`` and
``day_part.*`` strings) rather than in this file: the rules decide *when* to
say something, the content files decide *what* — in English, Hindi or Bengali,
chosen by ``PlannerContext.locale`` (default English).

The hard rules the task sets, and where they are enforced:

* **Never more than three.** :data:`MAX_SUGGESTIONS` caps the list; the
  builder stops collecting once it is reached, and the unit tests assert it
  across the whole rule table.
* **Every suggestion carries a "why".** A :class:`PlanSuggestion` cannot be
  constructed with an empty ``why``; the tests scan every rule's output.
* **Never nag.** Suggestions are gentle, skippable without a reason, and most
  rules only fire on multi-day signals (no rule complains about a single
  missed day).
* **Rest days are allowed.** When the rules find nothing to ask for and the
  person has been active, the plan says resting is fine; otherwise the plan
  is simply empty.
* **Quiet hours.** While the person's quiet hours are active the planner emits
  nothing at all — with one documented exception below.
* **PHQ-9 item 9.** An assessment answer may influence the plan in exactly one
  way: a recent positive item-9 answer shows the support-options suggestion
  and *nothing else*. The flag enters the context as a boolean; no other rule
  reads assessment data, and the support branch short-circuits every rule.

Privacy: the context only ever contains numbers, closed-vocabulary tags and
dates. Encrypted free text (mood notes, thought-record fields) is never
decrypted for planning; the API layer projects metadata columns only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from statistics import fmean

from app.content.i18n import LocalisedContent, load_localisation, resolve_locale
from app.models.enums import PlanItemKind
from app.services.exercises.engine import ExerciseCatalog, ExerciseDefinition, get_catalog

#: The plan never holds more than this many suggestions.
MAX_SUGGESTIONS: int = 3

#: How far back the planner looks for check-ins and completions.
LOOKBACK_DAYS: int = 14

#: Low-mood comparison windows: the last ``RECENT_DAYS`` local days against the
#: ``PRIOR_DAYS`` before them, each needing at least ``MIN_WINDOW`` check-ins.
RECENT_DAYS: int = 3
PRIOR_DAYS: int = 4
MIN_WINDOW: int = 3

#: A drop of less than this on the 1-5 scale is not worth mentioning.
MIN_DROP_POINTS: float = 0.75

#: A recent average at or below this is "persistently low" even without a
#: prior window to compare against.
LOW_MEAN: float = 2.0

#: Days without a check-in before the planner gently mentions it. One missed
#: day is nobody's business; this is deliberately not daily.
MISSED_CHECKIN_DAYS: int = 2

#: Distinct active days in the last week that earn the rest-day suggestion
#: when nothing else applies.
REST_ACTIVE_DAYS: int = 3

#: Distinct active days in the last week before the habit rule suggests the
#: next exercise. One lonely completion is not a habit, and treating it as one
#: would be the kind of nudge the planner exists to avoid.
HABIT_MIN_DAYS: int = 2

#: Emotion tags that count as an anxiety signal in recent check-ins.
ANXIETY_TAGS: frozenset[str] = frozenset({"anxiety", "fear"})

#: Defaults for quiet-hour bounds when a row has them enabled but unset.
DEFAULT_QUIET_START: time = time(22, 0)
DEFAULT_QUIET_END: time = time(7, 0)


class InvalidQuietTimeError(ValueError):
    """A quiet-hour bound that is not a 24-hour ``HH:MM`` string."""


@dataclass(frozen=True)
class PlanPreferences:
    """The settings the planner reads. Defaults mean "no preference, no quiet"."""

    preferred_exercise_tags: tuple[str, ...] = ()
    quiet_hours_enabled: bool = False
    quiet_start: time | None = None
    quiet_end: time | None = None


@dataclass(frozen=True)
class MoodSignal:
    """One check-in reduced to what the planner may see: a day and numbers."""

    local_date: date
    valence: int
    emotions: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlannerContext:
    """Everything the planner knows, and nothing more.

    ``now`` is timezone-aware in the person's own zone, so ``now.date()`` is
    their local today and ``now.hour`` is their local time of day.
    """

    now: datetime
    check_ins: tuple[MoodSignal, ...] = ()
    last_check_in_at: datetime | None = None
    exercise_days: tuple[date, ...] = ()
    exercise_ids_recent: tuple[str, ...] = ()
    open_thought_records: int = 0
    phq9_item9_flagged: bool = False
    preferences: PlanPreferences = field(default_factory=PlanPreferences)
    #: The language the suggestion copy is written in (Day 23). Resolved like
    #: every other locale in the backend: unknown or blank falls back to
    #: English. The default keeps the planner itself pure and English, so the
    #: rule table can be read without any locale machinery.
    locale: str = "en"


@dataclass(frozen=True)
class PlanSuggestion:
    """One suggestion. The ``why`` is mandatory: no reason, no suggestion."""

    kind: PlanItemKind
    title: str
    why: str
    ref: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.why.strip():
            raise ValueError("a suggestion needs both a title and a why")


@dataclass(frozen=True)
class DailyPlan:
    """The plan for one local day."""

    items: tuple[PlanSuggestion, ...]
    quiet_hours_active: bool = False
    support_only: bool = False


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def parse_quiet_time(value: str) -> time:
    """Parse a strict 24-hour ``HH:MM`` bound, or raise :class:`InvalidQuietTimeError`.

    The shape matches the settings write validation exactly: two digits, zero
    padded, ``00:00`` through ``23:59``. Anything looser is refused so a
    half-written bound can never reach the quiet-hours arithmetic.
    """
    parts = value.split(":")
    if len(parts) != 2 or len(parts[0]) != 2 or len(parts[1]) != 2:
        raise InvalidQuietTimeError(value)
    if not (parts[0].isdigit() and parts[1].isdigit()):
        raise InvalidQuietTimeError(value)
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise InvalidQuietTimeError(value)
    return time(hour, minute)


def is_quiet_hours(now: datetime, preferences: PlanPreferences) -> bool:
    """True while the person's quiet hours cover ``now``.

    A window whose start equals its end is treated as covering the whole day:
    if somebody sets quiet hours and the bounds collapse, the honest reading is
    "leave me alone", not "the setting silently switched off".
    """
    if not preferences.quiet_hours_enabled:
        return False
    start = preferences.quiet_start or DEFAULT_QUIET_START
    end = preferences.quiet_end or DEFAULT_QUIET_END
    current = now.timetz().replace(tzinfo=None)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end  # the window crosses midnight


def day_part(now: datetime) -> str:
    """``morning`` (05-11), ``afternoon`` (12-16) or ``evening`` (17-04)."""
    hour = now.hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    return "evening"


def _pick_exercise(
    preferred_tags: Sequence[str], candidates: Sequence[str], catalog: ExerciseCatalog
) -> tuple[str, ExerciseDefinition] | None:
    """The first candidate in the catalogue; preferred tags reorder softly.

    Preferences are a soft filter, never a hard one: if no candidate matches
    the preferred tags, the first known candidate still wins, because an
    appropriate exercise beats no exercise over a tag mismatch.
    """
    known = [definition for definition in map(catalog.get, candidates) if definition is not None]
    if not known:
        return None
    if preferred_tags:
        wanted = set(preferred_tags)
        matched = [definition for definition in known if wanted & set(definition.tags)]
        if matched:
            known = matched
    definition = known[0]
    return definition.id, definition


def _exercise_title(definition: ExerciseDefinition, locale_code: str) -> str:
    """The exercise's own title in the plan's language, English as fallback.

    ``LocalizedTitle`` makes the fallback explicit per field: a locale that has
    no translated title gets the English one rather than a blank.
    """
    if locale_code in ("hi", "bn"):
        translated = getattr(definition.title, locale_code)
        if translated:
            return translated
    return definition.title.en


def _copy(ctx: PlannerContext) -> tuple[LocalisedContent, str]:
    """The plan's localised content plus the resolved locale code.

    ``resolve_locale`` and ``load_localisation`` agree by construction: both
    map an unknown locale onto English, so the title language and the sentence
    language can never disagree.
    """
    resolved, _fallback = resolve_locale(ctx.locale)
    return load_localisation(resolved), resolved


def _days_since(ctx: PlannerContext, when: date) -> int:
    return (ctx.now.date() - when).days


# --------------------------------------------------------------------------- #
# The rules. Each returns a suggestion or None; order is priority.             #
# --------------------------------------------------------------------------- #


def _rule_first_check_in(ctx: PlannerContext) -> PlanSuggestion | None:
    """A brand-new person: invite one first check-in, never demand it."""
    if ctx.check_ins or ctx.last_check_in_at is not None:
        return None
    content, _ = _copy(ctx)
    return PlanSuggestion(
        kind=PlanItemKind.CHECK_IN,
        title=content.string("plan.first_check_in.title"),
        why=content.string("plan.first_check_in.why"),
    )


def _rule_open_thought_record(ctx: PlannerContext) -> PlanSuggestion | None:
    """A recent thought record still has no "what helped" step."""
    if ctx.open_thought_records <= 0:
        return None
    content, _ = _copy(ctx)
    return PlanSuggestion(
        kind=PlanItemKind.THOUGHT_RECORD,
        title=content.string("plan.open_thought_record.title"),
        why=content.string("plan.open_thought_record.why"),
    )


def _low_mood_candidates(ctx: PlannerContext) -> tuple[str, ...]:
    today_part = day_part(ctx.now)
    if today_part == "morning":
        return ("small-action-planner", "emotion-labelling", "body-scan")
    if today_part == "evening":
        return ("body-scan", "emotion-labelling", "small-action-planner")
    return ("emotion-labelling", "small-action-planner", "body-scan")


def _rule_low_mood(ctx: PlannerContext) -> PlanSuggestion | None:
    """Recent check-ins lower than the days before, or persistently low.

    Needs at least :data:`MIN_WINDOW` check-ins in each of the two windows to
    compare them — the same honesty rule the insights module uses: a pattern
    that cannot be supported is not reported.
    """
    today = ctx.now.date()
    recent_days = {today - timedelta(days=offset) for offset in range(RECENT_DAYS)}
    prior_days = {
        today - timedelta(days=offset) for offset in range(RECENT_DAYS, RECENT_DAYS + PRIOR_DAYS)
    }
    recent = [signal.valence for signal in ctx.check_ins if signal.local_date in recent_days]
    prior = [signal.valence for signal in ctx.check_ins if signal.local_date in prior_days]

    drop = (
        len(recent) >= MIN_WINDOW
        and len(prior) >= MIN_WINDOW
        and fmean(recent) <= fmean(prior) - MIN_DROP_POINTS
    )
    persistently_low = len(recent) >= MIN_WINDOW and fmean(recent) <= LOW_MEAN
    if not (drop or persistently_low):
        return None

    picked = _pick_exercise(
        ctx.preferences.preferred_exercise_tags, _low_mood_candidates(ctx), get_catalog()
    )
    if picked is None:
        return None
    exercise_id, definition = picked
    content, locale_code = _copy(ctx)
    return PlanSuggestion(
        kind=PlanItemKind.EXERCISE,
        ref=exercise_id,
        title=content.string("plan.low_mood.title"),
        why=content.render_string(
            "plan.low_mood.why",
            {"exercise_title": _exercise_title(definition, locale_code)},
        ),
    )


def _rule_anxiety(ctx: PlannerContext) -> PlanSuggestion | None:
    """Anxiety or fear tagged on a check-in in the last few days."""
    cutoff = ctx.now.date() - timedelta(days=RECENT_DAYS - 1)
    recent = [signal for signal in ctx.check_ins if signal.local_date >= cutoff]
    if not any(ANXIETY_TAGS & set(signal.emotions) for signal in recent):
        return None
    candidates = ("grounding-54321", "box-breathing", "slow-exhale-478")
    if day_part(ctx.now) == "evening":
        candidates = ("slow-exhale-478", "grounding-54321", "box-breathing")
    picked = _pick_exercise(ctx.preferences.preferred_exercise_tags, candidates, get_catalog())
    if picked is None:
        return None
    exercise_id, definition = picked
    content, locale_code = _copy(ctx)
    return PlanSuggestion(
        kind=PlanItemKind.EXERCISE,
        ref=exercise_id,
        title=content.string("plan.anxiety.title"),
        why=content.render_string(
            "plan.anxiety.why",
            {"exercise_title": _exercise_title(definition, locale_code)},
        ),
    )


def _rule_missed_check_in(ctx: PlannerContext) -> PlanSuggestion | None:
    """Check-ins exist, but the last one is a couple of days old.

    Works from the lifetime ``last_check_in_at``, not the window rows, so
    somebody away a fortnight still gets one gentle invitation instead of
    silence (and the first-check-in rule cannot fire for them either).
    """
    if ctx.last_check_in_at is None:
        return None
    last_local = ctx.last_check_in_at.astimezone(ctx.now.tzinfo).date()
    gap = _days_since(ctx, last_local)
    if gap < MISSED_CHECKIN_DAYS:
        return None
    content, _ = _copy(ctx)
    return PlanSuggestion(
        kind=PlanItemKind.CHECK_IN,
        title=content.string("plan.missed_check_in.title"),
        why=content.render_string(
            "plan.missed_check_in.why", {"days": content.format_count(gap)}
        ),
    )


def _exercise_habit_candidates(ctx: PlannerContext) -> tuple[str, ...]:
    today_part = day_part(ctx.now)
    if today_part == "morning":
        return ("small-action-planner", "body-scan", "box-breathing")
    if today_part == "evening":
        return ("worry-time", "slow-exhale-478", "body-scan")
    return ("box-breathing", "body-scan", "emotion-labelling")


def _rule_exercise_habit(ctx: PlannerContext) -> PlanSuggestion | None:
    """Somebody who exercises already: one short suggestion, never today's repeat.

    Needs at least :data:`HABIT_MIN_DAYS` active days in the week — a single
    completion is not a habit to build on, and saying otherwise would nag.
    """
    week_cutoff = ctx.now.date() - timedelta(days=6)
    active_days = {day for day in ctx.exercise_days if day >= week_cutoff}
    if len(active_days) < HABIT_MIN_DAYS or ctx.now.date() in ctx.exercise_days:
        return None
    picked = _pick_exercise(
        ctx.preferences.preferred_exercise_tags, _exercise_habit_candidates(ctx), get_catalog()
    )
    if picked is None:
        return None
    exercise_id, definition = picked
    content, locale_code = _copy(ctx)
    note_key = (
        "plan.preference_note.matched"
        if ctx.preferences.preferred_exercise_tags
        else "plan.preference_note.time_of_day"
    )
    return PlanSuggestion(
        kind=PlanItemKind.EXERCISE,
        ref=exercise_id,
        title=content.render_string(
            "plan.exercise_habit.title",
            {"day_part": content.string(f"day_part.{day_part(ctx.now)}")},
        ),
        why=content.render_string(
            "plan.exercise_habit.why",
            {
                "days": content.format_count(len(active_days)),
                "week_days": content.format_count(7),
                "exercise_title": _exercise_title(definition, locale_code),
                "preference_note": content.string(note_key),
            },
        ),
    )


def _rest_suggestion(ctx: PlannerContext) -> PlanSuggestion:
    week_cutoff = ctx.now.date() - timedelta(days=6)
    active_days = {day for day in ctx.exercise_days if day >= week_cutoff}
    content, _ = _copy(ctx)
    return PlanSuggestion(
        kind=PlanItemKind.REST,
        title=content.string("plan.rest.title"),
        why=content.render_string(
            "plan.rest.why",
            {
                "days": content.format_count(len(active_days)),
                "week_days": content.format_count(7),
            },
        ),
    )


def _support_suggestion(ctx: PlannerContext) -> PlanSuggestion:
    """The only thing an assessment answer may ever put in a plan."""
    content, _ = _copy(ctx)
    return PlanSuggestion(
        kind=PlanItemKind.SUPPORT,
        title=content.string("plan.support.title"),
        why=content.string("plan.support.why"),
    )


@dataclass(frozen=True)
class Rule:
    """One named planner rule. The table below is the whole planner."""

    id: str
    description: str
    build: Callable[[PlannerContext], PlanSuggestion | None]


#: The rule table, in priority order. The support and rest behaviours are
#: handled around the table (support short-circuits it; rest applies only when
#: the table produced nothing), and both are covered by the unit tests.
RULES: tuple[Rule, ...] = (
    Rule(
        id="first_check_in",
        description="No check-in yet: invite one first, gentle check-in.",
        build=_rule_first_check_in,
    ),
    Rule(
        id="open_thought_record",
        description="A recent thought record has no 'what helped' step yet.",
        build=_rule_open_thought_record,
    ),
    Rule(
        id="low_mood",
        description=(
            "Check-ins in the last 3 days average at least 0.75 lower than the 4 "
            "days before, or at most 2.0 with enough data: suggest a gentle exercise."
        ),
        build=_rule_low_mood,
    ),
    Rule(
        id="anxiety",
        description=(
            "Anxiety or fear tagged on a check-in in the last 3 days: suggest a grounding exercise."
        ),
        build=_rule_anxiety,
    ),
    Rule(
        id="missed_check_in",
        description=(
            f"Check-ins exist but the last one is at least {MISSED_CHECKIN_DAYS} "
            "days old: ask gently for a new one."
        ),
        build=_rule_missed_check_in,
    ),
    Rule(
        id="exercise_habit",
        description=(
            "Exercises done in the last 7 days but not yet today: suggest one "
            "short exercise for this time of day."
        ),
        build=_rule_exercise_habit,
    ),
)


# --------------------------------------------------------------------------- #
# The builder                                                                  #
# --------------------------------------------------------------------------- #


def build_daily_plan(ctx: PlannerContext) -> DailyPlan:
    """Build today's plan from the context. Pure and deterministic.

    Order of operations:

    1. A positive PHQ-9 item-9 flag shows the support-options suggestion and
       nothing else — the only influence an assessment answer may have.
    2. Quiet hours suppress every routine suggestion. (Support still shows:
       a helpline is not a nudge, and safety outranks quiet.)
    3. Rules run in table order; at most :data:`MAX_SUGGESTIONS` are kept and
       at most one suggestion of each kind survives.
    4. If the table produced nothing, the person already did something today,
       and the week has at least :data:`REST_ACTIVE_DAYS` active days, a
       rest-day card says resting is fine. Otherwise the plan is empty — and
       an empty plan is a valid answer, never an error.
    """
    quiet = is_quiet_hours(ctx.now, ctx.preferences)
    if ctx.phq9_item9_flagged:
        return DailyPlan(
            items=(_support_suggestion(ctx),), quiet_hours_active=quiet, support_only=True
        )
    if quiet:
        return DailyPlan(items=(), quiet_hours_active=True)

    items: list[PlanSuggestion] = []
    for rule in RULES:
        if len(items) >= MAX_SUGGESTIONS:
            break
        suggestion = rule.build(ctx)
        if suggestion is None:
            continue
        if any(existing.kind == suggestion.kind for existing in items):
            continue
        items.append(suggestion)

    if not items:
        week_cutoff = ctx.now.date() - timedelta(days=6)
        active_days = {day for day in ctx.exercise_days if day >= week_cutoff}
        if ctx.now.date() in ctx.exercise_days and len(active_days) >= REST_ACTIVE_DAYS:
            items.append(_rest_suggestion(ctx))

    return DailyPlan(items=tuple(items[:MAX_SUGGESTIONS]))


__all__ = [
    "ANXIETY_TAGS",
    "DEFAULT_QUIET_END",
    "DEFAULT_QUIET_START",
    "HABIT_MIN_DAYS",
    "LOOKBACK_DAYS",
    "LOW_MEAN",
    "MAX_SUGGESTIONS",
    "MIN_DROP_POINTS",
    "MIN_WINDOW",
    "MISSED_CHECKIN_DAYS",
    "PRIOR_DAYS",
    "RECENT_DAYS",
    "REST_ACTIVE_DAYS",
    "RULES",
    "DailyPlan",
    "InvalidQuietTimeError",
    "MoodSignal",
    "PlanPreferences",
    "PlanSuggestion",
    "PlannerContext",
    "Rule",
    "build_daily_plan",
    "day_part",
    "is_quiet_hours",
    "parse_quiet_time",
]
