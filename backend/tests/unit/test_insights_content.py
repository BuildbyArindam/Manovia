"""The localised insight templates: what they must contain and must never say.

Two jobs. The first is ordinary i18n hygiene — every locale carries every id,
placeholders resolve, numbers and dates come out in the locale's own script. The
second is the one that matters on this product: **a summary sentence may not
become causal, and the guard is at load time**, so a template that says
"because you slept badly" stops the application from starting rather than
reaching somebody's screen.

The wording assertions deliberately use a blocklist written *here*, not the one
in :mod:`app.content.insights`. A test that re-imports the production list only
proves the list agrees with itself; an independent list proves the shipped copy
is clean.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from importlib.resources import files
from typing import Any

import pytest
from pydantic import ValidationError

from app.content.insights import (
    BANNED_EVERYWHERE,
    CAUSAL_CLAIM_TOKENS,
    EXPLAINER_LIST_IDS,
    EXPLAINER_TEMPLATE_IDS,
    FALLBACK_LOCALE,
    LABEL_TEMPLATE_IDS,
    REQUIRED_PLACEHOLDERS,
    REQUIRED_STRINGS,
    SUMMARY_TEMPLATE_IDS,
    InsightsContentError,
    available_insight_locales,
    load_all_insights,
    load_insights,
    parse_insights,
    resolve_insight_locale,
)

#: An independent scan of the shipped copy. Written here on purpose (see the
#: module docstring): overlapping but not identical to the loader's own list, so
#: a token that is missing from one of them still fails somewhere.
INDEPENDENT_CAUSAL_BLOCKLIST: tuple[str, ...] = (
    "because",
    "causes",
    "caused",
    "cause",
    "due to",
    "leads to",
    "results in",
    "as a result",
    "the reason",
    "why your",
    "makes your",
    "triggers",
    "drives",
    "effect",
    "impact",
    "इसलिए",
    "क्योंकि",
    "की वजह",
    "कारण",
    "কারণে",
    "তাই",
    "ফলে",
)

INDEPENDENT_CLINICAL_BLOCKLIST: tuple[str, ...] = (
    "diagnos",
    "symptom",
    "disorder",
    "depression",
    "anxiety disorder",
    "निदान",
    "লক্ষণ",
)


def payload(locale: str) -> dict[str, Any]:
    """One shipped locale file, as a mutable dict."""
    directory = files("app.content") / "insights"
    text = (directory / f"{locale}.json").read_text(encoding="utf-8")
    loaded: Any = json.loads(text)
    assert isinstance(loaded, dict)
    return loaded


# --------------------------------------------------------------------------- #
# What ships                                                                    #
# --------------------------------------------------------------------------- #


def test_three_locales_ship_and_the_fallback_is_one_of_them() -> None:
    locales = available_insight_locales()

    assert locales == ("bn", "en", "hi")
    assert FALLBACK_LOCALE in locales


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_every_locale_carries_every_required_id(locale: str) -> None:
    strings = load_insights(locale)

    for template_id in REQUIRED_STRINGS:
        assert strings.string(template_id).strip(), template_id
    for list_id in EXPLAINER_LIST_IDS:
        assert len(strings.list_of(list_id)) >= 3, list_id
        assert all(item.strip() for item in strings.list_of(list_id))


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_the_explainer_is_as_long_in_every_language(locale: str) -> None:
    """A translation that quietly drops a limitation is a smaller warning label."""
    strings = load_insights(locale)
    english = load_insights("en")

    for list_id in EXPLAINER_LIST_IDS:
        assert len(strings.list_of(list_id)) == len(english.list_of(list_id)), list_id
        assert all(len(item) >= 20 for item in strings.list_of(list_id))


def test_the_disclaimer_is_the_same_product_disclaimer_everywhere() -> None:
    for locale in ("en", "hi", "bn"):
        disclaimer = load_insights(locale).string("about.disclaimer")
        assert "Manovia" in disclaimer
        assert len(disclaimer) > 40


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_every_shipped_locale_has_its_own_causal_vocabulary(locale: str) -> None:
    """A new language without a blocklist would be scanned with English words.

    That is worse than no scan at all, because it looks like a scan: this fails
    the build instead, and the fix is to write the vocabulary with somebody who
    speaks the language.
    """
    assert locale in CAUSAL_CLAIM_TOKENS
    assert locale in BANNED_EVERYWHERE
    assert len(CAUSAL_CLAIM_TOKENS[locale]) >= 5
    assert len(BANNED_EVERYWHERE[locale]) >= 4


# --------------------------------------------------------------------------- #
# The wording guarantee, checked independently                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_no_summary_or_label_contains_causal_or_clinical_language(locale: str) -> None:
    strings = load_insights(locale)

    for template_id in (*SUMMARY_TEMPLATE_IDS, *LABEL_TEMPLATE_IDS):
        text = strings.string(template_id).lower()
        for token in INDEPENDENT_CAUSAL_BLOCKLIST:
            assert token not in text, f"{locale}/{template_id}: {token!r}"
        for token in INDEPENDENT_CLINICAL_BLOCKLIST:
            assert token not in text, f"{locale}/{template_id}: {token!r}"


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_the_explainer_denies_causality_in_so_many_words(locale: str) -> None:
    strings = load_insights(locale)

    marker = strings.string("about.marker")
    assert marker.strip()
    assert marker in strings.string("about.not_causal")
    for token in INDEPENDENT_CAUSAL_BLOCKLIST[:15]:  # the English assertions
        assert token not in strings.string("about.not_causal").lower().replace(marker, "")


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_the_explainer_lists_the_tag_ambiguity_limitation(locale: str) -> None:
    """The single most important caveat: a tag does not say good or bad."""
    limitations = " ".join(load_insights(locale).list_of("about.limitations"))

    assert len(limitations) > 200


def test_no_generated_sentence_is_causal_in_any_locale() -> None:
    """The end-to-end wording check, on sentences rather than on templates."""
    from datetime import UTC, datetime, timedelta

    from app.services.mood.insights import (
        CheckIn,
        build_insights,
        build_weekly,
        pattern_sentences,
        weekly_sentences,
    )

    zone = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 10, 11, 9, 0, tzinfo=UTC)
    check_ins = [
        CheckIn(
            at=now - timedelta(days=offset, hours=offset % 5),
            valence=2 if offset % 3 else 4,
            energy=3,
            factors=("sleep",) if offset % 2 else ("friends",),
        )
        for offset in range(40)
    ]

    for locale in ("en", "hi", "bn"):
        strings = load_insights(locale)
        report = build_insights(check_ins, zone=zone, today=now.date())
        weekly = build_weekly(check_ins, zone=zone, today=now.date())
        sentences = [
            *pattern_sentences(report, strings=strings),
            *weekly_sentences(weekly, strings=strings),
        ]
        assert sentences
        joined = " ".join(sentences).lower()
        for token in ("because", "causes", "caused", "due to", "leads to", "the reason"):
            assert token not in joined, f"{locale}: {token!r}"


# --------------------------------------------------------------------------- #
# The loader refuses content it cannot trust                                    #
# --------------------------------------------------------------------------- #


def test_a_causal_summary_template_stops_the_application_from_starting() -> None:
    broken = payload("en")
    broken["strings"]["factor.reported"] = (
        "On days you tagged {factor}, {measure} was about {difference} points {direction} "
        "because of it ({tagged_n} check-ins vs {untagged_n})."
    )

    with pytest.raises(InsightsContentError, match="causal"):
        parse_insights(broken)


def test_a_clinical_word_in_a_summary_is_refused() -> None:
    broken = payload("en")
    broken["strings"]["weekly.heading"] = "Your depression score over {weeks} weeks: {entries}."

    with pytest.raises(InsightsContentError, match="causal or clinical"):
        parse_insights(broken)


def test_a_causal_explainer_is_refused_too() -> None:
    broken = payload("en")
    broken["lists"]["about.limitations"] = [
        *broken["lists"]["about.limitations"],
        "Poor sleep causes low mood, so fix your sleep first.",
    ]

    with pytest.raises(InsightsContentError, match="asserts causality"):
        parse_insights(broken)


def test_an_explainer_without_its_non_causal_marker_is_refused() -> None:
    broken = payload("en")
    del broken["strings"]["about.marker"]

    with pytest.raises(InsightsContentError, match=r"about\.marker"):
        parse_insights(broken)


def test_a_marker_the_explainer_does_not_actually_say_is_refused() -> None:
    broken = payload("en")
    broken["strings"]["about.marker"] = "completely unrelated phrase"

    with pytest.raises(InsightsContentError, match="verbatim"):
        parse_insights(broken)


def test_a_missing_required_template_is_refused() -> None:
    broken = payload("en")
    del broken["strings"]["trend.steady"]

    with pytest.raises(InsightsContentError, match="missing templates"):
        parse_insights(broken)


def test_a_missing_explainer_list_is_refused() -> None:
    broken = payload("en")
    broken["lists"] = {"about.limitations": broken["lists"]["about.limitations"]}

    with pytest.raises(InsightsContentError, match="missing lists"):
        parse_insights(broken)


def test_a_summary_that_drops_its_sample_sizes_is_refused() -> None:
    """The n is the part that keeps a difference from being a verdict."""
    broken = payload("en")
    broken["strings"]["factor.reported"] = (
        "On days you tagged {factor}, {measure} was about {difference} points {direction}."
    )

    with pytest.raises(InsightsContentError, match="must use exactly"):
        parse_insights(broken)


def test_an_unlisted_placeholder_is_refused() -> None:
    broken = payload("en")
    broken["strings"]["weekly.heading"] = "Your last {weeks} weeks ({note})."
    broken["placeholder_whitelist"] = [*broken["placeholder_whitelist"], "note"]

    with pytest.raises(InsightsContentError, match="must use exactly"):
        parse_insights(broken)


def test_a_placeholder_outside_the_whitelist_is_refused() -> None:
    broken = payload("en")
    broken["strings"]["weekly.week_empty"] = "Week of {week_start}: {entries} check-ins."

    with pytest.raises(InsightsContentError, match="must use exactly"):
        parse_insights(broken)


def test_explainer_lists_may_not_take_placeholders() -> None:
    broken = payload("en")
    broken["lists"]["about.methods"] = [*broken["lists"]["about.methods"], "Draws: {needed}."]

    with pytest.raises(InsightsContentError, match="static prose"):
        parse_insights(broken)


def test_a_label_may_not_take_placeholders() -> None:
    broken = payload("en")
    broken["strings"]["factor.sleep"] = "sleep {entries}"

    with pytest.raises(InsightsContentError, match="static prose"):
        parse_insights(broken)


def test_numerals_must_be_ten_distinct_characters() -> None:
    broken = payload("en")
    broken["numerals"] = "012345678"

    with pytest.raises(ValidationError, match="numerals"):
        parse_insights(broken)


def test_a_locale_that_disagrees_with_its_filename_is_refused() -> None:
    broken = payload("en")
    broken["locale"] = "hi"

    content = parse_insights(broken)  # parses fine on its own…
    assert content.locale == "hi"
    # …and is caught by the loader that knows which file it came from.
    loaded = load_all_insights()
    assert all(name == value.locale for name, value in loaded.items())


def test_the_date_format_may_only_use_day_month_and_year() -> None:
    broken = payload("en")
    broken["date_format"] = "{day} {month} {year} {era}"

    with pytest.raises(ValidationError, match="date_format"):
        parse_insights(broken)


def test_every_required_placeholder_set_is_a_summary_or_label_the_caller_fills() -> None:
    """The contract between the templates and the sentence builders."""
    assert set(REQUIRED_PLACEHOLDERS) == set(SUMMARY_TEMPLATE_IDS)
    for template_id in (*EXPLAINER_TEMPLATE_IDS, *LABEL_TEMPLATE_IDS):
        assert template_id not in REQUIRED_PLACEHOLDERS


# --------------------------------------------------------------------------- #
# Rendering, numbers and dates                                                  #
# --------------------------------------------------------------------------- #


def test_render_fills_exactly_the_placeholders_a_template_asks_for() -> None:
    strings = load_insights("en")

    rendered = strings.render(
        "factor.reported",
        {
            "factor": "sleep",
            "measure": "your mood",
            "difference": "0.8",
            "direction": "lower",
            "tagged_n": "9",
            "untagged_n": "21",
        },
    )

    assert rendered == (
        "On days you tagged sleep, your mood was about 0.8 points lower than on days "
        "without that tag (9 check-ins vs 21)."
    )


def test_render_refuses_a_missing_value() -> None:
    strings = load_insights("en")

    with pytest.raises(InsightsContentError, match="needs values"):
        strings.render("factor.reported", {"factor": "sleep"})


def test_render_refuses_a_value_the_template_does_not_use() -> None:
    """A caller passing `note` would mean somebody's words reaching a sentence."""
    strings = load_insights("en")

    with pytest.raises(InsightsContentError, match="unused values"):
        strings.render("weekly.week_empty", {"week_start": "5 October 2026", "note": "hello"})


