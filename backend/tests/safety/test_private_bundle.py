"""Day 41: the browser's safety bundle must be the Python engine's own data.

Private Mode compiles ``backend/app/content/**`` into
``frontend/public/private/safety-bundle.json`` (see
``frontend/scripts/build-private-bundle.mjs``) and runs the rules engine, the
output guard and the crisis copy from it, on the device. This module is the gate
that keeps "compiled from the same content" true rather than merely intended:
every rule, every vocabulary list, every numeric constant, every Unicode table,
the escalation policy and the helpline set in the bundle are compared against
what the Python engine actually loads and compiles.

Two directions are covered, and both matter:

* **data** — the bundle cannot disagree with ``content/safety/*.yaml``,
  ``content/i18n/*.json`` or ``content/helplines.json``;
* **behaviour claims** — the bundle carries assertions about Python's ``\\s``,
  ``\\w``, ``\\d``, its punctuation classes and its case folding, written in a
  form a browser can use. Each claim is re-derived here from the Python engine
  over *every* code point, so a browser that follows the bundle follows Python.

The bundle's own freshness (was it rebuilt after a content edit?) is checked from
the JavaScript side by ``src/features/private/bundle/drift.test.ts``; here the
``sources`` hashes are compared with the files on disk, which fails the same edit
from this side too.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

import pytest
import yaml

from app.content.crisis import load_helplines
from app.content.i18n import load_all_localisations
from app.services.safety import normalise
from app.services.safety import rules as rules_module
from app.services.safety.base import RiskCategory, RiskLevel
from app.services.safety.escalation import Escalator
from app.services.safety.output_guard import load_output_patterns
from app.services.safety.patterns import load_patterns

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
FRONTEND_PRIVATE: Final = REPO_ROOT / "frontend" / "src" / "features" / "private"
UNICODE_DIR: Final = FRONTEND_PRIVATE / "unicode"
BUNDLE_PATH: Final = REPO_ROOT / "frontend" / "public" / "private" / "safety-bundle.json"
CASEFOLD_PATH: Final = UNICODE_DIR / "python-casefold.json"
JS_LOWERCASE_PATH: Final = UNICODE_DIR / "js-lowercase-codepoints.json"
CONTENT: Final = REPO_ROOT / "backend" / "app" / "content"
CASEFOLD_EXPORTER: Final = REPO_ROOT / "backend" / "scripts" / "export_casefold_cases.py"

#: The whole Unicode range, minus the surrogates (which no string may contain).
_CODEPOINTS: Final = [cp for cp in range(0x110000) if not 0xD800 <= cp <= 0xDFFF]


def _load_exporter() -> ModuleType:
    """Load ``scripts/export_casefold_cases.py`` the way the repo loads scripts."""
    spec = importlib.util.spec_from_file_location("export_casefold_cases", CASEFOLD_EXPORTER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bundle() -> dict[str, Any]:
    """The committed browser bundle."""
    if not BUNDLE_PATH.exists():
        pytest.fail(
            f"missing {BUNDLE_PATH} — run `npm --prefix frontend run private:bundle`",
            pytrace=False,
        )
    return cast(dict[str, Any], json.loads(BUNDLE_PATH.read_text(encoding="utf-8")))


def test_format_is_the_one_the_browser_loader_accepts(bundle: dict[str, Any]) -> None:
    assert bundle["format"] == "manovia-private-bundle/1"


def test_bundle_was_built_from_the_content_on_disk(bundle: dict[str, Any]) -> None:
    """A content edit without a rebuild is drift, and drift is a failure."""
    sources = cast(dict[str, dict[str, Any]], bundle["sources"])
    for relative, recorded in sources.items():
        path = REPO_ROOT / "backend" / "app" / relative
        assert path.exists(), f"bundle names a source that is not in the repository: {relative}"
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == recorded["sha256"], f"{relative} changed"
        assert len(raw) == recorded["bytes"], f"{relative} changed size"


# --------------------------------------------------------------------------- #
# Detection rules and the pragmatics vocabulary
# --------------------------------------------------------------------------- #


def test_rules_match_the_compiled_python_pattern_set(bundle: dict[str, Any]) -> None:
    """Every rule, field by field, in the order Python loads them."""
    compiled = load_patterns().rules
    shipped = cast(list[dict[str, Any]], bundle["engine"]["rules"])
    assert len(shipped) == len(compiled)
    for entry, rule in zip(shipped, compiled, strict=True):
        assert entry["id"] == rule.id
        assert entry["category"] == rule.category.value
        assert entry["level"] == rule.level.label
        assert entry["kind"] == rule.kind
        assert entry["value"] == rule.value
        assert entry["negation_sensitive"] is rule.negation_sensitive
        assert entry["negation_baked"] is rule.negation_baked
        assert entry["figurative_prone"] is rule.figurative_prone
        assert entry["figurative_subject"] is rule.figurative_subject
        assert entry["language"] == rule.language
        assert entry["script"] == rule.script
        assert entry["spelling"] == rule.spelling


def test_rule_ids_are_unique_and_well_formed(bundle: dict[str, Any]) -> None:
    ids = [entry["id"] for entry in cast(list[dict[str, Any]], bundle["engine"]["rules"])]
    assert len(set(ids)) == len(ids)
    assert ids, "a bundle with no rules would ship a blind engine"
    pattern = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
    assert all(pattern.match(rule_id) for rule_id in ids)


def _compiled_misspellings() -> list[tuple[str, str]]:
    """Recover the misspelling map from Python's compiled patterns, in order.

    ``patterns._compile_context`` wraps each key as ``(?<![\\w])key(?![\\w])``
    and sorts longest-source-first; the bundle ships the ordered pairs, so the
    comparison unwraps them and keeps the order — the browser must apply the same
    fixes in the same sequence or ``sucide`` would rewrite part of
    ``sucidalthoughts`` first.
    """
    recovered: list[tuple[str, str]] = []
    wrapper = re.compile(r"\(\?<!\[\\w\]\)(.*)\(\?!\[\\w\]\)")
    for pattern, replacement in load_patterns().context.misspellings:
        match = wrapper.fullmatch(pattern.pattern)
        assert match is not None, f"unexpected misspelling pattern {pattern.pattern}"
        source = match.group(1).replace("\\", "")
        recovered.append((source, replacement))
    return recovered


def test_context_vocabulary_matches_the_compiled_python_context(bundle: dict[str, Any]) -> None:
    context = load_patterns().context
    shipped = cast(dict[str, Any], bundle["engine"]["context"])
    assert shipped["leet_map"] == dict(context.leet_map)
    assert [tuple(pair) for pair in shipped["misspellings"]] == _compiled_misspellings()
    assert sorted(shipped["negation_cues"]) == sorted(context.negation_cues)
    assert sorted(shipped["negation_after_cues"]) == sorted(context.negation_after_cues)
    assert sorted(shipped["transparent_words"]) == sorted(context.transparent_words)
    assert shipped["third_person_markers"] == list(context.third_person_markers)
    assert shipped["third_person_patterns"] == [
        item.pattern for item in context.third_person_patterns
    ]
    assert shipped["fiction_frames"] == list(context.fiction_frames)
    assert shipped["relation_possessives"] == list(context.relation_possessives)
    assert shipped["relation_nouns"] == list(context.relation_nouns)
    assert shipped["first_person_markers"] == list(context.first_person_markers)
    assert shipped["timeframe_markers"] == list(context.timeframe_markers)
    assert shipped["benign_frames"] == [item.pattern for item in context.benign_frames]
    assert sorted(shipped["benign_subjects"]) == sorted(context.benign_subjects)


def _scalar(node: yaml.Node) -> str:
    """The raw text of a scalar node, or a stand-in for a composite one."""
    if isinstance(node, yaml.ScalarNode):
        return cast(str, node.value)
    return f"<{type(node).__name__}>"


def _divergent_duplicate_keys(node: yaml.Node | None, found: list[tuple[str, str, str]]) -> None:
    """Walk a composed YAML tree, collecting duplicated keys with *different* values."""
    if isinstance(node, yaml.MappingNode):
        seen: dict[str, str] = {}
        for key_node, value_node in node.value:
            key = _scalar(key_node)
            value = _scalar(value_node)
            if key in seen and seen[key] != value:
                found.append((key, seen[key], value))
            seen[key] = value
            _divergent_duplicate_keys(value_node, found)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _divergent_duplicate_keys(item, found)


def test_duplicate_yaml_keys_the_compiler_tolerates_are_harmless() -> None:
    """The compiler accepts duplicate keys because PyYAML does; prove that is safe.

    ``patterns_context.yaml`` repeats one ``misspellings`` key. js-yaml refuses
    duplicates unless it is told to behave like PyYAML, and the bundle compiler
    tells it to — so this test is what makes that tolerance safe: a duplicated key
    whose two values *differ* would mean the two loaders disagree about the
    engine's data, and must fail here rather than quietly ship one reading.
    """
    found: list[tuple[str, str, str]] = []
    for path in sorted((CONTENT / "safety").glob("*.yaml")):
        _divergent_duplicate_keys(yaml.compose(path.read_text(encoding="utf-8")), found)
    assert found == [], f"duplicate YAML keys with different values: {found}"


# --------------------------------------------------------------------------- #
# Engine constants
# --------------------------------------------------------------------------- #


def test_numeric_constants_match_the_python_engine(bundle: dict[str, Any]) -> None:
    engine = cast(dict[str, Any], bundle["engine"])
    assert engine["max_input_chars"] == normalise.DEFAULT_MAX_INPUT_CHARS
    assert engine["max_backward_tokens"] == normalise.MAX_BACKWARD_TOKENS
    assert engine["max_transparent_skips"] == normalise.MAX_TRANSPARENT_SKIPS
    assert engine["max_forward_tokens"] == normalise.MAX_FORWARD_TOKENS
    assert engine["subject_window"] == normalise.SUBJECT_WINDOW
    assert engine["window_overlap_chars"] == rules_module.WINDOW_OVERLAP_CHARS
    assert engine["max_windowed_chars"] == rules_module.MAX_WINDOWED_CHARS
    assert engine["clause_bar"] == normalise._CLAUSE_BAR


def test_vocabularies_match_the_python_enums(bundle: dict[str, Any]) -> None:
    engine = cast(dict[str, Any], bundle["engine"])
    assert engine["levels"] == {level.label: level.value for level in RiskLevel}
    assert engine["categories"] == [category.value for category in RiskCategory]
    # The four-tier database labels (ADR 0008 §4): LOW is stored as CAUTION and
    # MEDIUM as ELEVATED, so the browser must not derive them from the labels.
    assert engine["stored_levels"] == {
        level.label: level.to_stored().name.lower() for level in RiskLevel
    }
    assert engine["codes"] == {
        "negated": rules_module.CODE_NEGATED,
        "figurative": rules_module.CODE_FIGURATIVE,
        "third_person": rules_module.CODE_THIRD_PERSON,
        "quoted": rules_module.CODE_QUOTED,
        "fiction_frame": rules_module.CODE_FICTION,
        "timeframe": rules_module.CODE_TIMEFRAME,
        "empty_input": rules_module.CODE_EMPTY_INPUT,
        "truncated": rules_module.CODE_TRUNCATED,
        "windowed": rules_module.CODE_WINDOWED,
    }
    assert sorted(engine["first_person_subjects"]) == sorted(rules_module._FIRST_PERSON_SUBJECTS)
    assert engine["self_quote_pattern"] == rules_module._SELF_QUOTE.pattern


# --------------------------------------------------------------------------- #
# The Unicode claims the browser cannot derive for itself
# --------------------------------------------------------------------------- #


def test_whitespace_codepoints_are_exactly_pythons_whitespace_class(bundle: dict[str, Any]) -> None:
    """JavaScript's whitespace class is a different set from Python's."""
    unicode_tables = cast(dict[str, Any], bundle["unicode"])
    claimed = set(unicode_tables["whitespace_codepoints"])
    actual = {cp for cp in _CODEPOINTS if re.match(r"\s", chr(cp))}
    assert claimed == actual


def test_word_class_claim_holds_for_every_codepoint(bundle: dict[str, Any]) -> None:
    """The browser's word class must be Python's ``\\w`` — checked exhaustively."""
    unicode_tables = cast(dict[str, Any], bundle["unicode"])
    assert unicode_tables["word_class_body"] == "\\p{L}\\p{N}_"
    for codepoint in _CODEPOINTS:
        char = chr(codepoint)
        expected = bool(re.match(r"\w", char))
        actual = unicodedata.category(char)[0] in ("L", "N") or char == "_"
        assert expected is actual, f"U+{codepoint:04X} ({unicodedata.category(char)})"


def test_digit_class_claim_holds_for_every_codepoint(bundle: dict[str, Any]) -> None:
    unicode_tables = cast(dict[str, Any], bundle["unicode"])
    assert unicode_tables["digit_class"] == "\\p{Nd}"
    for codepoint in _CODEPOINTS:
        char = chr(codepoint)
        assert bool(re.match(r"\d", char)) is (unicodedata.category(char) == "Nd"), hex(codepoint)


def test_invisible_and_apostrophe_sets_match(bundle: dict[str, Any]) -> None:
    unicode_tables = cast(dict[str, Any], bundle["unicode"])
    assert set(unicode_tables["invisible_codepoints"]) == set(normalise._INVISIBLE)
    assert set(unicode_tables["apostrophe_codepoints"]) == {
        ord(char) for char in normalise._APOSTROPHES
    }


def _in_ranges(ranges: list[list[int]]) -> Callable[[str], bool]:
    pairs = [(int(low), int(high)) for low, high in ranges]

    def predicate(char: str) -> bool:
        codepoint = ord(char)
        return any(low <= codepoint <= high for low, high in pairs)

    return predicate


def test_punctuation_classes_match_for_every_codepoint(bundle: dict[str, Any]) -> None:
    """The browser's stripping regex is built from these ranges; prove they agree.

    ``normalise._PUNCTUATION`` keeps word characters, whitespace and the Indic
    marks, and replaces everything else with a space;
    ``_PUNCTUATION_KEEPING_BAR`` additionally keeps the clause bar. Both are
    recomputed here from the bundle's ranges and compared against Python's
    compiled classes over the whole range.
    """
    unicode_tables = cast(dict[str, Any], bundle["unicode"])
    engine = cast(dict[str, Any], bundle["engine"])
    kept_marks = _in_ranges(cast(list[list[int]], unicode_tables["kept_mark_ranges"]))
    bar = str(engine["clause_bar"])
    for codepoint in _CODEPOINTS:
        char = chr(codepoint)
        kept = bool(re.match(r"\w", char)) or bool(re.match(r"\s", char)) or kept_marks(char)
        assert bool(normalise._PUNCTUATION.match(char)) is (not kept), hex(codepoint)
        assert bool(normalise._PUNCTUATION_KEEPING_BAR.match(char)) is (
            not (kept or char == bar)
        ), hex(codepoint)


def test_latin_combining_ranges_match_for_every_codepoint(bundle: dict[str, Any]) -> None:
    unicode_tables = cast(dict[str, Any], bundle["unicode"])
    combined = _in_ranges(cast(list[list[int]], unicode_tables["latin_combining_ranges"]))
    for codepoint in _CODEPOINTS:
        char = chr(codepoint)
        assert bool(normalise._LATIN_COMBINING.match(char)) is combined(char), hex(codepoint)


def test_confusables_match_the_python_lookalike_tables(bundle: dict[str, Any]) -> None:
    unicode_tables = cast(dict[str, Any], bundle["unicode"])
    shipped = {
        int(key): value
        for key, value in cast(dict[str, str], unicode_tables["confusables"]).items()
    }
    assert shipped == {ord(source): target for source, target in normalise._CONFUSABLES.items()}


def test_casefold_overrides_are_python_casefold(bundle: dict[str, Any]) -> None:
    """Every override the browser applies must be exactly Python's casefold."""
    unicode_tables = cast(dict[str, Any], bundle["unicode"])
    overrides = cast(dict[str, str], unicode_tables["casefold_overrides"])
    assert overrides, "the fixture says 1671 code points diverge; an empty table is a bug"
    for key, value in overrides.items():
        codepoint = int(key)
        assert chr(codepoint).casefold() == value, f"U+{codepoint:04X}"


