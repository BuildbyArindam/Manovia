"""The table-driven suite: every case in ``cases.yaml``, against the real engine.

One test per case, id-named, so a failure says which sentence changed meaning
rather than "assertion 47 failed". The parametrise list is built at import time
from the file, which is why a malformed case is a collection error and not a
silent pass.

The cases are the contract. When one fails there are exactly two honest fixes:
the pattern data is wrong, or the expectation was written wrong. Deleting the
case, or loosening it to whatever the engine happens to do, is not one of them.
"""

from __future__ import annotations

from collections import Counter

import pytest

from app.services.safety.base import RiskCategory, RiskLevel
from app.services.safety.rules import (
    CODE_EMPTY_INPUT,
    CODE_FIGURATIVE,
    CODE_NEGATED,
    CODE_QUOTED,
    CODE_THIRD_PERSON,
    CODE_TIMEFRAME,
    CODE_TRUNCATED,
    RuleEngine,
)
from tests.safety.conftest import SafetyCase, load_cases

CASES = load_cases()
BY_ID = {case.id: case for case in CASES}


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_case_matches_the_table(engine: RuleEngine, case: SafetyCase) -> None:
    assessment = engine.assess(case.text)
    why = f"[{case.group}] {case.note}" if case.note else f"[{case.group}]"

    assert assessment.level is case.level, (
        f"{case.id}: expected {case.level.label}, got {assessment.level.label} "
        f"(codes={list(assessment.rationale_codes)}) {why}"
    )

    if case.categories is not None:
        assert assessment.matched_categories == case.categories, (
            f"{case.id}: expected categories {[c.value for c in case.categories]}, "
            f"got {[c.value for c in assessment.matched_categories]} {why}"
        )

    for code in case.codes:
        assert code in assessment.rationale_codes, f"{case.id}: missing code {code} {why}"
    for code in case.forbid_codes:
        assert code not in assessment.rationale_codes, f"{case.id}: unexpected code {code} {why}"

    if case.third_person is not None:
        assert assessment.context.third_person is case.third_person, (
            f"{case.id}: expected third_person={case.third_person} {why}"
        )
    if case.first_person is not None:
        assert assessment.context.first_person is case.first_person, (
            f"{case.id}: expected first_person={case.first_person} {why}"
        )


# --------------------------------------------------------------------------- #
# Properties of the table itself                                               #
# --------------------------------------------------------------------------- #


def test_the_table_covers_every_level() -> None:
    levels = Counter(case.level for case in CASES)

    assert set(levels) == set(RiskLevel), f"levels covered: {dict(levels)}"
    assert len(CASES) >= 120, f"only {len(CASES)} cases"


def test_the_table_covers_every_category(engine: RuleEngine) -> None:
    """Each of the eight categories must be exercised by at least one case."""
    seen: set[RiskCategory] = set()
    for case in CASES:
        seen.update(engine.assess(case.text).matched_categories)

    missing = sorted((set(RiskCategory) - seen), key=lambda c: c.value)
    assert not missing, f"categories no case reaches: {[c.value for c in missing]}"


def test_the_table_covers_the_hard_groups() -> None:
    """The groups the brief calls out by name must all be present."""
    required = {
        "negation",
        "third_person",
        "quoted",
        "figurative",
        "euphemism",
        "misspelling",
        "leetspeak",
        "spacing",
        "repeated_letters",
        "hindi_romanised",
        "hindi_native",
        "bengali_romanised",
        "bengali_native",
        "benign",
        "combination",
        "edge",
    }
    present = {case.group for case in CASES}

    assert required <= present, f"missing groups: {sorted(required - present)}"


def test_every_group_has_more_than_one_case() -> None:
    counts = Counter(case.group for case in CASES)
    thin = sorted(group for group, count in counts.items() if count < 2)

    assert not thin, f"groups with a single case: {thin}"


def test_cases_are_synthetic_and_method_free() -> None:
    """No case may read like a real disclosure, or carry method detail.

    The texts are invented and deliberately bland: no names, no places, no
    quantities, no locations, no technique. This is a check on the file, not on
    the engine, and it exists so the next person adding a case keeps it that way.
    """
    for case in CASES:
        lowered = case.text.lower()
        assert "@" not in case.text, f"{case.id}: looks like a real contact detail"
        for forbidden in ("http://", "https://", "www."):
            assert forbidden not in lowered, f"{case.id}: contains a URL"
        # A means *category* may appear as a risk signal; a quantity or a
        # technique may not.
        for forbidden in (" mg", "mg ", "grams", "how many pills", "mix with", "step by step"):
            assert forbidden not in lowered, f"{case.id}: carries method detail"


# --------------------------------------------------------------------------- #
# Engine-level properties, independent of the table                            #
# --------------------------------------------------------------------------- #


def test_the_same_text_always_gets_the_same_answer(engine: RuleEngine) -> None:
    """Determinism is the property that makes the rest of this suite meaningful."""
    for text in ("I want to die", "i feel hopeless", "this traffic is killing me"):
        first = engine.assess(text)
        second = engine.assess(text)
        assert first == second


def test_assessment_is_the_same_across_engine_instances() -> None:
    """A second engine over the same data must not disagree with the first."""
    other = RuleEngine()

    for case in CASES:
        assert other.assess(case.text).level is case.level, case.id


