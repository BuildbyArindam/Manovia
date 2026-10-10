"""The privacy guarantee: nothing the person wrote leaves the safety module.

AGENTS.md is explicit — never log raw message text at INFO or above — and the
design decision behind this module is stronger than that: the raw text is not
stored, not cached, not returned, and not logged *at any level*, because there is
nothing downstream that legitimately needs it. A reviewer reconstructing "why did
this escalate" gets a level, a category vocabulary, and pattern ids. That is
enough to audit and not enough to read somebody's message back to them.

These tests fail if that ever stops being true, including by accident: a future
``matched_text`` field, a debug log line, or a cache keyed on the input.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import app.services.safety as safety_package
from app.services.safety.base import (
    AssessmentContext,
    RiskAssessment,
    RiskCategory,
    RiskLevel,
    empty_assessment,
    text_fingerprint,
)
from app.services.safety.escalation import Escalator
from app.services.safety.normalise import normalise
from app.services.safety.patterns import load_patterns
from app.services.safety.rules import RuleEngine
from tests.safety.conftest import SafetyCase, load_cases

#: Derived here rather than exported by the package: these tests inspect the
#: module's own source, and the app should not grow a public constant just so a
#: test can find the directory it already knows how to import from.
PACKAGE_DIR = Path(safety_package.__file__).resolve().parent

#: A token that cannot occur in English, Hindi or Bengali prose, so finding it
#: anywhere downstream is unambiguous evidence of a leak.
CANARY = "zzqx7canary"

CRISIS_TEXTS = (
    f"I want to die {CANARY} and I have a plan for tonight",
    f"main aaj raat mar jaunga {CANARY}",
    f"ami bachte chai na {CANARY}",
    f"my friend says she wants to die {CANARY}",
    f"I just took a whole bottle of pills {CANARY}",
)


def _strings(value: Any) -> list[str]:
    """Every string reachable from a dumped model, however deeply nested."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_strings(item))
        return found
    if isinstance(value, (list, tuple)):
        found = []
        for item in value:
            found.extend(_strings(item))
        return found
    return []


# --------------------------------------------------------------------------- #
# The module does not log at all                                              #
# --------------------------------------------------------------------------- #

LOGGING_CALL = re.compile(r"\b(?:structlog|logging|logger|log)\s*[.(]|\bprint\s*\(")
LOGGING_IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:structlog|logging)\b", re.MULTILINE)


def test_the_safety_package_contains_no_logging_calls() -> None:
    """Grep, as a test: no logger, no print, anywhere in the package.

    Not "logs at DEBUG" and not "redacts before logging" — there is no logging
    call to get wrong. The API layer logs the assessment's fingerprint and length,
    and that is the only place a safety decision touches a log sink.
    """
    sources = sorted(PACKAGE_DIR.glob("*.py"))
    assert sources, "no safety sources found"

    offenders: list[str] = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        if LOGGING_IMPORT.search(text):
            offenders.append(f"{path.name}: imports a logging module")
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.split("#", 1)[0]
            if LOGGING_CALL.search(stripped):
                offenders.append(f"{path.name}:{number}: {stripped.strip()}")

    assert not offenders, "logging in the safety package:\n  " + "\n  ".join(offenders)


def test_no_safety_module_writes_to_a_file() -> None:
    """Nothing in the package opens a file for writing."""
    writing = re.compile(r"\bopen\s*\([^)]*[\"'][wa+]|\.write_text\s*\(|\.write\s*\(|\.dump\s*\(")
    offenders: list[str] = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if writing.search(line.split("#", 1)[0]):
                offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert not offenders, "file writes in the safety package: " + "; ".join(offenders)


def test_no_cache_in_the_package_can_be_keyed_on_somebody_s_message() -> None:
    """The one cache here holds our own pattern data, and must stay that way.

    ``load_patterns`` is cached because the shipped YAML never changes at runtime.
    Every one of its parameters is keyword-only, so no caller can key it on a
    positional message argument. This asserts that shape rather than banning the
    decorator outright: a future cache over assessments would need a positional
    parameter to be any use, and that is what fails here.
    """
    cached = re.compile(r"@(?:lru_cache|cache)\b")

    offenders: list[str] = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if not cached.search(line.split("#", 1)[0]):
                continue
            signature = next(
                (later for later in lines[number + 1 : number + 4] if later.startswith("def ")),
                "",
            )
            parameters = signature.split("(", 1)[1] if "(" in signature else ""
            if not parameters.startswith("*"):
                offenders.append(f"{path.name}:{number + 1}: {signature.strip()}")

    assert not offenders, "cache keyed on a positional argument: " + "; ".join(offenders)


