"""Load and validate the localised insight templates in ``content/insights/*.json``.

The insight sentences are generated from templates, never from a model
(:mod:`app.services.mood.insights` fills them in). That buys two things worth
protecting, and this module is where they are protected:

1. **The words are reviewable.** Every sentence a person can be shown about
   their own mood exists as a file in this repository, in the language they
   asked for. A reviewer reads one JSON file, not a graph of f-strings.
2. **The words cannot become causal by accident.** A template that says
   "because you slept badly" would turn a description of somebody's numbers
   into a claim about their life. This loader refuses to load any *summary*
   template containing causal vocabulary, in any shipped language, so the app
   cannot start with a causal sentence in it. The explainer templates are held
   to the opposite rule: they must contain the locale's explicit
   "this is not a cause" marker, because saying so is the whole point of them.

Everything else mirrors :mod:`app.content.i18n` (the crisis templates) on
purpose: a missing template id is a startup error rather than a blank panel,
every ``{placeholder}`` must be on that file's whitelist so a typo cannot reach
a screen and no caller can smuggle free text into a sentence, and the fallback
locale must ship with the backend.

Numbers and dates are localised by the file too: ``numerals`` gives the ten
digits the locale writes with (Devanagari and Bengali numerals are not a
typographic flourish — ``3.2`` in a Hindi sentence is a foreign token), and
``months`` plus ``date_format`` decide how a week is named.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from importlib.resources import files
from typing import Any, Final

from pydantic import BaseModel, Field, field_validator

#: Directory inside ``app/content`` holding one JSON file per locale.
INSIGHTS_DIR: Final = "insights"

#: The locale served when the requested one is unknown or untranslated. Shares
#: the value (and therefore the behaviour) of the crisis templates.
FALLBACK_LOCALE: Final = "en"

#: ``{name}`` — the only substitution syntax templates may use.
_PLACEHOLDER_RE: Final = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

#: Sentences generated *about the person's own numbers*. These are held to the
#: strictest wording rule: no causal vocabulary at all, not even negated,
#: because a summary has no business discussing causality.
SUMMARY_TEMPLATE_IDS: Final[tuple[str, ...]] = (
    "weekly.heading",
    "weekly.not_enough_data",
    "weekly.no_check_ins",
    "weekly.week_line",
    "weekly.week_thin",
    "weekly.week_empty",
    "trend.rising",
    "trend.falling",
    "trend.steady",
    "trend.not_enough_data",
    "rolling.average",
    "factor.reported",
    "factor.none",
    "pattern.time_of_day.reported",
    "pattern.weekday.reported",
    "pattern.none",
    "pattern.not_enough_data",
    "patterns.no_check_ins",
    "patterns.not_enough_data",
)

#: The "About these insights" explainer. Allowed — required — to discuss
#: causality, but only to deny it, and never to assert it.
EXPLAINER_TEMPLATE_IDS: Final[tuple[str, ...]] = (
    "about.title",
    #: The marker sentence itself: shipped so a reviewer can see the exact
    #: words the loader requires ``about.not_causal`` to contain.
    "about.marker",
    "about.not_causal",
    "about.disclaimer",
)

#: Explainer content that is a list of paragraphs rather than one sentence.
EXPLAINER_LIST_IDS: Final[tuple[str, ...]] = ("about.limitations", "about.methods")

#: Vocabulary a summary sentence may reach for when naming a measure, a
#: direction, a factor tag, a bucket or a grouping. All of them are looked up by
#: :mod:`app.services.mood.insights`, so a missing one is a startup failure.
LABEL_TEMPLATE_IDS: Final[tuple[str, ...]] = (
    "measure.valence",
    "measure.energy",
    "word.higher",
    "word.lower",
    "pattern_kind.time_of_day",
    "pattern_kind.weekday",
    #: Bare-noun twins of the labels above, for chart axes and table headers.
    #: The sentence forms carry a preposition in every language ("in the
    #: morning", "on Monday", "সোমবারে") because a template cannot know which
    #: one a locale needs; an axis wants the noun on its own.
    *(f"label.measure.{name}" for name in ("valence", "energy")),
    *(f"label.trend.{name}" for name in ("rising", "falling", "steady", "not_enough_data")),
    *(
        f"factor.{name}"
        for name in ("sleep", "study", "work", "family", "friends", "health", "money", "weather")
    ),
    *(f"time_of_day.{name}" for name in ("morning", "afternoon", "evening", "night")),
    *(f"label.time_of_day.{name}" for name in ("morning", "afternoon", "evening", "night")),
    *(
        f"label.weekday.{name}"
        for name in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    ),
    *(
        f"weekday.{name}"
        for name in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    ),
)

#: Every id a locale file must carry as a single string.
REQUIRED_STRINGS: Final[tuple[str, ...]] = (
    *SUMMARY_TEMPLATE_IDS,
    *EXPLAINER_TEMPLATE_IDS,
    *LABEL_TEMPLATE_IDS,
)

#: The placeholders each summary template *must* use — no more, no fewer.
#:
#: This is the invariant that keeps an insight honest across languages. The
#: sample sizes in ``factor.reported`` are not decoration: "your mood was 0.8
#: lower on sleep days" without "(9 vs 21)" is a claim a reader cannot weigh,
#: and a translator working quickly is exactly the person who drops a
#: parenthetical. Checking it at load time means a locale that loses its n
#: cannot start, rather than serving a sentence that overstates itself.
REQUIRED_PLACEHOLDERS: Final[dict[str, frozenset[str]]] = {
    "weekly.heading": frozenset({"weeks", "entries"}),
    "weekly.not_enough_data": frozenset({"entries", "needed"}),
    "weekly.no_check_ins": frozenset({"weeks"}),
    "weekly.week_line": frozenset({"week_start", "entries", "days", "valence", "energy"}),
    "weekly.week_thin": frozenset({"week_start", "entries", "days"}),
    "weekly.week_empty": frozenset({"week_start"}),
    "trend.rising": frozenset({"measure", "window_days", "change", "direction"}),
    "trend.falling": frozenset({"measure", "window_days", "change", "direction"}),
    "trend.steady": frozenset({"measure", "window_days"}),
    "trend.not_enough_data": frozenset({"measure", "window_days", "needed"}),
    "rolling.average": frozenset({"measure", "window_days", "average", "scale_max", "entries"}),
    "factor.reported": frozenset(
        {"factor", "measure", "difference", "direction", "tagged_n", "untagged_n"}
    ),
    "factor.none": frozenset({"needed"}),
    "pattern.time_of_day.reported": frozenset(
        {"measure", "lowest", "lowest_mean", "lowest_n", "highest", "highest_mean", "highest_n"}
    ),
    "pattern.weekday.reported": frozenset(
        {"measure", "lowest", "lowest_mean", "lowest_n", "highest", "highest_mean", "highest_n"}
    ),
    "pattern.none": frozenset({"kind"}),
    "pattern.not_enough_data": frozenset({"kind", "needed"}),
    "patterns.no_check_ins": frozenset({"days"}),
    "patterns.not_enough_data": frozenset({"entries", "days", "needed"}),
}

#: Causal vocabulary, per language. These are phrases that can only appear in a
#: causal *claim* — a negation ("this is not a cause") never needs them — which
#: is what makes a substring scan the right tool here rather than a parser.
CAUSAL_CLAIM_TOKENS: Final[dict[str, tuple[str, ...]]] = {
    "en": (
        "because",
        "due to",
        "leads to",
        "lead to",
        "results in",
        "as a result",
        "causes",
        "caused by",
        "causing",
        "driven by",
        "drives",
        "triggered by",
        "triggers",
        "makes your",
        "make your",
        "the reason",
        "why your",
        "that is why",
        "so that",
        "effect of",
        "impact of",
        "influences",
        "improves your",
        "worsens your",
    ),
    "hi": (
        "क्योंकि",
        "की वजह",
        "वजह से",
        "के कारण",
        "यही कारण",
        "इसलिए",
        "इस कारण",
        "का असर",
        "प्रभाव से",
        "के चलते",
        "इसलिए कि",
    ),
    "bn": (
        "কারণে",
        "কারণই",
        "জন্যই",
        "তাই",
        "যার জন্য",
        "ফলে",
        "প্রভাবে",
        "করে তোলে",
        "কারণ হয়ে",
    ),
}

#: Vocabulary that must appear nowhere in a summary, in any language: not even
#: negated. A summary describes; the explainer is where causality is discussed.
BANNED_EVERYWHERE: Final[dict[str, tuple[str, ...]]] = {
    "en": ("cause", "causal", "effect", "reason", "diagnos", "symptom", "disorder", "normal"),
    "hi": ("कारण", "प्रभाव", "निदान", "लक्षण", "विकार", "सामान्य"),
    "bn": ("কারণ", "প্রভাব", "নির্ণয়", "লক্ষণ", "রোগ", "স্বাভাবিক"),
}


class InsightsContentError(ValueError):
    """Raised when a shipped locale file cannot be trusted."""


class InsightsStrings(BaseModel):
    """One locale's insight copy, validated at load time."""

    version: int = Field(ge=1)
    locale: str = Field(min_length=2, max_length=16)
    #: Native-script name, for a language picker.
    display_name: str = Field(min_length=1, max_length=80)
    #: The ten digits this locale writes numbers with, in order 0-9.
    numerals: str = Field(min_length=10, max_length=10)
    #: Twelve month names, January first.
    months: list[str] = Field(min_length=12, max_length=12)
    #: How a date is spelled; may use ``{day}``, ``{month}`` and ``{year}``.
    date_format: str = Field(min_length=3, max_length=60)
    #: Placeholders any template in this file may use.
    placeholder_whitelist: list[str] = Field(min_length=1)
    strings: dict[str, str] = Field(min_length=1)
    lists: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("locale")
    @classmethod
    def _lower_locale(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("numerals")
    @classmethod
    def _distinct_numerals(cls, value: str) -> str:
        if len(set(value)) != 10 or any(char.isspace() for char in value):
            raise ValueError("numerals must be ten distinct, non-space characters")
        return value

    @field_validator("months")
    @classmethod
    def _non_blank_months(cls, value: list[str]) -> list[str]:
        if any(not month.strip() for month in value):
            raise ValueError("every month name must be non-blank")
        return value

    @field_validator("date_format")
    @classmethod
    def _known_date_fields(cls, value: str) -> str:
        unknown = sorted(set(_PLACEHOLDER_RE.findall(value)) - {"day", "month", "year"})
        if unknown:
            raise ValueError(f"date_format may only use day/month/year, got {unknown}")
        return value

    # -- accessors ---------------------------------------------------------- #

    def string(self, template_id: str) -> str:
        """One label or sentence. ``KeyError`` means a content bug, not a user."""
        return self.strings[template_id]

    def list_of(self, list_id: str) -> tuple[str, ...]:
        """One explainer list (limitations, methods)."""
        return tuple(self.lists.get(list_id, ()))

    def all_text(self) -> list[str]:
        """Every human-readable string in this file, in a stable order."""
        collected = [self.strings[key] for key in sorted(self.strings)]
        for key in sorted(self.lists):
            collected.extend(self.lists[key])
        return collected

    def placeholders_used(self, template_id: str) -> set[str]:
        """The placeholders one template actually asks for."""
        return set(_PLACEHOLDER_RE.findall(self.strings[template_id]))

    def render(self, template_id: str, values: Mapping[str, str]) -> str:
        """Substitute whitelisted placeholders and return a display-ready sentence.

        The three checks are the same ones :meth:`app.content.i18n.LocalisedContent.render`
        makes, for the same reason: a template and its caller must agree about
        what is allowed to appear in a sentence shown to a person.
        """
        if template_id not in self.strings:
            raise InsightsContentError(f"{self.locale} has no template {template_id!r}")
        allowed = set(self.placeholder_whitelist)
        used = self.placeholders_used(template_id)

        unknown = sorted(used - allowed)
        if unknown:
            raise InsightsContentError(
                f"{self.locale}/{template_id} uses unlisted placeholders {unknown}"
            )
        missing = sorted(used - {key for key, value in values.items() if value})
        if missing:
            raise InsightsContentError(f"{self.locale}/{template_id} needs values for {missing}")
        extra = sorted(set(values) - used)
        if extra:
            raise InsightsContentError(
                f"{self.locale}/{template_id} was given unused values {extra}"
            )

        text = self.strings[template_id]
        leftover = sorted(set(_PLACEHOLDER_RE.findall(text)) - set(values))
        if leftover:
            raise InsightsContentError(f"{self.locale}/{template_id} left {leftover} unfilled")
        return text.format_map(dict(values))

    # -- localised numbers and dates ---------------------------------------- #

    def localise_number(self, value: float, *, digits: int = 1) -> str:
        """``3.14`` → the locale's own spelling of it, rounded to ``digits``.

        Rounded with ``round`` (banker's rounding, like the rest of Python) and
        formatted by hand rather than through ``babel``: the app must not gain a
        locale-data dependency just to spell a number the way its own language
        writes it.
        """
        if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
            return self.localise_number(0.0, digits=digits)
        formatted = f"{round(value, digits):.{digits}f}" if digits > 0 else f"{round(value):.0f}"
        return self._translate_digits(formatted)

    def localise_date(self, day: date) -> str:
        """A date in this locale's own words and digits."""
        return self._translate_digits(
            self.date_format.format(
                day=str(day.day),
                month=self.months[day.month - 1],
                year=str(day.year),
            )
        )

    def _translate_digits(self, text: str) -> str:
        if self.numerals == "0123456789":
            return text
        return text.translate({ord(str(index)): char for index, char in enumerate(self.numerals)})


def _locale_files() -> tuple[str, ...]:
    """Every ``*.json`` in the insights directory, sorted, without the extension."""
    directory = files("app.content") / INSIGHTS_DIR
    return tuple(
        sorted(
            path.name[: -len(".json")]
            for path in directory.iterdir()
            if path.name.endswith(".json")
        )
    )


def available_insight_locales() -> tuple[str, ...]:
    """Locales that ship with insight copy (derived from the files present)."""
    return _locale_files()


def parse_insights(payload: dict[str, Any]) -> InsightsStrings:
    """Validate one locale payload and the invariants a model cannot express."""
    content = InsightsStrings.model_validate(payload)

    missing = [
        template_id for template_id in REQUIRED_STRINGS if template_id not in content.strings
    ]
    if missing:
        raise InsightsContentError(f"locale {content.locale} is missing templates {missing}")

    missing_lists = [list_id for list_id in EXPLAINER_LIST_IDS if not content.lists.get(list_id)]
    if missing_lists:
        raise InsightsContentError(f"locale {content.locale} is missing lists {missing_lists}")

    for template_id, expected in REQUIRED_PLACEHOLDERS.items():
        used = content.placeholders_used(template_id)
        if used != expected:
            raise InsightsContentError(
                f"locale {content.locale}/{template_id} must use exactly {sorted(expected)}, "
                f"found {sorted(used)}: a summary may not drop the numbers it rests on"
            )
    for template_id in (*EXPLAINER_TEMPLATE_IDS, *LABEL_TEMPLATE_IDS):
        used = content.placeholders_used(template_id)
        if used:
            raise InsightsContentError(
                f"locale {content.locale}/{template_id} must be static prose, found {sorted(used)}"
            )

    declared = set(content.placeholder_whitelist)
    for template_id, text in content.strings.items():
        for name in _PLACEHOLDER_RE.findall(text):
            if name not in declared:
                raise InsightsContentError(
                    f"locale {content.locale}/{template_id} uses {name!r}, "
                    "which is not on its placeholder whitelist"
                )
    for list_id, items in content.lists.items():
        for text in items:
            for name in _PLACEHOLDER_RE.findall(text):
                raise InsightsContentError(
                    f"locale {content.locale}/{list_id} may not use placeholders "
                    f"(found {name!r}): explainer lists are static prose"
                )

    _check_wording(content)
    return content


def _check_wording(content: InsightsStrings) -> None:
    """Refuse to load a locale whose summaries could be read as causal.

    Two rules, opposite in direction, because the two kinds of text have
    opposite jobs:

    * a **summary** describes a difference between two sets of the person's own
      days. It may not contain causal vocabulary, and it may not contain the
      words "cause", "effect" or "reason" either — not even negated. There is
      nothing to negate in a sentence that only reports a mean.
    * the **explainer** must contain the locale's non-causal marker
      (``about.marker``), so "these are associations, not a cause" is a
      structural requirement of shipping insight copy in a language rather than
      a sentence somebody might forget.
    """
    locale = content.locale
    claims = CAUSAL_CLAIM_TOKENS.get(locale, CAUSAL_CLAIM_TOKENS[FALLBACK_LOCALE])
    banned = BANNED_EVERYWHERE.get(locale, BANNED_EVERYWHERE[FALLBACK_LOCALE])

    for template_id in (*SUMMARY_TEMPLATE_IDS, *LABEL_TEMPLATE_IDS):
        text = content.strings[template_id]
        lowered = text.lower()
        for token in (*claims, *banned):
            if token.lower() in lowered:
                raise InsightsContentError(
                    f"locale {content.locale}/{template_id} contains causal or clinical "
                    f"vocabulary {token!r}: insight summaries must stay associative"
                )

    explainer = [content.strings[template_id] for template_id in EXPLAINER_TEMPLATE_IDS]
    explainer.extend(text for list_id in EXPLAINER_LIST_IDS for text in content.lists[list_id])
    for text in explainer:
        lowered = text.lower()
        for token in claims:
            if token.lower() in lowered:
                raise InsightsContentError(
                    f"locale {content.locale} explainer asserts causality with {token!r}"
                )

    marker = content.strings.get("about.marker", "").strip()
    if not marker:
        raise InsightsContentError(
            f"locale {content.locale} needs an 'about.marker': the sentence that says "
            "these patterns are associations, not a cause"
        )
    if marker not in content.strings["about.not_causal"]:
        raise InsightsContentError(
            f"locale {content.locale}/about.not_causal must contain its 'about.marker' verbatim"
        )


@lru_cache(maxsize=1)
def load_all_insights() -> dict[str, InsightsStrings]:
    """Every shipped locale, validated. A content bug is a startup error."""
    directory = files("app.content") / INSIGHTS_DIR
    loaded: dict[str, InsightsStrings] = {}
    for name in _locale_files():
        payload = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
        content = parse_insights(payload)
        if content.locale != name:
            raise InsightsContentError(f"insights/{name}.json declares locale {content.locale!r}")
        loaded[name] = content

    if FALLBACK_LOCALE not in loaded:
        raise InsightsContentError(
            f"the fallback locale {FALLBACK_LOCALE!r} must ship with insight copy"
        )
    return loaded


def resolve_insight_locale(locale: str | None) -> tuple[str, bool]:
    """Map a requested locale onto a shipped one.

    Accepts BCP-47 tags and strips the region (``hi-IN`` → ``hi``), exactly like
    the crisis templates do: the copy is the same across regions.
    """
    if locale:
        candidate = locale.strip().lower().split("-")[0]
        if candidate in load_all_insights():
            return candidate, False
    return FALLBACK_LOCALE, True


def load_insights(locale: str | None = None) -> InsightsStrings:
    """The insight copy for ``locale``, or English when it is unknown."""
    resolved, _ = resolve_insight_locale(locale)
    return load_all_insights()[resolved]


__all__ = [
    "BANNED_EVERYWHERE",
    "CAUSAL_CLAIM_TOKENS",
    "EXPLAINER_LIST_IDS",
    "EXPLAINER_TEMPLATE_IDS",
    "FALLBACK_LOCALE",
    "INSIGHTS_DIR",
    "LABEL_TEMPLATE_IDS",
    "REQUIRED_PLACEHOLDERS",
    "REQUIRED_STRINGS",
    "SUMMARY_TEMPLATE_IDS",
    "InsightsContentError",
    "InsightsStrings",
    "available_insight_locales",
    "load_all_insights",
    "load_insights",
    "parse_insights",
    "resolve_insight_locale",
]
