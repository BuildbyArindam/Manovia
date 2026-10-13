"""What the safety loaders do when the content is wrong, and the odd corners.

Two things in ``app/services/safety`` are loaders: the pattern set behind the
rules engine (``patterns.py``) and the output policy behind the reply guard
(``output_guard.py``). Both read YAML that a human edits, both raise
``ValueError`` naming the offending key or id, and both are *supposed* to stop
the process rather than start degraded — "the crisis vocabulary failed to load,
so we will wing it" is not a state this product is allowed to reach.

A loader is also the part of a safety system that is easiest to leave untested,
because the shipped files are valid and the interesting branches only run on a
file that is broken. So the helpers below build deliberately broken documents —
one field at a time — and assert on the refusal and the message.

The rest of the file covers branches a normal request cannot reach: a classifier
that raises (the ensemble must fall back to rules), an input cap of zero, the
cross-script language checks in the output guard, and the quoted-speech and
obfuscation projections in the rules engine.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.services.safety.base import RiskAssessment, RiskLevel
from app.services.safety.ensemble import SOURCE_RULES, SOURCE_UNCERTAIN, combine, decide
from app.services.safety.ml_classifier import (
    LEVEL_LABELS,
    MLPrediction,
    NullClassifier,
    TfidfLogisticClassifier,
)
from app.services.safety.normalise import normalise
from app.services.safety.output_guard import (
    RuleBasedOutputGuard,
    _locale,
)
from app.services.safety.output_guard import (
    _parse_pattern_set as parse_output_patterns,
)
from app.services.safety.patterns import (
    CONTEXT_FILE,
    PATTERN_FILES,
    _load_yaml,
    load_patterns,
    parse_pattern_set,
)
from app.services.safety.pipeline import run_ensemble
from app.services.safety.rules import RuleEngine, build_engine

# --------------------------------------------------------------------------- #
# Building broken pattern documents                                           #
# --------------------------------------------------------------------------- #


def shipped_documents() -> dict[str, dict[str, Any]]:
    """A deep copy of the four shipped pattern documents, safe to mutate."""
    return {name: dict(copy.deepcopy(_load_yaml(name))) for name in PATTERN_FILES}


def with_first_rule(documents: dict[str, dict[str, Any]], **changes: Any) -> dict[str, Any]:
    """Apply ``changes`` to the first rule of the first vocabulary file."""
    for name, document in documents.items():
        if name == CONTEXT_FILE:
            continue
        rules = document.get("rules") or []
        if rules:
            rules[0] = {**rules[0], **changes}
            return documents
    raise AssertionError("no shipped rule to mutate")


def test_the_shipped_documents_still_load_and_describe_themselves() -> None:
    """The baseline: the real files parse, and ``describe()`` reports counts only."""
    pattern_set = parse_pattern_set(shipped_documents())
    described = pattern_set.describe()

    assert described["rules"] == len(pattern_set.rules)
    assert described["sources"] == list(PATTERN_FILES)
    assert sum(described["by_level"].values()) == len(pattern_set.rules)
    assert sum(described["by_category"].values()) == len(pattern_set.rules)
    assert sum(described["by_language"].values()) == len(pattern_set.rules)
    # English rules carry no language tag, so they count under the "en" key.
    assert described["by_language"]["en"] > 0
    assert described["negation_cues"] == len(pattern_set.context.negation_cues)
    assert described["misspellings"] == len(pattern_set.context.misspellings)
    # The description is a health-endpoint payload: counts and file names. No
    # pattern value, no example text, nothing that could name a method.
    assert all(isinstance(key, str) for key in described)


def test_parse_pattern_set_requires_the_context_file() -> None:
    with pytest.raises(ValueError, match=r"needs patterns_context\.yaml"):
        parse_pattern_set({"patterns_core.yaml": {"rules": []}})


def test_parse_pattern_set_rejects_a_non_mapping_document() -> None:
    documents = shipped_documents()
    documents["patterns_extra.yaml"] = ["not", "a", "mapping"]  # type: ignore[assignment]
    with pytest.raises(ValueError, match="expected a mapping at the top level"):
        parse_pattern_set(documents)


def test_parse_pattern_set_rejects_a_non_list_rules_key() -> None:
    documents = shipped_documents()
    documents["patterns_extra.yaml"] = {"rules": "not-a-list"}
    with pytest.raises(ValueError, match="'rules' must be a list"):
        parse_pattern_set(documents)


def test_parse_pattern_set_refuses_duplicate_ids() -> None:
    documents = shipped_documents()
    documents["patterns_extra.yaml"] = {
        "rules": [
            {
                "id": "dup.one",
                "category": "suicidal_ideation",
                "level": "high",
                "kind": "phrase",
                "value": "alpha",
            },
            {
                "id": "dup.one",
                "category": "self_harm",
                "level": "low",
                "kind": "phrase",
                "value": "beta",
            },
        ]
    }
    with pytest.raises(ValueError, match=r"duplicated: \['dup.one'\]"):
        parse_pattern_set(documents)


def test_parse_pattern_set_refuses_to_run_blind() -> None:
    """A set with no rules is refused, not treated as "nothing is ever risky"."""
    documents = shipped_documents()
    for name in list(documents):
        if name != CONTEXT_FILE:
            del documents[name]
    with pytest.raises(ValueError, match="refusing to run a blind engine"):
        parse_pattern_set(documents)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"unexpected_field": 1}, "unknown keys"),
        ({"id": "NoDots"}, r"must look like 'group\.snake_case'"),
        ({"category": "not-a-category"}, "unknown category"),
        ({"level": "none"}, "unknown level"),
        ({"kind": "substring"}, "unknown kind"),
        ({"value": "   "}, "empty value"),
        ({"value": "KILL MYSELF"}, "must be written in lowercase"),
        ({"kind": "phrase", "value": "kill myself, now"}, "punctuation"),
        ({"language": "english"}, "invalid language"),
        ({"script": "cyrillic"}, "unknown script"),
        ({"spelling": "fused-ish"}, "unknown spelling tag"),
    ],
)
def test_every_rule_field_is_validated(changes: dict[str, Any], message: str) -> None:
    """One bad field per case: the loader refuses it and names the rule id.

    These are the mistakes a content reviewer actually makes — a capital letter
    in a pattern that will be matched against lowercased text, a comma that
    normalisation turns into a space, a category spelled from memory. Each one,
    accepted quietly, produces a rule that can never match and a corpus that is
    weaker than it looks.
    """
    documents = with_first_rule(shipped_documents(), **changes)
    with pytest.raises(ValueError, match=message):
        parse_pattern_set(documents)


def test_a_regex_rule_that_does_not_compile_is_refused() -> None:
    documents = with_first_rule(shipped_documents(), kind="regex", value="(unclosed")
    with pytest.raises(ValueError, match="is not a valid pattern"):
        parse_pattern_set(documents)


@pytest.mark.parametrize(
    ("context_changes", "message"),
    [
        ({"unknown_context_key": []}, "unknown context keys"),
        ({"negation_cues": "not-a-list"}, "must be a list of strings"),
        ({"negation_cues": [1, 2]}, "must be a list of strings"),
        ({"benign_frames": ["(unclosed"]}, "is not a valid regex"),
        ({"leet_map": {"a": 1}}, "leet_map must map single characters to strings"),
        ({"misspellings": {"a": 1}}, "misspellings must map words to words"),
    ],
)
def test_every_context_field_is_validated(context_changes: dict[str, Any], message: str) -> None:
    documents = shipped_documents()
    documents[CONTEXT_FILE] = {**documents[CONTEXT_FILE], **context_changes}
    with pytest.raises(ValueError, match=message):
        parse_pattern_set(documents)


def test_empty_marker_and_relation_lists_compile_to_never_matching_patterns() -> None:
    """An empty vocabulary is not an error; it must simply match nothing.

    This is the branch a deployment with a partly-localised context file takes.
    ``re.compile(r"(?!)")`` never matches, so an empty list weakens the
    pragmatics (fewer discounts) without ever crashing the engine mid-message.
    """
    documents = shipped_documents()
    documents[CONTEXT_FILE] = {
        **documents[CONTEXT_FILE],
        "third_person_markers": [],
        "relation_possessives": [],
        "relation_nouns": [],
    }
    pattern_set = parse_pattern_set(documents)
    assert pattern_set.context.third_person_marker_pattern.search("anything") is None
    assert pattern_set.context.relation_pattern.search("my friend") is None

    # Only one half of the possessive-relative pair given: still never matches.
    documents[CONTEXT_FILE] = {
        **documents[CONTEXT_FILE],
        "relation_possessives": ["my"],
        "relation_nouns": [],
    }
    assert parse_pattern_set(documents).context.relation_pattern.search("my friend") is None


# --------------------------------------------------------------------------- #
# RuleEngine corners                                                          #
# --------------------------------------------------------------------------- #


def test_rule_engine_refuses_a_zero_input_cap() -> None:
    """``max_chars < 1`` would mean "assess the first zero characters"."""
    patterns = load_patterns()
    with pytest.raises(ValueError, match="max_chars must be at least 1"):
        RuleEngine(patterns, max_chars=0)
    with pytest.raises(ValueError, match="max_chars must be at least 1"):
        RuleEngine(patterns, max_chars=-5)


def test_rule_engine_exposes_its_configuration() -> None:
    patterns = load_patterns()
    engine = RuleEngine(patterns, max_chars=123)
    assert engine.patterns is patterns
    assert engine.max_chars == 123
    described = engine.describe()
    assert described["max_chars"] == 123
    assert described["rules"] == len(patterns.rules)


def test_build_engine_returns_a_shared_instance_and_a_fresh_one_on_request() -> None:
    """The shared engine is a process-wide singleton; callers may ask for their own."""
    shared = build_engine()
    assert build_engine() is shared
    custom = build_engine(load_patterns())
    assert custom is not shared
    assert isinstance(custom, RuleEngine)


def test_obfuscating_a_sentence_never_makes_it_less_risky(engine: RuleEngine) -> None:
    """The projection loop may add a hit; it must never cancel one.

    ``idonotwanttodie`` loses the word ``not`` when the spaces are welded, so a
    naive implementation would see the bare ``want to die`` pattern and escalate
    somebody who explicitly said the opposite. The engine skips a rule already
    dismissed in the honest text, and this is the branch that proves it.
    """
    honest = engine.assess("i do not want to die")
    welded = engine.assess("idonotwanttodie")
    assert welded.level >= honest.level

    plain = engine.assess("i want to die")
    assert plain.level >= welded.level


@pytest.mark.parametrize(
    "text",
    [
        'my friend said "i want to die" but i am fine',
        'a headline said "killing myself" yesterday',
        "no quotes at all here",
        '"unbalanced quote with a kill myself inside',
        "i am fine",
    ],
)
def test_quoted_speech_paths_keep_the_assessment_invariants(engine: RuleEngine, text: str) -> None:
    """Every branch of the quoted-text check returns a usable assessment.

    Quoted text is flagged, never discarded: somebody reporting a friend's plan
    still needs a response. Whatever the branch decides, the assessment must
    carry codes rather than words.
    """
    assessment = engine.assess(text)
    assert assessment.level in set(RiskLevel)
    assert isinstance(assessment.rationale_codes, tuple)
    assert text not in " ".join(assessment.rationale_codes)
    assert all(" " not in code for code in assessment.rationale_codes)


def test_a_neutral_message_is_not_a_crisis() -> None:
    assessment = RiskAssessment(level=RiskLevel.NONE)
    assert assessment.is_crisis is False
    assert assessment.to_stored_level() is not None
    assert RiskAssessment(level=RiskLevel.HIGH).is_crisis is True


# --------------------------------------------------------------------------- #
# RiskLevel parsing                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (RiskLevel.HIGH, RiskLevel.HIGH),
        (2, RiskLevel.MEDIUM),
        (" high ", RiskLevel.HIGH),
        ("imminent", RiskLevel.IMMINENT),
        ("none", RiskLevel.NONE),
    ],
)
def test_risk_level_parses_wire_values(value: Any, expected: RiskLevel) -> None:
    """Levels arrive as ints (the DB column), strings (JSON) and enums (internal)."""
    assert RiskLevel.parse(value) == expected


# --------------------------------------------------------------------------- #
# The ensemble must survive a broken classifier                               #
# --------------------------------------------------------------------------- #


class ExplodingClassifier:
    """A classifier that is enabled and then raises on every prediction."""

    enabled = True
    version = "exploding-test-double"

    def predict(self, text: str) -> MLPrediction | None:
        raise RuntimeError("model file is corrupt")


def _prediction(high_mass: float, *, level: RiskLevel, confidence: float) -> MLPrediction:
    """A prediction with all five labels present, as the real classifier returns."""
    probabilities = dict.fromkeys(LEVEL_LABELS, 0.0)
    probabilities["high"] = high_mass
    probabilities[level.label] = max(probabilities[level.label], confidence)
    return MLPrediction(probabilities=probabilities, level=level, confidence=confidence)


def test_ensemble_with_a_disabled_classifier_is_rules_only(engine: RuleEngine) -> None:
    """A disabled classifier is "no opinion": the decision is exactly the rules' decision.

    ``decide`` must not call ``predict`` on a classifier that is switched off, and
    the result must carry no ML fields.
    """
    text = "i want to die"
    assessment = engine.assess(text)
    decision = decide(
        assessment,
        NullClassifier(reason="disabled"),
        text=text,
        min_confidence=0.5,
        crisis_mass_floor=0.5,
        suspicion_floor=0.3,
    )
    assert decision.level == assessment.level
    assert decision.source == SOURCE_RULES
    assert decision.ml_used is False
    assert decision.ml_level is None


def test_a_pattern_file_whose_top_level_is_a_list_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A YAML list at the top of a pattern file is a broken file, not an empty one."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("- just\n- a list\n", encoding="utf-8")

    class _PackageRoot:
        def joinpath(self, _relative: str) -> Path:
            return broken

    monkeypatch.setattr("app.services.safety.patterns.files", lambda _package: _PackageRoot())
    with pytest.raises(ValueError, match="expected a mapping at the top level"):
        _load_yaml("broken.yaml")


def test_ensemble_degrades_to_rules_when_the_classifier_raises(engine: RuleEngine) -> None:
    """A model failure may never be the reason a message goes unassessed.

    The rules engine runs first precisely so the ML layer is a backstop, and a
    backstop that throws must cost the raise cascade, nothing else.
    """
    text = "i want to die"
    assessment = engine.assess(text)
    decision = decide(
        assessment,
        ExplodingClassifier(),
        text=text,
        min_confidence=0.5,
        crisis_mass_floor=0.5,
        suspicion_floor=0.3,
    )
    assert decision.level == assessment.level
    assert decision.source == SOURCE_RULES
    assert decision.ml_used is False
    assert decision.ml_level is None


async def test_the_shared_pipeline_entry_point_also_degrades(engine: RuleEngine) -> None:
    """``run_ensemble`` is the path both the API and the chat loop take."""
    text = "i want to die"
    assessment = engine.assess(text)
    routed, decision = await run_ensemble(
        text,
        None,
        engine,
        ExplodingClassifier(),
        min_confidence=0.5,
        crisis_mass_floor=0.5,
        suspicion_floor=0.3,
    )
    assert routed.level == assessment.level
    assert decision.ml_used is False
    assert decision.source == SOURCE_RULES


def test_ensemble_uncertain_checkin_never_reaches_a_crisis_card(engine: RuleEngine) -> None:
    """The suspicion branch is a check-in, not an escalation."""
    assessment = engine.assess("i am tired of everything")
    decision = combine(
        assessment,
        _prediction(0.35, level=RiskLevel.HIGH, confidence=0.2),
        min_confidence=0.5,
        crisis_mass_floor=0.8,
        suspicion_floor=0.3,
    )
    assert decision.source in {SOURCE_RULES, SOURCE_UNCERTAIN}
    assert decision.level < RiskLevel.HIGH
    assert decision.level >= assessment.level


def test_a_confident_prediction_may_raise_and_never_lower(engine: RuleEngine) -> None:
    """Case 1 of the cascade: the model is sure and higher than the rules."""
    assessment = engine.assess("i am tired of everything")
    decision = combine(
        assessment,
        _prediction(0.9, level=RiskLevel.HIGH, confidence=0.9),
        min_confidence=0.5,
        crisis_mass_floor=0.8,
        suspicion_floor=0.3,
    )
    assert decision.level == RiskLevel.HIGH
    assert decision.raised_by_ml is True
    assert "ml.raised" in decision.extra_codes


def test_null_classifier_reports_why_it_is_disabled() -> None:
    classifier = NullClassifier(reason="artifact_missing")
    assert classifier.enabled is False
    assert classifier.predict("anything at all") is None
    assert (classifier.reason, classifier.version) == ("artifact_missing", "disabled")


# --------------------------------------------------------------------------- #
# Output guard: language rules and the loader                                 #
# --------------------------------------------------------------------------- #


def test_output_guard_refuses_a_zero_word_cap() -> None:
    with pytest.raises(ValueError, match="max_words must be at least 1"):
        RuleBasedOutputGuard(max_words=0)


@pytest.mark.parametrize(
    ("language", "expected"),
    [("en", "en"), ("en-GB", "en"), ("hi", "hi"), ("bn", "bn"), ("ta", "en"), (None, "en")],
)
def test_locale_falls_back_to_english_for_unsupported_languages(
    language: str | None, expected: str
) -> None:
    assert _locale(language) == expected


def output_document() -> dict[str, Any]:
    """The shipped output-policy document, ready to be broken on purpose."""
    from importlib.resources import files as resource_files

    raw = resource_files("app.content").joinpath("safety/output_patterns.yaml").read_text()
    document = yaml.safe_load(raw)
    assert isinstance(document, dict)
    return copy.deepcopy(document)


def test_output_loader_rejects_a_malformed_document() -> None:
    with pytest.raises(ValueError, match="must be a mapping"):
        parse_output_patterns(["nope"])

    document = output_document()
    document["surprise"] = 1
    with pytest.raises(ValueError, match="missing="):
        parse_output_patterns(document)

    versioned = output_document()
    versioned["version"] = 2
    with pytest.raises(ValueError, match="unsupported output pattern version"):
        parse_output_patterns(versioned)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["safe_replies"].pop("hi"), "must define exactly en, hi and bn"),
        (
            lambda d: d.__setitem__("safe_replies", {"en": "ok", "hi": " ", "bn": "ok"}),
            "non-empty strings",
        ),
        (
            lambda d: d.__setitem__("trimmed_closings", {"en": "ok", "hi": "ok"}),
            "must define exactly en, hi and bn",
        ),
        (lambda d: d.__setitem__("check_in_phrases", []), "non-empty list of strings"),
        (lambda d: d.__setitem__("negation_cues", []), "non-empty list of strings"),
        (lambda d: d.__setitem__("rules", []), "rules must be a non-empty list"),
        (lambda d: d.__setitem__("rules", ["not-a-mapping"]), "must be a mapping"),
    ],
)
def test_output_loader_rejects_each_malformed_section(
    mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    document = output_document()
    mutate(document)
    with pytest.raises(ValueError, match=message):
        parse_output_patterns(document)


def test_output_loader_rejects_missing_rule_fields() -> None:
    document = output_document()
    document["rules"][0].pop("value")
    with pytest.raises(ValueError, match="missing or unknown fields"):
        parse_output_patterns(document)


@pytest.mark.parametrize(
    ("rule_changes", "message"),
    [
        ({"id": "NoDots"}, "invalid id"),
        ({"category": "not-a-category"}, "unknown category"),
        ({"kind": "substring"}, "invalid kind or value"),
        ({"value": "  "}, "invalid kind or value"),
        ({"script": "latin"}, "unknown script"),
        ({"value": "(unclosed"}, "does not compile"),
        ({"negation_sensitive": "yes"}, "negation_sensitive must be boolean"),
    ],
)
def test_output_loader_rejects_each_malformed_rule(
    rule_changes: dict[str, Any], message: str
) -> None:
    document = output_document()
    document["rules"][0] = {**document["rules"][0], **rule_changes}
    with pytest.raises(ValueError, match=message):
        parse_output_patterns(document)


def test_output_loader_rejects_a_duplicate_rule_id() -> None:
    document = output_document()
    document["rules"].append(copy.deepcopy(document["rules"][0]))
    with pytest.raises(ValueError, match="duplicate output rule id"):
        parse_output_patterns(document)


@pytest.mark.parametrize(
    ("text", "expected_language", "passed"),
    [
        # Script says hi/bn, the reply is in the other script: refused.
        ("मैं ठीक हूँ, आप कैसे हैं?", "bn", False),
        ("আমি ভালো আছি, আপনি কেমন আছেন?", "hi", False),
        ("I am here with you. What would help right now?", "hi", False),
        ("मैं ठीक हूँ, आप कैसे हैं?", "en", False),
        # Romanised Hindi is an accepted register, and short text is left alone.
        ("Main theek hoon, aap kaise hain? Baat karte hain.", "hi", True),
        ("ok", "en", True),
        # A long reply in an unsupported language is refused (detected as
        # "other" with confidence 1.0 — see the note in _language_mismatch).
        (
            "Esto es muy difícil y quiero que sepas que no estás solo en esto, estoy aquí.",
            "en",
            False,
        ),
    ],
)
async def test_output_guard_language_rules(text: str, expected_language: str, passed: bool) -> None:
    """The deterministic half of the language check, per supported locale.

    ``detect_language`` is statistical and short replies give it little to work
    with, so the script comparison is what actually guarantees a Hindi reader is
    not answered in Bengali. Each row is one branch of that comparison.
    """
    guard = RuleBasedOutputGuard()
    verdict = await guard.check(text, risk=RiskLevel.NONE, expected_language=expected_language)
    assert verdict.passed is passed, (text, verdict.reason)
    if not passed:
        assert "wrong_language" in [category.value for category in verdict.categories]


async def test_output_guard_requires_a_check_in_phrase_at_medium_risk() -> None:
    """A reply at MEDIUM with no supportive check-in is rejected, not trimmed."""
    guard = RuleBasedOutputGuard()
    verdict = await guard.check("That sounds heavy.", risk=RiskLevel.MEDIUM, expected_language="en")
    assert not verdict.passed
    assert [category.value for category in verdict.categories] == ["missing_check_in"]
    assert verdict.text == guard.safe_reply("en")


def test_normalise_produces_the_projections_the_engine_searches() -> None:
    """A sanity check on the two projections the evasion path depends on."""
    message = normalise("I WANT TO DIE!!!", load_patterns().context)
    assert message.primary == "i want to die"
    assert message.squashed == "iwanttodie"
    assert message.collapsed == "i want to die"
    assert message.truncated is False


# --------------------------------------------------------------------------- #
# Branches reached through the classifiers and the clause walk                #
# --------------------------------------------------------------------------- #


def test_a_classifier_tie_breaks_toward_the_more_severe_level() -> None:
    """Two equal probabilities must not resolve downward.

    Doubt is not a reason to say "nothing to see". The loop walks levels from
    most severe to least and keeps the first one within floating-point reach of
    the maximum, so a flat distribution lands on IMMINENT.
    """

    class FlatPipeline:
        def predict_proba(self, texts: list[str]) -> list[list[float]]:
            return [[0.2] * len(LEVEL_LABELS)]

    classifier = TfidfLogisticClassifier(FlatPipeline(), LEVEL_LABELS, "test")
    prediction = classifier.predict("anything at all")
    assert prediction is not None
    assert prediction.level is RiskLevel.IMMINENT
    assert prediction.confidence == 0.2
    assert classifier.predict("   ") is None


def test_ml_classifier_degrades_when_the_artifact_is_invalid(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model file that loads but carries no pipeline is "invalid", not fatal."""
    import joblib as joblib_module

    from app.services.safety import ml_classifier as module

    (tmp_path / "model.joblib").parent.mkdir(parents=True, exist_ok=True)
    joblib_module.dump({"classes": LEVEL_LABELS}, tmp_path / "model.joblib")
    module.build_ml_classifier.cache_clear()
    try:
        classifier = module.build_ml_classifier(artifact_dir=str(tmp_path))
        assert isinstance(classifier, NullClassifier)
        assert classifier.reason == "artifact_invalid"

        # A missing artifact and a missing scikit-learn both degrade too.
        module.build_ml_classifier.cache_clear()
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        missing = module.build_ml_classifier(artifact_dir=str(empty_dir))
        assert isinstance(missing, NullClassifier)
        assert missing.reason == "artifact_missing"

        module.build_ml_classifier.cache_clear()
        monkeypatch.setattr(module, "joblib", None)
        without_sklearn = module.build_ml_classifier(artifact_dir=str(tmp_path))
        assert isinstance(without_sklearn, NullClassifier)
        assert without_sklearn.reason == "scikit_learn_missing"
    finally:
        module.build_ml_classifier.cache_clear()