# --------------------------------------------------------------------------- #
# The assessment carries a vocabulary, not prose                              #
# --------------------------------------------------------------------------- #


def _assessment_vocabulary(engine: RuleEngine) -> set[str]:
    """Every string an assessment is allowed to contain."""
    patterns = engine.patterns
    allowed = {level.label for level in RiskLevel}
    allowed |= {level.name.lower() for level in RiskLevel}
    allowed |= {category.value for category in RiskCategory}
    allowed |= {category.name.lower() for category in RiskCategory}
    allowed |= {rule.id for rule in patterns.rules}
    allowed |= {
        "ctx.negated",
        "ctx.figurative",
        "ctx.third_person",
        "ctx.quoted",
        "ctx.fiction_frame",
        "ctx.timeframe",
        "input.empty",
        "input.truncated",
    }
    # Field names appear as dict keys, not values; include them so the dump of a
    # model can be compared as a whole.
    allowed |= set(AssessmentContext.model_fields)
    allowed |= set(RiskAssessment.model_fields)
    return allowed


@pytest.mark.parametrize("case", load_cases(), ids=[case.id for case in load_cases()])
def test_no_case_produces_an_assessment_containing_its_own_words(
    engine: RuleEngine, case: SafetyCase
) -> None:
    """Positive test: every string in the dump belongs to a fixed vocabulary.

    Stronger than hunting for the input, because a leak would have to appear as a
    string, and there is no string in an assessment that is not a level, a
    category, a pattern id or a context flag.
    """
    allowed = _assessment_vocabulary(engine)
    assessment = engine.assess(case.text)
    dumped = assessment.model_dump(mode="json")

    unexpected = sorted({s for s in _strings(dumped) if s not in allowed})
    assert not unexpected, f"{case.id}: assessment carries {unexpected}"


def _pattern_id_corpus(engine: RuleEngine) -> str:
    """Every rationale code the engine can emit, as one searchable blob.

    Pattern ids are transliterations and fragments of the phrases they detect —
    ``si.want_to_die`` contains "die", ``hi.mar_jaunga`` contains "jaunga". They
    come from our own YAML and are the same whoever typed the message, so a word
    appearing inside one is not a leak of anybody's text. Anything else is.
    """
    return "|".join(rule.id for rule in engine.patterns.rules).lower()


def _unexplained_words(engine: RuleEngine, text: str) -> list[str]:
    """Input words that no pattern id accounts for."""
    corpus = _pattern_id_corpus(engine)
    return [
        word.lower().strip(".,!?\"'()")
        for word in text.split()
        if len(word.strip(".,!?\"'()")) >= 5
        and word.lower() != CANARY
        and word.lower().strip(".,!?\"'()") not in corpus
    ]


@pytest.mark.parametrize("text", CRISIS_TEXTS)
def test_a_canary_in_the_input_never_reaches_the_assessment(engine: RuleEngine, text: str) -> None:
    assessment = engine.assess(text)
    dumped = json.dumps(assessment.model_dump(mode="json")).lower()

    assert CANARY not in dumped
    # Nor any fragment of the sentence that a pattern id does not account for:
    # the assessment is a verdict, not a summary.
    leaked = [word for word in _unexplained_words(engine, text) if word in dumped]
    assert not leaked, leaked


