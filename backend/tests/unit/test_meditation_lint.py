"""Day 45: the deterministic script linter — positive and negative pins.

Every finding the linter can raise is pinned to a piece of text that must
trigger it, and the curated catalogue's own strings are pinned to pass.
This is the gate the brief names: forbidden content (trance, medical
claims, promises, closed-eye instructions while moving) plus length,
pacing and clarity.
"""

from __future__ import annotations

from typing import Literal

from app.services.meditation.content import content_for, preface_for
from app.services.meditation.lint import LintCategory, LintReport, lint_passage, lint_script


def _clean_script(
    *,
    language: Literal["en", "hi", "bn"] = "en",
    context: Literal["seated", "walking", "driving", "unknown"] = "unknown",
    eyes_open: bool = True,
    budget: int = 400,
) -> str:
    """A structurally complete script built from the curated catalogue."""
    content = content_for(language)
    parts = [
        preface_for(language, context),
        content.stop_cue,
        content.eyes_open if eyes_open else content.eyes_closed,
        content.openings["calm"][0],
        content.passages["calm"][0],
        content.passages["calm"][1],
        content.breath_cues[0],
        content.passages["calm"][2],
        content.closings["calm"][0],
    ]
    del budget
    return "\n\n".join(parts)


def _lint(
    text: str,
    context: str = "seated",
    eyes_open: bool = True,
    language: str = "en",
) -> LintReport:
    """The linter with the budget set to the text's own size, so tests pin
    content findings instead of fighting the word budget."""
    return lint_script(
        text,
        language=language,  # type: ignore[arg-type]
        context=context,  # type: ignore[arg-type]
        eyes_open=eyes_open,
        word_budget=len(text.split()),
    )


def test_clean_curated_script_passes() -> None:
    for language in ("en", "hi", "bn"):
        text = _clean_script(language=language)
        report = lint_script(
            text,
            language=language,
            context="unknown",
            eyes_open=True,
            word_budget=len(text.split()),
        )
        assert report.passed, report.findings


def test_trance_induction_is_flagged() -> None:
    text = _clean_script() + "\n\nNow let your mind go blank as you float away."
    report = _lint(text, "seated")
    assert not report.passed
    assert report.count(LintCategory.TRANCE_INDUCTION) >= 1


def test_empty_your_mind_is_flagged() -> None:
    text = _clean_script() + "\n\nTry to empty your mind completely."
    report = _lint(text, "seated")
    assert report.count(LintCategory.TRANCE_INDUCTION) >= 1


def test_medical_claim_is_flagged() -> None:
    text = _clean_script() + "\n\nThis practice will lower your blood pressure."
    report = _lint(text, "seated")
    assert report.count(LintCategory.MEDICAL_CLAIM) >= 1


def test_not_treatment_is_not_a_medical_claim() -> None:
    # The preface's own disclosure must never be a finding.
    text = _clean_script()
    assert "not treatment" in text.lower()
    report = _lint(text, "unknown")
    assert report.count(LintCategory.MEDICAL_CLAIM) == 0


def test_treatment_for_is_flagged_when_not_negated() -> None:
    text = _clean_script() + "\n\nUse this as treatment for your insomnia."
    report = _lint(text, "seated")
    assert report.count(LintCategory.MEDICAL_CLAIM) >= 1


def test_promise_is_flagged() -> None:
    text = _clean_script() + "\n\nYou will feel calm in just minutes."
    report = _lint(text, "seated")
    assert report.count(LintCategory.PROMISE) >= 1


def test_negated_promise_statement_passes() -> None:
    # "nothing here will continue on its own" is a negation, not a promise.
    text = _clean_script()
    report = _lint(text, "seated")
    assert report.count(LintCategory.PROMISE) == 0


def test_closed_eye_instruction_flagged_while_eyes_open() -> None:
    text = _clean_script() + "\n\nNow close your eyes slowly."
    report = _lint(text, "walking")
    assert report.count(LintCategory.EYES_CLOSED) >= 1


def test_negated_closed_eye_mention_passes() -> None:
    text = _clean_script() + "\n\nThere is no closing your eyes in this practice."
    report = _lint(text, "walking")
    assert report.count(LintCategory.EYES_CLOSED) == 0


def test_closed_eye_scan_skipped_for_the_eyes_closed_variant() -> None:
    # In the seated eyes-closed variant, closed eyes are the point.
    content = content_for("en")
    text = "\n\n".join(
        [
            preface_for("en", "seated"),
            content.stop_cue,
            content.eyes_closed,
            content.openings["calm"][0],
            content.passages["calm"][0],
            content.closings["calm"][0],
        ]
    )
    report = _lint(text, "seated", eyes_open=False)
    assert report.count(LintCategory.EYES_CLOSED) == 0