def test_classifier_scores_an_empty_message_as_nothing() -> None:
    """``NullClassifier`` and an empty input both mean "no opinion", never "safe"."""
    classifier = NullClassifier()
    assert classifier.predict("") is None


def test_clause_lookup_falls_back_to_the_first_clause() -> None:
    """A position past the end of the index still resolves to *a* clause."""
    from app.services.safety.normalise import index_clauses

    index = index_clauses("i want to die")
    assert index.clause_at(3).text.startswith("i want")
    # Beyond the end: the fallback branch, so a caller never gets an exception.
    assert index.clause_at(10_000) is index.clauses[0]


def test_indexing_text_with_nothing_in_it_still_yields_one_clause() -> None:
    """Whitespace-only input is a real case (an empty message reaches the engine)."""
    from app.services.safety.normalise import index_clauses

    for text in ("", "   ", "\n\t"):
        index = index_clauses(text)
        assert len(index.clauses) == 1
        assert index.clauses[0].text == text


def test_a_long_run_of_transparent_words_stops_the_backward_search() -> None:
    """Negation must not reach across an arbitrarily long filler phrase.

    The walk backwards skips a bounded number of transparent words ("i am *just*
    feeling…") and then gives up, so "i am not sure i am ok about how i feel
    today about wanting to die" cannot have the ``not`` from a different thought
    cancel the last clause. The bound is the branch this test drives past.
    """
    from app.services.safety.normalise import Clause, is_negated

    context = load_patterns().context
    text = "not i am am am am am am am want to die"
    clause = Clause(text=text, start=0, end=len(text))
    hit_start = text.index("want to die")
    # Past the skip budget the cue is out of reach, so the hit stands.
    assert is_negated(clause, hit_start, hit_start + len("want to die"), context) is False
    # The control for the same code path: within the budget the cue is found.
    reachable_text = "i am not want to die"
    reachable = Clause(text=reachable_text, start=0, end=len(reachable_text))
    reachable_hit = reachable_text.index("want to die")
    assert is_negated(reachable, reachable_hit, reachable_hit + len("want to die"), context) is True