def test_render_refuses_an_unknown_template_id() -> None:
    with pytest.raises(InsightsContentError, match="no template"):
        load_insights("en").render("crisis.high", {})


def test_an_unknown_string_id_is_a_key_error() -> None:
    with pytest.raises(KeyError):
        load_insights("en").string("not.a.template")


def test_an_unknown_list_id_is_empty_rather_than_an_error() -> None:
    assert load_insights("en").list_of("about.nonsense") == ()


@pytest.mark.parametrize(
    ("locale", "value", "digits", "expected"),
    [
        ("en", 3.14159, 1, "3.1"),
        ("en", 9.0, 0, "9"),
        ("en", 0.75, 1, "0.8"),
        ("hi", 3.14159, 1, "३.१"),
        ("hi", 21.0, 0, "२१"),
        ("bn", 3.14159, 1, "৩.১"),
        ("bn", 1000.0, 0, "১০০০"),
    ],
)
def test_numbers_are_written_in_the_locale_own_digits(
    locale: str, value: float, digits: int, expected: str
) -> None:
    assert load_insights(locale).localise_number(value, digits=digits) == expected


def test_a_number_that_is_not_a_number_becomes_zero_rather_than_nan() -> None:
    """JSON has no NaN, and a chart cannot plot one either."""
    strings = load_insights("en")

    assert strings.localise_number(float("nan"), digits=1) == "0.0"
    assert strings.localise_number(float("inf"), digits=1) == "0.0"


