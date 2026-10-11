"""The system prompt is product surface, so it is tested like product surface.

:data:`~app.services.llm.prompts.REQUIRED_RULES` is the machine-readable version
of the nine rules in the Day 10 brief. This file asserts each one twice: once
via that table (so a missing rule is a startup failure) and once here with an
explicit, human-readable test per rule (so a *weakened* rule fails in CI with a
sentence a reviewer can read, not a regex id).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from app.services.llm import prompts as prompts_module
from app.services.llm.prompts import (
    DEFAULT_MAX_WORDS,
    LANGUAGE_NAMES,
    PROMPT_VERSION,
    UNDETERMINED,
    UNKNOWN_LANGUAGE_PHRASE,
    PromptError,
    describe_prompt,
    language_phrase,
    load_prompt,
    prompt_text,
    render_system_prompt,
    strip_header,
)

CONTENT_DIR = Path(__file__).resolve().parents[2] / "app" / "content" / "prompts"


@pytest.fixture
def prompt() -> str:
    return render_system_prompt().text


def flat(prompt: str) -> str:
    """Whitespace-flattened, so wrapping cannot hide a rule."""
    return re.sub(r"\s+", " ", prompt)


# --- the nine rules ----------------------------------------------------------


def test_rule_1_the_prompt_states_that_it_is_an_ai(prompt: str) -> None:
    text = flat(prompt)
    assert re.search(r"You are Manovia, an AI", text)
    assert "not a therapist" in text.lower()
    assert "not a person" in text.lower()


def test_rule_2_warm_and_brief_by_default(prompt: str) -> None:
    text = flat(prompt)
    assert f"about {DEFAULT_MAX_WORDS} words" in text
    assert "one short paragraph" in text
    assert "Brevity is kindness" in text
    # Warmth is in the vocabulary, not just the absence of coldness.
    assert re.search(r"Warm, plain, unhurried", text)


def test_rule_3_validates_without_toxic_positivity(prompt: str) -> None:
    text = flat(prompt)
    assert "Validate the feeling, not the outcome" in text
    assert "never offer silver linings" in text.lower()
    assert "no encouraging exclamation marks" in text.lower()
    # The two stock toxic-positivity phrases are named as wrong.
    assert "Everything happens for a reason" in text
    assert "you've got this!" in text


def test_rule_4_at_most_one_gentle_question(prompt: str) -> None:
    text = flat(prompt)
    assert "Ask at most one gentle, open question" in text
    assert "Often the right number of questions is none" in text


def test_rule_5_never_diagnoses_or_discusses_medication(prompt: str) -> None:
    text = flat(prompt)
    assert "Never diagnose" in text
    assert "Never discuss medication" in text
    assert "start, stop, or change" in text
    assert "doctor or psychiatrist" in text


def test_rule_6_never_gives_self_harm_instructions(prompt: str) -> None:
    text = flat(prompt)
    assert "Never give instructions" in text
    assert "self-harm" in text.lower()
    assert "methods, means, or details" in text


def test_rule_7_encourages_professional_help_and_trusted_people(prompt: str) -> None:
    text = flat(prompt)
    assert "professional help and one trusted person" in text
    assert "helpline" in text.lower()
    assert "not instead of it" in text


def test_rule_8_declines_to_role_play_as_a_therapist_or_human(prompt: str) -> None:
    text = flat(prompt)
    assert "Never claim to be a therapist" in text
    assert "never role-play as one" in text
    assert "decline gently" in text


def test_rule_9_responds_in_the_users_language(prompt: str) -> None:
    text = flat(prompt)
    # The default render has no detected language, so it names none and tells
    # the model to read it off the message; with a language it names that one.
    assert "Reply in the language they wrote in" in text
    assert "Reply in English" in flat(render_system_prompt(language="en").text)
    assert "If they mix languages, mix with them" in text


def test_the_required_rules_table_matches_the_tests_above() -> None:
    """A rule in the table with no test above is a rule nobody checks."""
    expected = {
        "discloses_ai",
        "brief",
        "validates_without_toxic_positivity",
        "at_most_one_question",
        "no_diagnosis",
        "no_medication",
        "no_self_harm_instructions",
        "encourages_professional_help",
        "declines_roleplay",
        "responds_in_user_language",
    }
    assert {rule_id for rule_id, _ in prompts_module.REQUIRED_RULES} == expected
    # v2 adds three retrieval-contract rules on top (tested below).
    document = load_prompt()
    assert set(document.rules_present) == expected | {
        "reference_use_only_when_it_fits",
        "reference_ignorable",
        "reference_never_overrides_safety",
    }


# --- versioning --------------------------------------------------------------


def test_the_version_is_the_filename_and_the_setting_default() -> None:
    assert (CONTENT_DIR / f"{PROMPT_VERSION}.md").exists()
    assert PROMPT_VERSION == "system_v2"  # Day 13: v2 adds the retrieval contract


def test_the_hash_is_stable_and_matches_the_file() -> None:
    document = load_prompt()
    rendered = render_system_prompt()
    # Hashed after the human-readable header is stripped: that is the text the
    # model receives, and it is the text whose change must be visible.
    prompt_only = strip_header(prompt_text(PROMPT_VERSION))
    assert document.sha256 == hashlib.sha256(prompt_only.encode()).hexdigest()
    assert len(document.short_sha) == 12
    # The rendered hash differs from the file hash: placeholders were filled.
    assert rendered.sha256 != document.sha256


def test_only_the_whitelisted_placeholders_exist() -> None:
    assert set(load_prompt().placeholders) == {"max_words", "language"}


def test_the_header_note_is_stripped_before_the_model_sees_it() -> None:
    """The blockquote at the top of the file is for a reviewer. Sending it
    would spend tokens on meta-instructions — and it names the placeholders,
    which would then render as literal values inside the prompt."""
    raw = prompt_text(PROMPT_VERSION)
    assert raw.startswith(">")
    text = render_system_prompt().text
    assert not text.startswith(">")
    assert "This file is the product's voice" not in text
    assert "CHANGELOG.md" not in text


def test_strip_header_leaves_a_prompt_with_no_header_alone() -> None:
    assert strip_header("You are Manovia.\n") == "You are Manovia."


def test_a_changelog_exists_and_documents_the_shipped_version() -> None:
    changelog = (CONTENT_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"`{PROMPT_VERSION}`" in changelog
    for rule_id, _ in prompts_module.REQUIRED_RULES:
        # Every rule in the table must be traceable to a row in the changelog,
        # or the table becomes an undocumented gate.
        assert rule_id in changelog or rule_id.replace("_", " ") in changelog


# --- rendering ---------------------------------------------------------------


def test_render_substitutes_both_placeholders() -> None:
    rendered = render_system_prompt(language="hi", max_words=90)
    assert "about 90 words" in rendered.text
    assert "Reply in Hindi" in rendered.text
    assert "{max_words}" not in rendered.text
    assert "{language}" not in rendered.text


@pytest.mark.parametrize(("code", "phrase"), sorted(LANGUAGE_NAMES.items()))
def test_known_languages_are_named(code: str, phrase: str) -> None:
    assert language_phrase(code) == phrase
    assert f"Reply in {phrase}" in render_system_prompt(language=code).text


def test_a_script_tag_still_names_the_language() -> None:
    """Hinglish is detected as hi-Latn; the prompt should still say Hindi."""
    assert language_phrase("hi-Latn") == "Hindi"
    assert render_system_prompt(language="hi-Latn").language == "hi"


@pytest.mark.parametrize("code", ["other", "", None, "und", "zz", "xx-YY"])
def test_unknown_languages_fall_back_to_a_phrase(code: str | None) -> None:
    """Detection says "other" when it has no idea. "Reply in other" is not
    something a model can act on, so the prompt falls back to a phrase that
    lets the model read the language off the message."""
    assert language_phrase(code) == UNKNOWN_LANGUAGE_PHRASE
    text = render_system_prompt(language=code).text
    assert f"Reply in {UNKNOWN_LANGUAGE_PHRASE}" in text
    assert "Reply in other" not in text


def test_the_language_code_is_recorded_not_the_phrase() -> None:
    assert render_system_prompt(language="bn").language == "bn"
    assert render_system_prompt(language=None).language == UNDETERMINED


def test_max_words_is_clamped_to_a_sane_range() -> None:
    assert render_system_prompt(max_words=1).max_words == 30
    assert render_system_prompt(max_words=100_000).max_words == 400
    assert render_system_prompt(max_words=150).max_words == 150


def test_load_prompt_is_cached() -> None:
    assert load_prompt() is load_prompt()


# --- load-time validation ----------------------------------------------------


def test_an_unknown_version_raises() -> None:
    with pytest.raises(PromptError, match="no prompt file"):
        load_prompt("system_v_does_not_exist")
    with pytest.raises(PromptError):
        prompt_text("system_v_does_not_exist")


def test_an_unknown_placeholder_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A template that can interpolate arbitrary fields is a template that can
    interpolate user text into instructions."""
    load_prompt.cache_clear()
    # The rule check runs first and would win, so it is emptied here: this test
    # is about the placeholder whitelist specifically.
    monkeypatch.setattr(prompts_module, "REQUIRED_RULES", ())
    monkeypatch.setattr(
        prompts_module,
        "prompt_text",
        lambda version: "You are an AI. Reply in {language}. Hello {user_name}.",
    )
    try:
        with pytest.raises(PromptError, match="user_name"):
            load_prompt("test_bad_placeholder")
    finally:
        load_prompt.cache_clear()