def test_casefold_fixture_is_current_and_complete() -> None:
    """The fixture the overrides come from must be a current export.

    Compared field by field, and byte for byte once the recorded Unicode version is
    the committed one, because that field is provenance rather than data: CPython
    3.11 folds with Unicode 14.0.0 and CI's 3.12 folds with 15.0.0, so requiring
    the string to match would call the fixture stale on every interpreter upgrade
    even when not one case changed. The cases themselves are compared strictly, in
    both directions - a missing entry is as much drift as a wrong one.

    Completeness in the other direction (a code point a browser lowercases but
    Python leaves alone) is asserted by ``verifyUnicodeClaim`` in the bundle
    compiler, from the JavaScript side, and by the coverage check below.
    """
    if not CASEFOLD_PATH.exists():
        pytest.fail(f"missing {CASEFOLD_PATH}", pytrace=False)
    exporter = _load_exporter()
    payload = cast(dict[str, Any], exporter.build_payload())
    committed_text = CASEFOLD_PATH.read_text(encoding="utf-8")
    committed_doc = cast(dict[str, Any], json.loads(committed_text))
    differing = cast("list[str]", exporter.drift(committed_doc, payload))
    assert not differing, (
        f"python-casefold.json is stale: {', '.join(differing)} differ on Python "
        f"{sys.version_info[0]}.{sys.version_info[1]} / Unicode "
        f"{unicodedata.unidata_version}; run "
        "`.venv/bin/python backend/scripts/export_casefold_cases.py`"
    )
    reformatted = cast(
        str,
        exporter.render(
            {**payload, "unicodedata_version": committed_doc.get("unicodedata_version")}
        ),
    )
    assert committed_text == reformatted, (
        "python-casefold.json is not written the way the exporter writes it: run "
        "`.venv/bin/python backend/scripts/export_casefold_cases.py`"
    )

    committed = committed_doc
    cases = {int(key, 16): value for key, value in cast(dict[str, str], committed["cases"]).items()}
    for codepoint, value in cases.items():
        assert chr(codepoint).casefold() == value
    if JS_LOWERCASE_PATH.exists():
        browser = cast(dict[str, Any], json.loads(JS_LOWERCASE_PATH.read_text(encoding="utf-8")))
        for codepoint in cast(list[int], browser["codepoints"]):
            assert int(codepoint) in cases, (
                f"U+{int(codepoint):04X} lowercases in a browser but has no casefold entry"
            )