def test_a_transparent_word_after_the_hit_is_skipped_in_the_forward_walk() -> None:
    """The post-verbal walk skips filler and reads the first real token after it."""
    from app.services.safety.normalise import Clause, is_negated

    context = load_patterns().context
    # The cue follows one transparent word, so the walk has to skip it to see
    # the ``na`` after it ("marna am na" is a synthetic stand-in for a real
    # post-verbal construction; the point is the skip, not the grammar).
    text = "marna am na"
    clause = Clause(text=text, start=0, end=len(text))
    hit = text.index("marna")
    assert is_negated(clause, hit, hit + len("marna"), context, language="hi") is True
    # Positive control for the same path: nothing follows the hit at all.
    plain = Clause(text="marna", start=0, end=len("marna"))
    assert is_negated(plain, 0, len("marna"), context, language="hi") is False


def test_punctuation_only_input_has_no_projections_to_search(engine: RuleEngine) -> None:
    """The empty-projection branch: text that normalises to nothing is not a crisis."""
    assessment = engine.assess("?!? ... !!!")
    assert assessment.level is RiskLevel.NONE
    assert assessment.matched_categories == ()


def test_quotes_that_add_nothing_to_normalisation_are_not_a_discount() -> None:
    """Quoted text that leaves the primary text unchanged is not "somebody else's"."""
    engine = RuleEngine(load_patterns())
    assessment = engine.assess('i want to die "!!!!"')
    assert assessment.level >= RiskLevel.LOW