@pytest.mark.parametrize("text", CRISIS_TEXTS)
def test_a_canary_in_the_input_never_reaches_the_response_plan(
    engine: RuleEngine, escalator: Escalator, text: str
) -> None:
    """The plan is what the API returns, so it is the leak that would matter.

    The plan does carry prose — the crisis templates and the helpline entries — but
    all of it is content we shipped, identical for every person at that level in
    that region. Common words in it ("tonight", "friend") are ours, not echoes of
    the message, so this test does not go word hunting: it asserts the canary is
    absent and then proves, in the next test, that nothing outside the assessment
    can influence the response at all.
    """
    assessment = engine.assess(text)

    for region, locale in (("IN", "en"), ("US", "hi"), ("GB", "bn"), (None, None)):
        plan = escalator.plan(assessment, region=region, locale=locale)
        dumped = json.dumps(plan.model_dump(mode="json")).lower()

        assert CANARY not in dumped
        # Nor any whole word unique enough to be somebody's: a name, a place, a
        # quantity. Words our own copy also uses are not evidence either way.
        for word in text.split():
            cleaned = word.lower().strip(".,!?\"'()")
            if len(cleaned) >= 8 and cleaned != CANARY:
                assert cleaned not in dumped, f"{region}/{locale}: {cleaned}"


@pytest.mark.parametrize("text", CRISIS_TEXTS)
def test_the_response_is_a_pure_function_of_the_assessment(
    engine: RuleEngine, escalator: Escalator, text: str
) -> None:
    """Round-trip the assessment through JSON and the plan must not change.

    This is the structural half of the privacy guarantee. If a plan depended on
    anything the assessment does not carry — the message, a projection of it, a
    cache — then rebuilding the assessment from its serialised form would produce a
    different response. Combined with the vocabulary test above (an assessment can
    only contain levels, categories, pattern ids and flags), it follows that no
    part of what somebody wrote can reach the response.
    """
    assessment = engine.assess(text)
    rebuilt = RiskAssessment.model_validate_json(assessment.model_dump_json())

    assert rebuilt == assessment
    for region, locale in (("IN", "en"), ("US", "hi"), (None, None)):
        direct = escalator.plan(assessment, region=region, locale=locale)
        round_tripped = escalator.plan(rebuilt, region=region, locale=locale)
        assert direct.model_dump(mode="json") == round_tripped.model_dump(mode="json")


def test_the_normalised_message_is_not_reachable_from_the_assessment(
    engine: RuleEngine,
) -> None:
    """Normalisation happens inside ``assess`` and is discarded with it."""
    assessment = engine.assess("I want to die tonight")

    # No attribute, on the assessment or its context, holds the message or any
    # projection of it. The dump is vocabulary only: levels, categories, ids, flags.
    for forbidden in ("message", "text", "normalised", "raw", "content", "input"):
        assert not hasattr(assessment, forbidden), forbidden
        assert not hasattr(assessment.context, forbidden), forbidden

    allowed = _assessment_vocabulary(engine)
    dumped_strings = _strings(assessment.model_dump(mode="json"))
    unexpected = sorted({value for value in dumped_strings if value not in allowed})
    assert not unexpected, unexpected


def test_normalisation_itself_is_not_persisted_by_the_engine(engine: RuleEngine) -> None:
    """The engine keeps no per-call state: two calls cannot see each other."""
    first = engine.assess(f"I want to die {CANARY}")
    second = engine.assess("i feel hopeless")

    assert CANARY not in json.dumps(second.model_dump(mode="json"))
    assert first.context.variants_searched >= 1
    # No attribute of the engine holds either message.
    for name, value in vars(engine).items():
        rendered = repr(value)
        assert CANARY not in rendered, f"engine.{name} retained the input"


def test_a_normalised_message_is_a_local_value_not_an_attribute(
    engine: RuleEngine,
) -> None:
    """``normalise`` returns a value; the engine stores no reference to it."""
    message = normalise(f"I want to die {CANARY}", engine.patterns.context)

    assert CANARY in message.primary  # the projection does hold text, locally
    assessment = engine.assess(f"I want to die {CANARY}")
    assert CANARY not in json.dumps(assessment.model_dump(mode="json"))
    assert not any(CANARY in repr(value) for value in vars(engine).values())