def test_the_casefold_gate_ignores_only_the_unicode_version() -> None:
    """Regression: the fixture was written by CPython 3.11 (Unicode 14.0.0) and CI
    checks it with 3.12 (Unicode 15.0.0), where a byte-for-byte comparison fails on
    the version string alone. Provenance may differ; data may not, in either
    direction."""
    exporter = _load_exporter()
    payload = cast(dict[str, Any], exporter.build_payload())
    assert cast("list[str]", exporter.drift(payload, payload)) == []

    other_version = {**payload, "unicodedata_version": "99.0.0"}
    assert cast("list[str]", exporter.drift(payload, other_version)) == []
    assert cast("list[str]", exporter.drift(other_version, payload)) == []

    cases = cast(dict[str, str], payload["cases"])
    one_key = next(iter(sorted(cases)))
    for label, changed in (
        ("a wrong case", {**payload, "cases": {**cases, one_key: "not what python folds to"}}),
        ("a missing case", {**payload, "cases": {k: v for k, v in cases.items() if k != one_key}}),
        ("an extra case", {**payload, "cases": {**cases, "10ffff": "\uffff"}}),
        ("a wrong count", {**payload, "count": payload["count"] + 1}),
        ("a wrong hash", {**payload, "sha256": "0" * 64}),
        ("a different js table", {**payload, "js_lowercase_source": {"sha256": "0" * 64}}),
        ("a dropped field", {k: v for k, v in payload.items() if k != "folded_count"}),
    ):
        assert cast("list[str]", exporter.drift(payload, changed)), label


