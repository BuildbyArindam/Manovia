"""Day 23: the planner's copy is localised, and the planner stays honest.

The wording of every suggestion lives in ``content/i18n/*.json`` under the
``plan.*`` and ``day_part.*`` keys. These tests pin three things:

* the three shipped languages actually produce their own words — a rule must
  never leak an English sentence into a Hindi or Bengali plan;
* counts are written in the locale's own digits (Devanagari/Bengali numerals),
  because ``३ दिन`` with an ASCII ``3`` in it is a half-translated sentence;
* an unknown locale falls back to English, the same fallback every other
  surface in the backend uses.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.content.i18n import (
    REQUIRED_STRINGS,
    available_locales,
    load_all_localisations,
    load_localisation,
)
from app.services.plan.daily_plan import (
    MoodSignal,
    PlannerContext,
    PlanPreferences,
    _rest_suggestion,
    _rule_exercise_habit,
    _rule_first_check_in,
    _rule_missed_check_in,
    _rule_open_thought_record,
    _support_suggestion,
    build_daily_plan,
)

MORNING = datetime(2026, 10, 11, 9, 0, tzinfo=ZoneInfo("UTC"))


def ctx(**overrides: object) -> PlannerContext:
    defaults: dict[str, object] = {
        "now": MORNING,
        "check_ins": (),
        "last_check_in_at": None,
        "exercise_days": (),
        "exercise_ids_recent": (),
        "open_thought_records": 0,
        "phq9_item9_flagged": False,
        "preferences": PlanPreferences(),
    }
    defaults.update(overrides)
    return PlannerContext(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Content files: every locale ships every plan string                          #
# --------------------------------------------------------------------------- #


def test_every_locale_ships_every_plan_string() -> None:
    locales = load_all_localisations()
    assert set(available_locales()) >= {"en", "hi", "bn"}
    for locale, content in locales.items():
        for key in REQUIRED_STRINGS:
            assert content.strings.get(key, "").strip(), f"{locale} is missing {key}"


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_plan_string_placeholders_agree_across_locales(locale: str) -> None:
    """A placeholder in one language must exist in all three: the planner
    substitutes the same values whichever locale it renders."""
    en = load_localisation("en")
    other = load_localisation(locale)
    for key in REQUIRED_STRINGS:
        assert set(_placeholders(en.string(key))) == set(_placeholders(other.string(key))), key


def _placeholders(text: str) -> list[str]:
    return re.findall(r"\{([a-z_][a-z0-9_]*)\}", text)


def test_format_count_uses_locale_digits() -> None:
    assert load_localisation("en").format_count(3) == "3"
    assert load_localisation("hi").format_count(3) == "३"
    assert load_localisation("bn").format_count(10) == "১০"
    with pytest.raises(ValueError):
        load_localisation("en").format_count(-1)


def test_render_string_refuses_missing_and_extra_values() -> None:
    content = load_localisation("en")
    with pytest.raises(ValueError, match="needs values"):
        content.render_string("plan.missed_check_in.why", {})
    with pytest.raises(ValueError, match="unused values"):
        content.render_string("plan.first_check_in.why", {"days": "2"})


# --------------------------------------------------------------------------- #
# Rules render in the requested language                                       #
# --------------------------------------------------------------------------- #


def test_first_check_in_is_localised() -> None:
    hi = _rule_first_check_in(ctx(locale="hi"))
    bn = _rule_first_check_in(ctx(locale="bn"))
    en = _rule_first_check_in(ctx(locale="en"))
    assert hi is not None and bn is not None and en is not None
    assert hi.title == "पहला मूड चेक-इन कीजिए"
    assert "चेक-इन" in hi.why
    assert bn.title == "প্রথম মুড চেক-ইন করুন"
    assert "চেক-ইন" in bn.why
    assert en.title == "Take a first check-in"


def test_missed_check_in_counts_in_locale_digits() -> None:
    last = MORNING - timedelta(days=3)
    hi = _rule_missed_check_in(ctx(last_check_in_at=last, locale="hi"))
    bn = _rule_missed_check_in(ctx(last_check_in_at=last, locale="bn"))
    en = _rule_missed_check_in(ctx(last_check_in_at=last, locale="en"))
    assert hi is not None and bn is not None and en is not None
    assert "३ दिन" in hi.why
    assert "৩ দিন" in bn.why
    assert "3 days ago" in en.why
    # No ASCII digit may survive into an Indic sentence (Devanagari/Bengali
    # digits are expected — they are what format_count writes).
    ascii_digits = set("0123456789")
    assert not ascii_digits.intersection(hi.why)
    assert not ascii_digits.intersection(bn.why)


def test_open_thought_record_is_localised() -> None:
    hi = _rule_open_thought_record(ctx(open_thought_records=1, locale="hi"))
    assert hi is not None
    assert hi.title == "एक विचार-रिकॉर्ड पूरा कीजिए"


def test_exercise_habit_localises_day_part_and_counts() -> None:
    days = tuple(MORNING.date() - timedelta(days=offset) for offset in (1, 2))
    context = ctx(exercise_days=days, locale="hi")
    suggestion = _rule_exercise_habit(context)
    assert suggestion is not None
    assert suggestion.title == "सुबह के लिए एक छोटा अभ्यास"
    assert "२ दिन" in suggestion.why
    assert "७ दिनों" in suggestion.why


def test_exercise_habit_uses_localised_exercise_title_when_available() -> None:
    days = tuple(MORNING.date() - timedelta(days=offset) for offset in (1, 2))
    hi = _rule_exercise_habit(ctx(exercise_days=days, locale="hi"))
    en = _rule_exercise_habit(ctx(exercise_days=days, locale="en"))
    assert hi is not None and en is not None
    # The morning pick is small-action-planner; its title is translated.
    assert hi.why != en.why


def test_rest_and_support_are_localised() -> None:
    days = tuple(MORNING.date() - timedelta(days=offset) for offset in (0, 1, 2))
    rest_hi = _rest_suggestion(ctx(exercise_days=days, locale="hi"))
    rest_bn = _rest_suggestion(ctx(exercise_days=days, locale="bn"))
    assert rest_hi.title == "आज आराम का दिन हो सकता है"
    assert "३ दिन" in rest_hi.why
    assert rest_bn.title == "আজ বিশ্রামের দিন হতে পারে"

    support = _support_suggestion(ctx(locale="bn"))
    assert support.title == "সহায়তার উপায়গুলো এখানেই আছে"


def test_unknown_locale_falls_back_to_english() -> None:
    suggestion = _rule_first_check_in(ctx(locale="fr"))
    assert suggestion is not None
    assert suggestion.title == "Take a first check-in"


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_build_daily_plan_item9_support_in_every_locale(locale: str) -> None:
    plan = build_daily_plan(ctx(phq9_item9_flagged=True, locale=locale))
    assert plan.support_only
    (item,) = plan.items
    expected = load_localisation(locale).string("plan.support.title")
    assert item.title == expected
    assert item.why == load_localisation(locale).string("plan.support.why")
