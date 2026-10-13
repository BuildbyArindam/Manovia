"""The explainability tool: what it must catch, and what it must never do.

Two separate claims, tested separately.

*What it must catch*: a model leaning on one word. The shortcut signals
(dominance, off-lexicon, copy-paste) are the reason the tool exists, so they are
asserted against synthetic explanations where the answer is known by construction
— a model whose attributions are all on one token must be reported as dominant.

*What it must never do*: leak. The tool prints message text and attributions to a
developer's terminal, which is exactly the shape of thing that ends up in a bug
report. Two guards: the module refuses to run in the same process as the trainer
(attributions must describe the saved artefact, not the model that was just
fitted), and special tokens must never be attributed (a ``<s>`` token carrying the
prediction is a bug, not an insight).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from manovia_ml.training.safety.explain import (
    DOMINANCE_THRESHOLD,
    RISK_LEXICON,
    TRAINER_MODULE,
    Attribution,
    Explanation,
    shortcut_findings,
)

pytest.importorskip("torch", reason='install the `training` extra: pip install -e ".[training]"')


# --------------------------------------------------------------------------- #
# The shortcut signals                                                         #
# --------------------------------------------------------------------------- #


def _explanation(
    example_id: str,
    *,
    text: str,
    lang: str = "en",
    true_label: str = "high",
    predicted: str = "high",
    attributions: tuple[tuple[str, float, str], ...] = (),
) -> Explanation:
    """One explanation with the two read-outs derived the way the tool derives them.

    ``explain_examples`` computes ``dominance`` and ``top_token_off_lexicon`` from
    the attribution list; mirroring that here means the tests below exercise
    ``shortcut_findings`` (the aggregation) against inputs shaped exactly like the
    real ones, rather than against hand-set booleans.
    """

    ranked = sorted(attributions, key=lambda item: item[1], reverse=True)
    positive = sum(value for _, value, _ in ranked if value > 0)
    dominance = (ranked[0][1] / positive) if positive > 0 and ranked[0][1] > 0 else 0.0
    top_word = ranked[0][2].casefold() if ranked else ""
    off_lexicon = bool(top_word) and top_word not in RISK_LEXICON.get(lang, frozenset())
    return Explanation(
        example_id=example_id,
        text=text,
        lang=lang,
        true_label=true_label,
        predicted_level=predicted,
        confidence=0.9,
        prediction_set=(predicted,),
        attributions=tuple(
            Attribution(token=token, value=value, word=word) for token, value, word in ranked
        ),
        dominance=dominance,
        top_token_off_lexicon=off_lexicon,
    )


def test_a_single_dominant_token_is_reported() -> None:
    """One token holding most of the mass is the headline shortcut signal.

    Both rows carry the same *kind* of message; the difference is only in how the
    attributions are spread, which is exactly the difference the tool exists to
    surface.
    """

    spread = _explanation(
        "a",
        text="i keep thinking of ways to die",
        attributions=(
            ("die", 0.3, "die"),
            ("harm", 0.25, "harm"),
            ("dead", 0.2, "dead"),
            ("end", 0.15, "end"),
            ("life", 0.1, "life"),
        ),
    )
    concentrated = _explanation(
        "b",
        text="i keep thinking of ways to die",
        attributions=(
            ("homework", 0.95, "homework"),
            ("die", 0.01, "die"),
            ("harm", 0.02, "harm"),
            ("dead", 0.03, "dead"),
        ),
    )

    findings = shortcut_findings([spread, concentrated])

    assert [item["id"] for item in findings["single_token_dominant"]] == ["b"]
    assert findings["verdict"] == "review these rows by hand"


def test_a_clean_batch_reports_a_clean_verdict() -> None:
    spread = _explanation(
        "a",
        text="i keep thinking of ways to die",
        attributions=(
            ("die", 0.3, "die"),
            ("harm", 0.25, "harm"),
            ("dead", 0.2, "dead"),
            ("end", 0.15, "end"),
            ("life", 0.1, "life"),
        ),
    )

    findings = shortcut_findings([spread])

    assert findings["reviewed"] == 1
    assert findings["single_token_dominant"] == []
    assert findings["top_token_off_lexicon"] == []
    assert findings["verdict"] == "clean"


def test_an_off_lexicon_top_token_is_reported() -> None:
    """The model keying on a word a reviewer would not have chosen."""

    on_lexicon = _explanation(
        "a",
        text="i have thoughts of dying",
        attributions=(("die", 0.9, "die"), ("harm", 0.1, "harm")),
    )
    off_lexicon = _explanation(
        "b",
        text="i have thoughts of homework",
        attributions=(("homework", 0.9, "homework"), ("die", 0.1, "die")),
    )

    findings = shortcut_findings([on_lexicon, off_lexicon])

    assert [item["id"] for item in findings["top_token_off_lexicon"]] == ["b"]


def test_copy_paste_attributions_across_categories_are_reported() -> None:
    """The same words lighting up for different kinds of message is a shortcut.

    The tool compares the top-five words, and deliberately ignores pairs that
    share both a language and a true label — two suicidal-ideation messages in
    English keying on the same five words is what a working model looks like.
    """

    suicidal = _explanation(
        "a",
        text="i want to hurt myself again tonight",
        attributions=tuple(
            (word, 0.2, word) for word in ("want", "hurt", "myself", "again", "tonight")
        ),
    )
    abuse = _explanation(
        "b",
        text="wuh chahni nijeke aghat korte abu raate",
        lang="hi-Latn",
        true_label="imminent",
        attributions=tuple(
            (word, 0.2, word) for word in ("want", "hurt", "myself", "again", "tonight")
        ),
    )

    findings = shortcut_findings([suicidal, abuse])

    assert findings["copy_paste_pairs"], "the overlap in the top five must be flagged"
    assert findings["copy_paste_pairs"][0]["shared"] == [
        "again",
        "hurt",
        "myself",
        "tonight",
        "want",
    ]


def test_the_same_words_for_the_same_kind_of_message_are_not_a_shortcut() -> None:
    """Two English HIGH messages keying on the same words is the model working."""

    left = _explanation(
        "a",
        text="i want to hurt myself again tonight",
        attributions=tuple(
            (word, 0.2, word) for word in ("want", "hurt", "myself", "again", "tonight")
        ),
    )
    right = _explanation(
        "b",
        text="i want to hurt myself again tomorrow",
        attributions=tuple(
            (word, 0.2, word) for word in ("want", "hurt", "myself", "again", "tomorrow")
        ),
    )

    assert shortcut_findings([left, right])["copy_paste_pairs"] == []


def test_the_dominance_threshold_is_between_zero_and_one() -> None:
    assert 0.0 < DOMINANCE_THRESHOLD < 1.0


def test_an_empty_batch_is_not_a_crash() -> None:
    findings = shortcut_findings([])

    assert findings["reviewed"] == 0
    assert findings["verdict"] == "clean"


# --------------------------------------------------------------------------- #
# The guards                                                                   #
# --------------------------------------------------------------------------- #


def test_the_tool_refuses_to_run_in_the_trainers_process() -> None:
    """Attributions describe the artefact on disk, not the model just fitted.

    Run in a fresh interpreter with the trainer module already imported: the
    guard must fire, because an in-process run would attribute a model whose
    weights are still in memory and whose artefact was written minutes ago.
    """

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import importlib; importlib.import_module({TRAINER_MODULE!r}); "
            "import manovia_ml.training.safety.explain as e; e._guard()",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=600,
        check=False,
    )
    assert completed.returncode != 0
    assert "fresh process" in (completed.stdout + completed.stderr)


def test_the_review_script_runs_from_the_artefact_alone() -> None:
    """No training code in the child: the CLI path a developer actually uses."""

    completed = subprocess.run(
        [sys.executable, "-m", "manovia_ml.training.safety.explain", "--help"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--artifact" in completed.stdout
    assert "--n" in completed.stdout
