"""Day 29 regression tests for the normaliser and quote handling.

Each test names the red-team finding it pins (docs/redteam-report.md).
"""

from __future__ import annotations

import pytest

from app.services.safety.base import RiskLevel
from app.services.safety.escalation import build_escalator
from app.services.safety.normalise import fold_confusables, split_quotes, strip_latin_combining
from app.services.safety.rules import RuleEngine, build_engine


@pytest.fixture(scope="module")
def engine() -> RuleEngine:
    return build_engine()


# -- RT-S-SP-07/08: homoglyphs ---------------------------------------------


def test_rt_s_sp_07_cyrillic_i_inside_kill_is_folded(engine: RuleEngine) -> None:
    assert engine.assess("I want to k\u0456ll myself").level is RiskLevel.HIGH


def test_rt_s_sp_08_greek_omicron_inside_to_is_folded(engine: RuleEngine) -> None:
    assert engine.assess("I want t\u03bf die").level is RiskLevel.HIGH


def test_real_cyrillic_words_are_left_alone() -> None:
    # "Privet, kak dela?" — "Privet" contains letters with no Latin twin.
    text = "\u041f\u0440\u0438\u0432\u0435\u0442, \u0434\u0435\u043b\u0430"
    assert fold_confusables(text) == text


def test_mixed_token_is_folded() -> None:
    assert fold_confusables("k\u0456ll") == "kill"


# -- RT-S-SP-09/10: stacked diacritics --------------------------------------


def test_rt_s_sp_09_strikethrough_is_stripped(engine: RuleEngine) -> None:
    assert strip_latin_combining("d\u0336i\u0336e\u0336") == "die"
    assert engine.assess("I want to d\u0336i\u0336e\u0336").level is RiskLevel.HIGH


def test_rt_s_sp_10_zalgo_is_stripped(engine: RuleEngine) -> None:
    assert engine.assess("I want to d\u0301\u0310i\u0352\u0315e\u0346").level is RiskLevel.HIGH


def test_indic_matras_survive_combining_strip() -> None:
    text = "\u092e\u0948\u0902 \u0920\u0940\u0915 \u0939\u0942\u0901"  # "main theek hoon"
    assert strip_latin_combining(text) == text


# -- RT-S-QT-04: contractions are not quotation marks ----------------------


def test_rt_s_qt_04_contractions_do_not_make_a_quotation() -> None:
    remainder, quoted = split_quotes("I'm so tired. I want to kill myself, I don't care")
    assert quoted == ""
    assert "kill myself" in remainder


def test_rt_s_qt_04_first_person_crisis_with_contractions_addresses_the_speaker(
    engine: RuleEngine,
) -> None:
    assessment = engine.assess("I'm so tired. I want to kill myself, I don't care anymore")
    plan = build_escalator().plan(assessment)
    assert assessment.level is RiskLevel.HIGH
    assert assessment.context.quoted is False
    assert plan.about_someone_else is False


def test_a_real_single_quoted_report_with_an_apostrophe_inside_is_still_quoted(
    engine: RuleEngine,
) -> None:
    assessment = engine.assess("She told me 'I can't go on, I want to die'")
    assert assessment.context.quoted is True
    assert build_escalator().plan(assessment).about_someone_else is True


# -- RT-S-QT-01..03: quoting yourself --------------------------------------


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ('Pasting what I wrote earlier: "I am going to end my life tonight"', RiskLevel.IMMINENT),
        ('My diary says "I want to kill myself" and I wrote that last night', RiskLevel.HIGH),
        ("I wrote this to myself: 'tonight I end it all'", RiskLevel.HIGH),
    ],
)
def test_rt_s_qt_self_quotes_are_about_the_speaker(
    engine: RuleEngine, text: str, level: RiskLevel
) -> None:
    assessment = engine.assess(text)
    assert assessment.level is level
    assert assessment.context.quoted is False
    assert build_escalator().plan(assessment).about_someone_else is False


def test_rt_s_qt_c1_a_friends_message_keeps_the_supporter_template(engine: RuleEngine) -> None:
    assessment = engine.assess('My friend texted me "I want to kill myself" and I don\'t know')
    assert build_escalator().plan(assessment).about_someone_else is True


# -- RT-S-EU-C3: decluttering is not a crisis ------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "I'm giving away my old clothes to charity",
        "giving away my spare books this weekend",
        "I gave away all the toys that don't fit to goodwill",
    ],
)
def test_rt_s_eu_c3_decluttering_does_not_fire(engine: RuleEngine, text: str) -> None:
    assert engine.assess(text).level is RiskLevel.NONE


def test_giving_things_away_is_still_high_in_either_word_order(engine: RuleEngine) -> None:
    assert engine.assess("I gave away my guitar yesterday").level >= RiskLevel.HIGH
    assert engine.assess("I've started giving my things away").level >= RiskLevel.HIGH


# -- RT-S-LN: windows ------------------------------------------------------


def test_rt_s_ln_02_risk_after_the_first_window_is_found(engine: RuleEngine) -> None:
    text = "Today I went to the shops and thought about the weather. " * 80 + "I want to die."
    assert len(text) > engine.max_chars
    assessment = engine.assess(text)
    assert assessment.level is RiskLevel.HIGH
    assert "input.windowed" in assessment.rationale_codes


def test_a_phrase_straddling_a_window_boundary_is_found() -> None:
    small = RuleEngine(max_chars=600)
    for offset in range(560, 620, 7):
        text = ("x " * 400)[:offset] + " I want to kill myself " + ("y " * 400)
        assert small.assess(text).level is RiskLevel.HIGH, offset


def test_the_highest_window_wins(engine: RuleEngine) -> None:
    text = "I feel a bit sad. " + "filler words here. " * 400 + "I am going to end my life tonight"
    assert engine.assess(text).level is RiskLevel.IMMINENT


# -- RT-S-TP-01: a message-wide third-person frame vs a first-person hit ---


def test_rt_s_tp_01_first_person_plan_is_not_capped_by_a_they_frame(engine: RuleEngine) -> None:
    assessment = engine.assess("I'm going to kill myself tonight and they have no idea")
    assert assessment.level is RiskLevel.IMMINENT
    assert assessment.context.third_person is False


@pytest.mark.parametrize(
    "text",
    [
        "she told me she has a plan for tonight",
        "My friend says she wants to kill herself",
        "I think she wants to die",
    ],
)
def test_reports_about_others_stay_third_person(engine: RuleEngine, text: str) -> None:
    assert engine.assess(text).context.third_person is True