def test_a_prompt_missing_a_required_rule_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A careless edit must be a startup failure, not a silent change in how
    the product talks to somebody in distress."""
    load_prompt.cache_clear()
    monkeypatch.setattr(
        prompts_module, "prompt_text", lambda version: "You are a helpful assistant. Be brief."
    )
    try:
        with pytest.raises(PromptError, match="missing required safety rules"):
            load_prompt("test_missing_rule")
    finally:
        load_prompt.cache_clear()


def test_a_rule_hidden_in_the_header_still_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole file is validated, so a required rule cannot hide in the
    human-readable note at the top and then be stripped out."""
    load_prompt.cache_clear()
    monkeypatch.setattr(
        prompts_module,
        "prompt_text",
        lambda version: "> Never diagnose. Never discuss medication.\n\nYou are an AI.\n",
    )
    try:
        with pytest.raises(PromptError):
            load_prompt("test_header_rule")
    finally:
        load_prompt.cache_clear()


# --- metadata ----------------------------------------------------------------


def test_describe_prompt_is_metadata_only() -> None:
    described = describe_prompt()
    assert described["version"] == PROMPT_VERSION
    assert described["rules_present"]
    assert isinstance(described["characters"], int)
    # The prompt text itself is not metadata and must not be echoed into logs.
    assert "You are Manovia" not in str(described)