# --------------------------------------------------------------------------- #
# Output guard content
# --------------------------------------------------------------------------- #


def test_output_guard_rules_match_the_yaml_and_the_compiled_guard(bundle: dict[str, Any]) -> None:
    document = yaml.safe_load((CONTENT / "safety" / "output_patterns.yaml").read_text("utf-8"))
    compiled = load_output_patterns()
    guard = cast(dict[str, Any], bundle["output_guard"])
    shipped = cast(list[dict[str, Any]], guard["rules"])
    assert len(shipped) == len(document["rules"]) == len(compiled.rules)
    for entry, raw, rule in zip(shipped, document["rules"], compiled.rules, strict=True):
        assert entry["id"] == raw["id"] == rule.id
        assert entry["category"] == raw["category"] == rule.category.value
        assert entry["kind"] == raw["kind"]
        assert entry["value"] == raw["value"]
        assert entry["negation_sensitive"] is bool(raw.get("negation_sensitive", False))
        assert entry["negation_sensitive"] is rule.negation_sensitive
        assert entry["script"] == raw.get("script")
    assert guard["safe_replies"] == dict(compiled.safe_replies)
    assert guard["trimmed_closings"] == dict(compiled.trimmed_closings)
    assert guard["check_in_phrases"] == list(document["check_in_phrases"])
    assert guard["negation_cues"] == list(document["negation_cues"])