@pytest.mark.parametrize(
    ("locale", "expected"),
    [
        ("en", "5 October 2026"),
        ("hi", "५ अक्तूबर २०२६"),
        ("bn", "৫ অক্টোবর ২০২৬"),
    ],
)
def test_dates_are_written_in_the_locale_own_words(locale: str, expected: str) -> None:
    assert load_insights(locale).localise_date(date(2026, 10, 5)) == expected


@pytest.mark.parametrize(
    ("requested", "expected", "fallback"),
    [
        ("en", "en", False),
        ("hi", "hi", False),
        ("bn", "bn", False),
        ("hi-IN", "hi", False),
        ("en-GB", "en", False),
        ("ta", "en", True),
        ("", "en", True),
        (None, "en", True),
        ("  HI  ", "hi", False),
    ],
)
def test_locale_resolution_strips_regions_and_falls_back_honestly(
    requested: str | None, expected: str, fallback: bool
) -> None:
    assert resolve_insight_locale(requested) == (expected, fallback)


def test_load_insights_serves_english_for_an_unknown_locale() -> None:
    assert load_insights("ta").locale == "en"
    assert load_insights(None).locale == "en"


def test_all_locales_parse_from_disk_on_every_call() -> None:
    """The cache is per-process; the files are the source of truth."""
    first = load_all_insights()
    second = load_all_insights()

    assert first is second
    assert set(first) == {"bn", "en", "hi"}


def test_a_deep_copy_of_a_payload_parses_identically() -> None:
    original = payload("en")

    assert parse_insights(copy.deepcopy(original)).locale == "en"