def test_available_versions_includes_the_shipped_one() -> None:
    from app.services.llm.prompts import available_versions

    assert PROMPT_VERSION in available_versions()


# --------------------------------------------------------------------------- #
# Day 13: system_v2 — the retrieval contract                                    #
# --------------------------------------------------------------------------- #


def test_the_default_prompt_version_is_system_v2() -> None:
    assert PROMPT_VERSION == "system_v2"
    # And the default version is the one that loads with every rule intact.
    load_prompt()


def test_system_v2_extra_rules_are_present_and_enforced() -> None:
    from app.services.llm.prompts import EXTRA_REQUIRED_RULES

    extra = EXTRA_REQUIRED_RULES["system_v2"]
    ids = [rule_id for rule_id, _pattern in extra]
    assert ids == [
        "reference_use_only_when_it_fits",
        "reference_ignorable",
        "reference_never_overrides_safety",
    ]
    # Each rule's regex must actually match the file's rendering of that rule
    # (the ids live in the table; the file carries the prose).
    flattened = re.sub(r"\s+", " ", prompt_text("system_v2"))
    for rule_id, pattern in extra:
        assert re.search(pattern, flattened), f"{rule_id} rule text missing from system_v2.md"


def test_system_v2_states_the_retrieval_contract() -> None:
    text = re.sub(r"\s+", " ", prompt_text("system_v2"))
    assert "Reference material" in text
    assert "give general supportive guidance" in text
    assert "professional help" in text
    assert "never something the user wrote" in text


def test_v1_does_not_carry_the_v2_only_rules() -> None:
    from app.services.llm.prompts import EXTRA_REQUIRED_RULES

    v1_ids = {rule_id for rule_id, _ in EXTRA_REQUIRED_RULES.get("system_v1", ())}
    assert "reference_use_only_when_it_fits" not in v1_ids
    text = re.sub(r"\s+", " ", prompt_text("system_v1"))
    assert "Reference material" not in text


def test_a_v2_file_missing_an_extra_rule_fails_the_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.llm import prompts as mod

    # Remove the "use only when it fits" sentence: the rule's regex must stop
    # matching, and the load must fail naming the rule id.
    rule_id, pattern = mod.EXTRA_REQUIRED_RULES["system_v2"][0]
    broken = re.sub(
        r"- Use a passage only when[^-]*?your own words\.",
        "- Reference passages are usually relevant; use them freely.",
        prompt_text("system_v2"),
    )
    assert re.search(pattern, re.sub(r"\s+", " ", broken)) is None, "mutation removed the rule"
    monkeypatch.setattr(mod, "prompt_text", lambda version="": broken)
    mod.load_prompt.cache_clear()
    try:
        with pytest.raises(PromptError, match=rule_id):
            mod.load_prompt("system_v2")
    finally:
        mod.load_prompt.cache_clear()