def test_prompts_are_the_files_on_disk(bundle: dict[str, Any]) -> None:
    """The bundle ships the reviewed prompts, byte for byte.

    The browser needs them for the output guard's prompt-echo check (the Day 29 red
    team found that leak server-side, RT-I-SP-01..03) and as the system prompt for the
    on-device model. Shipping a *copy* is only safe if the copy is verified, so this
    compares text, digest and word count against `content/prompts/`.
    """
    prompts = cast(dict[str, dict[str, Any]], bundle["prompts"])
    directory = CONTENT / "prompts"
    on_disk = sorted(path.stem for path in directory.glob("system_v*.md"))
    assert sorted(prompts) == on_disk
    for stem, entry in prompts.items():
        text = (directory / f"{stem}.md").read_text(encoding="utf-8")
        raw = text.encode("utf-8")
        assert entry["text"] == text
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest()
        assert entry["bytes"] == len(raw)
        assert entry["words"] == len(text.split())
        assert entry["path"] == f"content/prompts/{stem}.md"
        # Recorded in `sources` like every other compiled input.
        assert bundle["sources"][entry["path"]]["sha256"] == entry["sha256"]


def test_output_guard_regex_whitespace_stripping_is_pinned(bundle: dict[str, Any]) -> None:
    """Python strips formatting whitespace from an output regex before compiling.

    The browser must do the same, so the claim is pinned here: for every regex
    rule, the compiled Python pattern is the whitespace-stripped value, compiled
    case-insensitively.
    """
    guard = cast(dict[str, Any], bundle["output_guard"])
    compiled = load_output_patterns()
    shipped = cast(list[dict[str, Any]], guard["rules"])
    regexes = 0
    for entry, rule in zip(shipped, compiled.rules, strict=True):
        if entry["kind"] != "regex":
            continue
        regexes += 1
        assert rule.pattern.pattern == re.sub(r"\s+", "", entry["value"])
        assert rule.pattern.flags & re.IGNORECASE
    assert regexes > 0, "the output guard has regex rules; none reached this check"