def test_stillness_assumption_flagged_while_driving() -> None:
    text = _clean_script(context="driving") + "\n\nLie down and get comfortable."
    report = _lint(text, "driving")
    assert report.count(LintCategory.CONTEXT_MISMATCH) >= 1


def test_stillness_assumption_fine_seated() -> None:
    text = _clean_script(context="seated") + "\n\nLie down and get comfortable."
    report = _lint(text, "seated")
    assert report.count(LintCategory.CONTEXT_MISMATCH) == 0


def test_jargon_is_flagged() -> None:
    text = _clean_script() + "\n\nYour cortisol levels will drop."
    report = _lint(text, "seated")
    assert report.count(LintCategory.JARGON) >= 1


def test_missing_stop_cue_is_flagged() -> None:
    content = content_for("en")
    # Neither the preface nor the stop cue: nothing in the script offers
    # the stop-at-any-time guarantee.
    text = "\n\n".join(
        [
            content.eyes_open,
            content.openings["calm"][0],
            content.passages["calm"][0],
            content.closings["calm"][0],
        ]
    )
    report = _lint(text, "seated")
    assert not report.passed
    assert any(f.code == "missing_stop_cue" for f in report.findings)


def test_missing_driving_warning_is_flagged_for_driving() -> None:
    # A driving script without the pull-over preface is a safety gap.
    content = content_for("en")
    text = "\n\n".join(
        [
            content.preface["default"],  # seated preface: no driving warning
            content.stop_cue,
            content.eyes_open,
            content.openings["calm"][0],
            content.closings["calm"][0],
        ]
    )
    report = _lint(text, "driving")
    assert any(f.code == "missing_driving_warning" for f in report.findings)


def test_word_budget_floor() -> None:
    content = content_for("en")
    text = "\n\n".join(
        [
            preface_for("en", "seated"),
            content.stop_cue,
            content.eyes_open,
            content.openings["calm"][0],
            content.passages["calm"][0],
            content.closings["calm"][0],
        ]
    )
    report = lint_script(text, language="en", context="seated", eyes_open=True, word_budget=2000)
    assert any(f.code == "word_budget" for f in report.findings)


def test_word_budget_relaxes_to_min_achievable() -> None:
    content = content_for("en")
    text = "\n\n".join(
        [
            preface_for("en", "seated"),
            content.stop_cue,
            content.eyes_open,
            content.openings["calm"][0],
            content.passages["calm"][0],
            content.closings["calm"][0],
        ]
    )
    words = len(text.split())
    # Long budget the pool cannot fill: the floor relaxes to what was
    # actually achievable, and the script passes.
    report = lint_script(
        text,
        language="en",
        context="seated",
        eyes_open=True,
        word_budget=2000,
        min_achievable_words=words,
    )
    assert report.passed, report.findings


def test_sentence_too_long_is_flagged() -> None:
    long_sentence = "and ".join(["word"] * 45)
    text = _clean_script() + f"\n\nNotice {long_sentence}."
    report = _lint(text, "seated")
    assert any(f.code == "sentence_too_long" for f in report.findings)


def test_exclamation_noise_is_flagged() -> None:
    text = _clean_script() + "\n\nWow! Amazing! Incredible!"
    report = _lint(text, "seated")
    assert any(f.code == "exclamations" for f in report.findings)


def test_lint_passage_rejects_forbidden_content() -> None:
    for bad in (
        "Empty your mind and let yourself float away gently.",
        "This will cure your insomnia and lower your blood pressure.",
        "You will definitely feel relaxed in just minutes.",
        "Close your eyes and relax.",
        "Your cortisol will drop as your vagus nerve activates.",
    ):
        report = lint_passage(bad, language="en", min_words=10, max_words=90)
        assert not report.passed, bad


def test_lint_passage_accepts_clean_copy() -> None:
    good = (
        "Notice the sound of your breathing for a moment. You do not need to "
        "change it in any way. Let it simply be what it is, for a few slow breaths."
    )
    report = lint_passage(good, language="en", min_words=30, max_words=90)
    assert report.passed, report.findings


def test_lint_passage_enforces_the_word_window() -> None:
    short = "Breathe in. Breathe out."
    assert not lint_passage(short, language="en", min_words=30, max_words=90).passed
    long = " ".join(["word"] * 120)
    assert not lint_passage(long, language="en", min_words=30, max_words=90).passed


def test_report_as_dict_has_closed_codes_only() -> None:
    text = _clean_script() + "\n\nLet your mind go blank."
    report = lint_script(
        text,
        language="en",
        context="seated",
        eyes_open=True,
        word_budget=len(text.split()),
    )
    payload = report.as_dict()
    assert payload["passed"] is False
    findings = payload["findings"]
    assert isinstance(findings, list)
    for finding in findings:
        assert isinstance(finding["code"], str)
        assert isinstance(finding["count"], int)