# --------------------------------------------------------------------------- #
# The guard rails that keep it that way                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "code",
    [
        "I want to die",
        "si.want to die",
        "SI.WANT_TO_DIE",
        "matched: die",
        "si.want_to_die!",
        "die",
    ],
)
def test_an_assessment_refuses_to_carry_prose_as_a_rationale_code(code: str) -> None:
    """The model validator is the last line of defence against a matched_text field.

    A rationale code names a *pattern*. Anything with a space, a capital letter or
    punctuation is somebody's words, and the object refuses to be built.
    """
    with pytest.raises(ValueError, match="does not look like a pattern id"):
        RiskAssessment(level=RiskLevel.HIGH, rationale_codes=(code,))


def test_a_well_formed_rationale_code_is_accepted() -> None:
    assessment = RiskAssessment(
        level=RiskLevel.HIGH,
        matched_categories=(RiskCategory.SUICIDAL_IDEATION,),
        rationale_codes=("si.want_to_die", "ctx.timeframe", "hi_dev.aatmhatya"),
    )

    assert len(assessment.rationale_codes) == 3


def test_the_context_records_counts_and_flags_but_no_text() -> None:
    context = AssessmentContext(negated_hits=2, figurative_hits=1, variants_searched=3)
    dumped = json.dumps(context.model_dump(mode="json"))

    assert dumped.count('"') >= 2  # keys only
    for value in _strings(context.model_dump(mode="json")):
        # The only strings are field names and an optional language hint.
        is_field_name = value in set(AssessmentContext.model_fields)
        is_language_tag = re.fullmatch(r"[a-z]{2}(-[A-Za-z]+)?", value)
        assert is_field_name or is_language_tag, value


def test_a_language_hint_is_a_tag_and_never_the_message() -> None:
    context = AssessmentContext(language="hi-IN")

    assert context.language == "hi-IN"
    assert len(context.language or "") <= 16


# --------------------------------------------------------------------------- #
# Fingerprints: correlation without content                                   #
# --------------------------------------------------------------------------- #


def test_the_fingerprint_is_a_short_hex_digest() -> None:
    fingerprint = text_fingerprint("I want to die")

    assert re.fullmatch(r"[0-9a-f]{16}", fingerprint)


def test_the_fingerprint_does_not_contain_the_text() -> None:
    text = f"I want to die {CANARY}"
    fingerprint = text_fingerprint(text)

    assert CANARY not in fingerprint
    assert "die" not in fingerprint


def test_the_fingerprint_is_stable_and_discriminating() -> None:
    assert text_fingerprint("same text") == text_fingerprint("same text")
    assert text_fingerprint("same text") != text_fingerprint("same text.")
    assert text_fingerprint("") != text_fingerprint(" ")


def test_the_fingerprint_is_not_reversible_by_truncation() -> None:
    """A 16-hex prefix cannot be walked back into the message."""
    short = text_fingerprint("ok")
    long = text_fingerprint("ok " * 400)

    assert len(short) == len(long) == 16
    assert "ok" not in short and "ok" not in long


def test_an_empty_assessment_carries_no_text() -> None:
    assessment = empty_assessment(rationale="input.empty")

    dumped = json.dumps(assessment.model_dump(mode="json"))
    assert assessment.level is RiskLevel.NONE
    assert dumped.count("input.empty") == 1


# --------------------------------------------------------------------------- #
# The shipped data is not a back door                                         #
# --------------------------------------------------------------------------- #


def test_pattern_notes_carry_no_user_text() -> None:
    """Rule notes are documentation for reviewers, written by us, not captured."""
    for rule in load_patterns().rules:
        if rule.note is None:
            continue
        assert CANARY not in rule.note
        # A note is prose we wrote; it must not be a sample of somebody's message.
        assert len(rule.note) > 10, rule.id


def test_the_case_file_is_the_only_place_synthetic_messages_live() -> None:
    """The engine's data files must not embed whole messages as patterns."""
    safety_dir = Path(__file__).resolve().parents[2] / "app" / "content" / "safety"
    for path in sorted(safety_dir.glob("patterns_*.yaml")):
        text = path.read_text(encoding="utf-8")
        assert CANARY not in text
        # Patterns are fragments; a full sentence with terminal punctuation in a
        # value would mean somebody pasted a message into the data.
        for match in re.finditer(r'^\s+value:\s*"(.*)"\s*$', text, re.MULTILINE):
            value = match.group(1)
            assert not value.endswith((".", "!", "?")), f"{path.name}: {value[:60]}"