# --------------------------------------------------------------------------- #
# Crisis content: policy, templates, helplines
# --------------------------------------------------------------------------- #


def test_escalation_policy_table_matches_python(bundle: dict[str, Any]) -> None:
    escalator = Escalator()
    crisis = cast(dict[str, Any], bundle["crisis"])
    policy_table = cast(dict[str, dict[str, Any]], crisis["policy"])
    assert set(policy_table) == {level.label for level in RiskLevel}
    for label, shipped in policy_table.items():
        policy = escalator.policy_for(RiskLevel[label.upper()])
        assert shipped["level"] == policy.level.label
        assert shipped["template_id"] == policy.template_id
        assert shipped["about_others_template_id"] == policy.about_others_template_id
        assert shipped["actions"] == [action.value for action in policy.actions]
        assert shipped["allow_llm"] is policy.allow_llm
        assert shipped["deterministic_reply"] is policy.deterministic_reply
        assert shipped["show_helplines"] is policy.show_helplines
        assert shipped["show_emergency_instruction"] is policy.show_emergency_instruction
        assert shipped["record_event"] is policy.record_event


def test_high_and_imminent_never_allow_a_model_reply(bundle: dict[str, Any]) -> None:
    """AGENTS.md safety rule 1, restated in the artifact the browser runs on."""
    crisis = cast(dict[str, Any], bundle["crisis"])
    policy_table = cast(dict[str, dict[str, Any]], crisis["policy"])
    for label in ("high", "imminent"):
        policy = policy_table[label]
        assert policy["allow_llm"] is False
        assert policy["deterministic_reply"] is True
        assert "block_llm" in policy["actions"]