def test_an_empty_message_is_not_a_crisis(engine: RuleEngine) -> None:
    assessment = engine.assess("")

    assert assessment.level is RiskLevel.NONE
    assert assessment.matched_categories == ()
    # Not an empty tuple: saying *why* nothing was found is the difference between
    # "no risk" and "no idea", and a caller should be able to tell them apart.
    assert assessment.rationale_codes == (CODE_EMPTY_INPUT,)


def test_whitespace_only_is_reported_as_empty(engine: RuleEngine) -> None:
    assessment = engine.assess("   \n\t ")

    assert assessment.level is RiskLevel.NONE
    assert CODE_EMPTY_INPUT in assessment.rationale_codes


def test_a_message_longer_than_the_window_is_truncated_and_still_assessed(
    engine: RuleEngine,
) -> None:
    text = "I want to die " + ("and nothing helps " * 500)
    assert len(text) > engine.max_chars

    assessment = engine.assess(text)

    assert assessment.context.truncated is True
    assert CODE_TRUNCATED in assessment.rationale_codes
    # The risk is in the first window, so it must still be found.
    assert assessment.level is RiskLevel.HIGH


def test_risk_at_the_far_end_of_a_long_message_is_not_lost(engine: RuleEngine) -> None:
    """Padding in front must not hide the sentence that matters."""
    text = ("i had a long day and nothing much happened " * 60) + "i want to die"
    assert len(text) <= engine.max_chars

    assessment = engine.assess(text)

    assert assessment.level is RiskLevel.HIGH


def test_context_codes_only_appear_when_the_context_is_there(
    engine: RuleEngine,
) -> None:
    """The engine must not claim a judgement it did not make."""
    plain = engine.assess("I want to die")

    for code in (CODE_NEGATED, CODE_FIGURATIVE, CODE_QUOTED, CODE_THIRD_PERSON, CODE_TRUNCATED):
        assert code not in plain.rationale_codes

    assert CODE_TIMEFRAME in engine.assess("i want to die tonight").rationale_codes


def test_a_language_hint_never_changes_the_answer(engine: RuleEngine) -> None:
    """Every pattern runs against every message, so a wrong hint cannot hide risk."""
    for text in ("I want to die", "main mar jaunga", "ami bachte chai na"):
        hinted = engine.assess(text, language="hi-IN")
        plain = engine.assess(text)
        assert hinted.level is plain.level
        assert hinted.matched_categories == plain.matched_categories


def test_matched_categories_are_sorted_and_unique(engine: RuleEngine) -> None:
    assessment = engine.assess("i cut myself and i want to die")

    values = [category.value for category in assessment.matched_categories]
    assert values == sorted(values)
    assert len(values) == len(set(values))
    assert len(values) >= 2


def test_rationale_codes_are_stable_pattern_ids(engine: RuleEngine) -> None:
    """A code names a pattern, never a fragment of somebody's message."""
    for case in CASES:
        for code in engine.assess(case.text).rationale_codes:
            assert " " not in code, f"{case.id}: {code}"
            assert code == code.lower(), f"{case.id}: {code}"


def test_recall_on_the_high_and_imminent_cases(engine: RuleEngine) -> None:
    """Measured, not asserted: how many of the table's crisis cases are caught.

    This is the number the design doc quotes. It is a test rather than a report so
    that a regression in recall is a build failure, and so the figure in the docs
    cannot drift away from what the code actually does.
    """
    crisis = [case for case in CASES if case.level >= RiskLevel.HIGH]
    caught = [case for case in crisis if engine.assess(case.text).level >= RiskLevel.HIGH]

    recall = len(caught) / len(crisis)
    assert recall >= 0.95, (
        f"recall on HIGH+IMMINENT cases fell to {recall:.1%}; missed: "
        f"{[case.id for case in crisis if case not in caught]}"
    )


def test_a_derived_variant_cannot_readmit_a_hit_the_honest_text_dismissed(
    engine: RuleEngine,
) -> None:
    """The honest text's verdict binds every rewrite of it.

    Regression, found by the Day 8 out-of-table probe. The repeated-letter
    collapse rewrites ordinary words as well as tricks: "embarrassment" becomes
    "embarasment". That broke the benign frame "dying of embarrassment" in the
    collapsed variant while leaving "i am dying" intact, so an idiom the primary
    had already dismissed was re-admitted from a rewrite of itself and the
    message scored LOW on the strength of a joke.

    The projections already obeyed this rule — a projection may add a hit the
    honest text hid, it can never cancel one — but the collapse lives in the
    variants list, which was searched before the verdicts were computed. _match
    now judges the primary first and applies its verdicts to every derived view.
    """
    assessment = engine.assess("i am dying of embarrassment about that presentation")

    assert assessment.level is RiskLevel.NONE
    assert CODE_FIGURATIVE in assessment.rationale_codes
    assert "si.dying" not in assessment.rationale_codes, (
        "the collapsed spelling of the same sentence re-admitted a hit the honest "
        "text had already dismissed as an idiom"
    )


def test_stretched_letters_do_not_outvote_a_negation(engine: RuleEngine) -> None:
    """Collapsing "diiiiie" to "die" has to carry the denial with it.

    The collapse exists so letter-stretching cannot hide a risk phrase. It must
    not work in the other direction either: the negation in the honest text still
    applies to the collapsed spelling, so this stays a denial and scores LOW
    rather than becoming a crisis the moment somebody holds a key down.
    """
    assessment = engine.assess("i dont want to diiiiie")

    assert assessment.level is RiskLevel.LOW
    assert CODE_NEGATED in assessment.rationale_codes