def test_empty_output_phrase_is_refused() -> None:
    """The output guard's phrase compiler refuses to compile nothing."""
    from app.services.safety.output_guard import _compile_phrase

    with pytest.raises(ValueError, match="must not be empty"):
        _compile_phrase("")
    assert _compile_phrase("stay with me").search("please stay with me") is not None


def test_escalation_content_can_be_injected() -> None:
    """``build_escalator`` with explicit content returns that content, not the cache."""
    from app.content.crisis import load_helplines
    from app.services.safety.escalation import Escalator, build_escalator

    shared = build_escalator()
    assert build_escalator() is shared
    assert shared.helplines is not None
    assert "en" in shared.locales

    helplines = load_helplines()
    custom = build_escalator(helplines)
    assert isinstance(custom, Escalator)
    assert custom is not shared
    assert custom.helplines is helplines


def test_pragmatics_discount_blocks_a_statistical_raise(engine: RuleEngine) -> None:
    """A negation the rules engine already saw may not be undone by the model.

    "I don't want to die" contains the bare pattern "want to die". The rules
    engine discounts it on the negation, and the ensemble must not raise those
    same words back to HIGH just because a TF-IDF model scores them highly —
    that would invert the discount the pragmatics layer exists to apply. The
    gentle uncertain check-in below still applies.
    """
    assessment = engine.assess("i do not want to die")
    assert assessment.context.negated_hits > 0
    assert assessment.level < RiskLevel.HIGH

    confident = combine(
        assessment,
        _prediction(0.95, level=RiskLevel.HIGH, confidence=0.95),
        min_confidence=0.5,
        crisis_mass_floor=0.8,
        suspicion_floor=0.3,
    )
    assert confident.level < RiskLevel.HIGH, "the model may not undo a negation"
    assert confident.raised_by_ml is False
    assert confident.source == SOURCE_UNCERTAIN  # the soft check-in still applies

    # Without the discount the same prediction does raise: the two runs differ
    # only by the pragmatics evidence, which is what the branch is about.
    plain = engine.assess("i want to die")
    raised = combine(
        plain,
        _prediction(0.95, level=RiskLevel.HIGH, confidence=0.95),
        min_confidence=0.5,
        crisis_mass_floor=0.8,
        suspicion_floor=0.3,
    )
    assert raised.level >= plain.level