def test_crisis_templates_match_the_localised_content(bundle: dict[str, Any]) -> None:
    localisations = load_all_localisations()
    crisis = cast(dict[str, Any], bundle["crisis"])
    locales = cast(dict[str, dict[str, Any]], crisis["locales"])
    assert set(locales) == set(localisations)
    for locale, shipped in locales.items():
        content = localisations[locale]
        assert shipped["placeholder_whitelist"] == list(content.placeholder_whitelist)
        templates = cast(dict[str, dict[str, Any]], shipped["templates"])
        for template_id, template in templates.items():
            expected = content.template(template_id)
            assert template["title"] == expected.title
            assert template["body"] == list(expected.body)
            assert template["helpline_intro"] == expected.helpline_intro
            assert template["emergency_instruction"] == expected.emergency_instruction
            assert template["trusted_person"] == expected.trusted_person
            assert template["safety_steps"] == list(expected.safety_steps)
            assert template["closing"] == expected.closing
            assert template["disclaimer"] == expected.disclaimer


def test_helplines_match_the_only_file_allowed_to_carry_them(bundle: dict[str, Any]) -> None:
    """AGENTS.md safety rule 6: one source of truth, and the bundle is derived.

    The comparison is against ``helplines.json`` as written, field by field, with
    the two computed contact hrefs added — the browser must not re-derive a
    dialling format. ``load_helplines()`` then proves the derived copy still
    satisfies the invariants the loader enforces, so a bundle that passed the
    compiler cannot be a broken helpline set.
    """
    raw = cast(dict[str, Any], json.loads((CONTENT / "helplines.json").read_text(encoding="utf-8")))
    crisis = cast(dict[str, Any], bundle["crisis"])
    shipped = cast(dict[str, Any], crisis["helplines"])
    assert shipped["version"] == raw["version"]
    assert shipped["last_verified"] == raw["last_verified"]
    assert shipped["source"] == raw["source"]
    assert shipped["disclaimer"] == raw["disclaimer"]
    assert shipped["regions"] == raw["regions"]
    resources = cast(list[dict[str, Any]], shipped["resources"])
    assert len(resources) == len(raw["resources"])

    verbatim = (
        "id",
        "region",
        "kind",
        "name",
        "type",
        "hours",
        "languages",
        "description",
        "source_url",
        "last_verified",
        "priority",
        "number",
        "text_keyword",
        "instructions",
        "audience",
        "url",
        "verification_note",
    )
    for entry, source in zip(resources, cast(list[dict[str, Any]], raw["resources"]), strict=True):
        for field in verbatim:
            assert entry[field] == source.get(field), f"{source['id']}.{field}"
        assert entry["needs_verification"] is bool(source.get("needs_verification", False))
        number = source.get("number")
        cleaned = re.sub(r"[^\d+]", "", number or "")
        dialable = number is not None and cleaned != ""
        assert entry["tel_href"] == (
            f"tel:{cleaned}" if dialable and source["type"] == "call" else None
        ), source["id"]
        assert entry["sms_href"] == (
            f"sms:{cleaned}" if dialable and source["type"] == "text" else None
        ), source["id"]

    content = load_helplines()
    assert {resource.id for resource in content.resources} == {entry["id"] for entry in resources}
    assert content.known_regions() == tuple(cast(list[str], shipped["regions"]))
    by_id = {resource.id: resource for resource in content.resources}
    for entry in resources:
        assert by_id[cast(str, entry["id"])].tel_href == entry["tel_href"]
        assert by_id[cast(str, entry["id"])].sms_href == entry["sms_href"]


def test_every_region_keeps_an_emergency_number_in_the_bundle(bundle: dict[str, Any]) -> None:
    content = load_helplines()
    crisis = cast(dict[str, Any], bundle["crisis"])
    resources = cast(list[dict[str, Any]], cast(dict[str, Any], crisis["helplines"])["resources"])
    for region in content.known_regions():
        emergency = content.emergency_for(region)
        assert emergency is not None and emergency.number
        candidates = [
            entry
            for entry in resources
            if entry["region"] == region and entry["kind"] == "emergency" and entry["number"]
        ]
        assert candidates, f"the bundle lost the emergency number for {region}"
        assert candidates[0]["number"] == emergency.number
