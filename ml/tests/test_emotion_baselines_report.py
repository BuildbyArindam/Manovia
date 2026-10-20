"""Baselines and the report renderer.

The baselines are the numbers a fine-tuned model has to beat, so the harness
that measures them is worth testing on its own: an injected analyzer keeps the
test offline, and the real lexicon analyzer is used when the backend checkout is
importable (which it is in this repository, and not on PyPI).
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from manovia_ml.paths import ml_root
from manovia_ml.training.emotion import baselines as baselines_module
from manovia_ml.training.emotion import evaluate as evaluate_module
from manovia_ml.training.emotion import report as report_module
from manovia_ml.training.emotion.baselines import (
    BASELINE_MODEL_ID,
    BASELINE_THRESHOLD,
    available_baselines,
    checkpoint_is_cached,
    run_baseline,
)
from manovia_ml.training.emotion.data import EmotionExample, summary
from manovia_ml.training.emotion.evaluate import (
    CAUSES,
    ErrorCase,
    error_analysis,
    score_predictions,
)
from manovia_ml.training.emotion.report import (
    cause_summary,
    comparison_table,
    error_table,
    per_label_table,
    render_report,
    write_model_card,
)
from manovia_ml.training.emotion.taxonomy import EMOTIONS

REPO_ROOT = ml_root().parent


class _FakeAnalyzer:
    """The smallest thing that satisfies the analyzer contract the baselines use."""

    name = "fake"

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    def analyze(self, text: str, lang: str | None = None) -> object:  # noqa: ARG002
        class _Result:
            def __init__(self, scores: dict[str, float]) -> None:
                self.scores = scores

        return _Result(self._scores)


def _examples() -> list[EmotionExample]:
    return [
        EmotionExample(
            id="a",
            text="I finished everything on time, what a relief",
            labels=("calm",),
            lang="en",
            source="s",
        ),
        EmotionExample(
            id="b",
            text="nobody called me back all week",
            labels=("loneliness",),
            lang="en",
            source="s",
        ),
    ]


def test_run_baseline_returns_probabilities_for_every_label() -> None:
    analyzer = _FakeAnalyzer(dict.fromkeys(EMOTIONS, 0.1) | {"calm": 0.9})
    result = run_baseline("keyword", _examples(), analyzer=analyzer)
    assert result.labels == EMOTIONS
    assert len(result.probabilities) == 2
    assert all(len(row) == len(EMOTIONS) for row in result.probabilities)
    # Only `calm` clears the operating point, and the set is in taxonomy terms.
    assert result.predict_sets()[0] == frozenset({"calm"})
    assert BASELINE_THRESHOLD == 0.5


def test_available_baselines_reports_a_reason_rather_than_raising() -> None:
    availability = available_baselines()
    assert set(availability) == {"keyword", "sentiment", "baseline"}
    # The off-the-shelf checkpoint cannot be measured in a sandbox without
    # Hugging Face access; whatever the verdict, it comes with a reason string.
    assert isinstance(availability["baseline"], str)


def test_available_baselines_admits_a_checkpoint_it_cannot_reach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importable is not runnable: the checkpoint is a download.

    Regression: `available_baselines()` used to call `baseline` runnable whenever
    the `nlp` extra imported, so `python -m ...report --baselines baseline`
    crashed with a traceback from inside the Hugging Face loader on any offline
    machine — which is every machine this pipeline is built for.

    The two imports the function branches on are stubbed, so *this test* decides
    which path runs. An earlier version branched on `availability["keyword"] == ""`
    instead, which is not the same question: on a machine with the backend
    installed and no `nlp` extra (this repository's own dev venv after
    `pip install -e "ml[ml,dev]"`, and the Day-49 sandbox) the function correctly
    answers "transformers is missing" and the test failed — a machine-dependent
    test rather than a wrong behaviour. Day 49, fresh clone.
    """

    monkeypatch.setitem(sys.modules, "transformers", types.ModuleType("transformers"))
    hf_stub = types.ModuleType("app.services.nlp.hf")
    hf_stub.HFEmotionAnalyzer = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.services.nlp.hf", hf_stub)

    def cached(*args: Any, **kwargs: Any) -> str:
        return ""

    def not_cached(*args: Any, **kwargs: Any) -> str:
        return "off-the-shelf checkpoint X is not in the local Hugging Face cache"

    availability = available_baselines()
    assert set(availability) == {"keyword", "sentiment", "baseline"}

    # With both imports satisfied, the cache probe is what decides.
    monkeypatch.setattr(baselines_module, "checkpoint_is_cached", cached)
    assert available_baselines()["baseline"] == ""
    monkeypatch.setattr(baselines_module, "checkpoint_is_cached", not_cached)
    assert available_baselines()["baseline"].endswith("Hugging Face cache")


def test_available_baselines_names_transformers_when_the_extra_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch a dev venv without the `nlp` extra takes has to name the fix.

    Day 49 regression: this is the combination the sandbox hit (backend
    importable, no `transformers`), and no test pinned the message it produces.
    """

    monkeypatch.setitem(sys.modules, "transformers", None)

    baseline = available_baselines()["baseline"]

    assert "transformers" in baseline
    assert "nlp" in baseline


def test_available_baselines_explains_a_backend_it_cannot_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A machine without the backend gets a reason, not a traceback or a blank.

    Forced deterministically: a `None` entry in `sys.modules` makes the import
    raise wherever this runs, which gives the branch CI takes every time a test
    on a machine that normally takes the other one.
    """

    monkeypatch.setitem(sys.modules, "app.services.nlp.keyword", None)
    monkeypatch.setitem(sys.modules, "app.services.nlp.sentiment", None)

    availability = available_baselines()

    assert availability["keyword"].startswith("backend not importable:")
    # `None` in sys.modules raises ModuleNotFoundError; a missing dependency
    # raises ImportError. Both mean "the backend could not be imported", and the
    # reason has to name which, because the two have different fixes.
    assert any(name in availability["keyword"] for name in ("ImportError", "ModuleNotFoundError"))
    assert availability["sentiment"] == availability["keyword"]
    assert availability["baseline"], "the checkpoint verdict must not be blank"


def test_checkpoint_is_cached_names_the_checkpoint_and_the_cache() -> None:
    reason = checkpoint_is_cached("Example/definitely-not-downloaded")
    assert reason, "a checkpoint that was never downloaded cannot be runnable here"
    assert "Example/definitely-not-downloaded" in reason
    assert "cache" in reason
    assert BASELINE_MODEL_ID == "SamLowe/roberta-base-go_emotions"


def test_the_report_cli_survives_a_baseline_that_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tiny_run_stub: dict[str, Any]
) -> None:
    """One broken baseline must not cost the reader the whole report.

    Regression for the same root cause as above, one level down: the CLI called
    `run_baseline` outside any guard, so the trained model's numbers — already
    computed by then — were thrown away with the traceback. The report now
    carries a line saying which baseline is missing and why.

    Only two things are stubbed: loading the artefact and scoring it (a real
    training run is `test_emotion_train.py`'s job). Everything under test here is
    real — the argument parsing, the baseline guard, the table, the file.
    """

    def stub_cached(*args: Any, **kwargs: Any) -> str:
        return ""

    def exploding(name: str, examples: Any, **kwargs: Any) -> Any:
        if name == "baseline":
            raise RuntimeError("checkpoint could not be downloaded")
        return run_baseline(name, examples, **kwargs)

    class _Artifact:
        manifest = tiny_run_stub["manifest"]
        base_model = "tiny://xlm-roberta"
        labels = EMOTIONS
        directory = tmp_path / "artifact"

    def stub_evaluate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return dict(tiny_run_stub["payload"])

    def stub_load(path: Any) -> Any:
        return _Artifact()

    # Declare every baseline runnable so the CLI reaches the runner and the
    # *guard* is what is under test. Patch the gate itself rather than the cache
    # probe behind it: on CI's `ml` job there is no backend, so the probe is never
    # consulted and the first version of this test never reached the guard at all.
    monkeypatch.setattr(
        report_module, "available_baselines", lambda: {"keyword": "", "baseline": ""}
    )
    monkeypatch.setattr(report_module, "run_baseline", exploding)
    monkeypatch.setattr(report_module, "load_artifact", stub_load)
    # `evaluate_artifact` is imported *inside* `_main`, so the patch has to land
    # on the module it is imported from, not on `report`'s namespace.
    monkeypatch.setattr(evaluate_module, "evaluate_artifact", stub_evaluate)
    data = tmp_path / "held_out.jsonl"
    data.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"id": "a", "text": "rain on the window", "labels": ["calm"], "lang": "en"},
                {"id": "b", "text": "the bus left without me", "labels": ["sadness"], "lang": "hi"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "report.md"
    code = report_module._main(
        [
            "--artifact",
            str(tmp_path / "artifact"),
            "--data",
            str(data),
            "--baselines",
            "keyword,baseline",
            "--out",
            str(out),
            "--bootstrap",
            "20",
        ]
    )

    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "fine-tuned (this run)" in text
    assert "keyword" in text
    assert "baseline `baseline` was **not run here**" in text
    assert "RuntimeError" in text
    assert "could not be downloaded" in text


@pytest.mark.skipif(
    not (REPO_ROOT / "backend" / "app" / "services" / "nlp" / "keyword.py").is_file(),
    reason="needs the backend checkout",
)
def test_the_shipped_keyword_chain_runs_through_the_baseline_harness() -> None:
    pytest.importorskip("app.services.nlp.keyword", reason="needs the backend checkout")
    result = run_baseline("keyword", _examples())
    assert len(result.probabilities) == 2
    assert result.predict_sets()[0], (
        "the lexicon should name something for a clear joy/calm sentence"
    )
    assert "lexicon" in result.notes


def _payload() -> dict[str, Any]:
    probabilities = [
        [0.8, 0.1, 0.0, 0.0, 0.7, 0.0, 0.1, 0.0, 0.1],
        [0.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.6, 0.0, 0.2],
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.95],
    ]
    examples = [
        EmotionExample(
            id="a",
            text="the deadline is close and my chest is tight",
            labels=("anxiety",),
            lang="en",
            source="s",
        ),
        EmotionExample(
            id="b",
            text="nobody calls me any more",
            labels=("loneliness", "sadness"),
            lang="hi",
            source="s",
        ),
        EmotionExample(
            id="c",
            text="the shop is next to the station",
            labels=("neutral",),
            lang="bn",
            source="s",
        ),
    ]
    return score_predictions(
        examples,
        probabilities,
        labels=EMOTIONS,
        thresholds=dict.fromkeys(EMOTIONS, 0.5),
        split="test",
        bootstrap_resamples=100,
        calibration_bins=4,
        seed=3,
    )


def test_score_predictions_reports_per_language_and_calibration() -> None:
    payload = _payload()
    assert payload["rows"] == 3
    assert set(payload["per_language"]) == {"en", "hi", "bn"}
    assert payload["per_language"]["en"]["rows"] == 1
    assert 0.0 <= payload["ece"] <= 1.0
    assert payload["macro_f1_ci"][0] <= payload["macro_f1"] <= payload["macro_f1_ci"][1]
    assert payload["per_label_f1"]["calm"] is None or payload["per_label_f1"]["calm"] == 0.0
    # The confusion table is the argmax view and is keyed "true|predicted".
    assert all("|" in key for key in payload["primary_confusion"])


def test_error_analysis_groups_mistakes_by_cause() -> None:
    cases = _error_cases()
    causes = {case.cause for case in cases}
    assert cases, "two of the three rows are wrong on purpose"
    assert causes <= set(CAUSES)
    assert all(case.text for case in cases)
    # The Hindi row is a language the model was not trained on, so it is tagged
    # as such rather than as a label confusion.
    assert any(case.cause == "untrained_language" for case in cases)


def _error_cases() -> list[ErrorCase]:
    examples = [
        EmotionExample(
            id="a",
            text="the deadline is close and my chest is tight",
            labels=("anxiety",),
            lang="en",
            source="s",
        ),
        EmotionExample(
            id="b",
            text="nobody calls me any more",
            labels=("loneliness", "sadness"),
            lang="hi",
            source="s",
        ),
        EmotionExample(
            id="c",
            text="the shop is next to the station",
            labels=("neutral",),
            lang="bn",
            source="s",
        ),
    ]
    probabilities = [
        # Wrong on purpose: it invents joy and misses anxiety.
        [0.8, 0.1, 0.0, 0.0, 0.7, 0.0, 0.1, 0.0, 0.1],
        # Wrong on purpose, and in a language nothing was trained on: the model
        # calls a lonely, sad row neutral.
        [0.0, 0.4, 0.0, 0.0, 0.0, 0.0, 0.2, 0.0, 0.7],
        # Right: the one row that must not appear in the list.
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.95],
    ]
    return error_analysis(
        examples,
        probabilities,
        labels=EMOTIONS,
        thresholds=dict.fromkeys(EMOTIONS, 0.5),
        trained_languages=("en",),
        limit=10,
    )


def test_error_analysis_notices_neutral_absorbing_a_positive() -> None:
    examples = [
        EmotionExample(
            id="a", text="I am dreading tomorrow", labels=("anxiety",), lang="en", source="s"
        ),
    ]
    probabilities = [[0.01, 0.01, 0.01, 0.01, 0.2, 0.01, 0.01, 0.01, 0.9]]
    cases = error_analysis(
        examples, probabilities, labels=EMOTIONS, thresholds=dict.fromkeys(EMOTIONS, 0.5)
    )
    assert cases
    assert cases[0].cause == "neutral_absorbed_a_positive"
    assert cases[0].truth == ("anxiety",)
    assert cases[0].predicted == ("neutral",)


def test_comparison_table_prints_cis_and_language_columns() -> None:
    table = comparison_table(
        {
            "fine-tuned (this run)": {
                "rows": 174,
                "macro_f1": 0.5,
                "macro_f1_ci": [0.42, 0.58],
                "per_language": {"en": {"rows": 58, "macro_f1": 0.6, "macro_f1_ci": [0.5, 0.7]}},
            }
        }
    )
    assert "| model | rows | macro-F1 | 95% CI | en macro-F1 |" in table
    assert "[0.420, 0.580]" in table
    assert "n=58" in table


def test_report_flags_the_language_where_a_baseline_wins() -> None:
    trained = {
        "rows": 174,
        "macro_f1": 0.4,
        "macro_f1_ci": [0.3, 0.5],
        "ece": 0.2,
        "subset_accuracy": 0.1,
        "subset_accuracy_ci": [0.0, 0.2],
        "micro_f1": 0.3,
        "micro_f1_ci": [0.2, 0.4],
        "labels": list(EMOTIONS),
        "labels_averaged_over": list(EMOTIONS),
        "labels_without_support": [],
        "thresholds": dict.fromkeys(EMOTIONS, 0.5),
        "per_label_detail": {
            label: {"tp": 1, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
            for label in EMOTIONS
        },
        "per_language": {"bn": {"rows": 58, "macro_f1": 0.2, "macro_f1_ci": [0.1, 0.3]}},
        "primary_confusion": {"joy|joy": 1},
    }
    baselines = {
        "keyword": {
            "rows": 174,
            "macro_f1": 0.6,
            "macro_f1_ci": [0.5, 0.7],
            "per_language": {"bn": {"rows": 58, "macro_f1": 0.5, "macro_f1_ci": [0.4, 0.6]}},
        }
    }
    text = render_report(
        run_id="deadbeef",
        config_name="emotion_lora_smoke",
        manifest={
            "config_sha256": "a" * 64,
            "base_model": "tiny://xlm-roberta",
            "base_note": {"kind": "tiny-random"},
            "lora": {"rank": 4, "alpha": 8, "dropout": 0.1},
            "device": "cpu",
            "mixed_precision": "off",
            "seed": 1,
        },
        trained=trained,
        baselines=baselines,
        data_summaries={
            "train": summary(
                [EmotionExample(id="a", text="t", labels=("joy",), lang="en", source="s")]
            )
        },
        comparison=comparison_table({"fine-tuned (this run)": trained, **baselines}),
        error_cases=[
            {
                "id": "a",
                "lang": "en",
                "text": "t",
                "truth": ["joy"],
                "predicted": ["neutral"],
                "primary": "neutral",
                "cause": "neutral_absorbed_a_positive",
            }
        ],
        per_label=per_label_table(trained),
        notes=["baseline `baseline` was **not run here**: no network"],
    )
    assert "This model is worse than a baseline on at least one language" in text
    assert "keyword scores 0.5000 on `bn`" in text
    assert "Domain shift from social-media text" in text
    assert "run `deadbeef`" in text


def test_table_helpers_render_markdown() -> None:
    payload = _payload()
    cases = [case.as_dict() for case in _error_cases()]
    assert per_label_table(payload).startswith("| label | support |")
    assert error_table(cases).startswith("| # | id | lang |")
    summary_text = cause_summary(cases)
    assert "`" in summary_text and summary_text.startswith("- `")


def test_write_model_card_includes_the_required_sections(
    tmp_path: Path, tiny_run_stub: dict[str, Any]
) -> None:
    path = write_model_card(
        tmp_path / "MODEL_CARD.md",
        manifest=tiny_run_stub["manifest"],
        validation=tiny_run_stub["payload"],
        test=tiny_run_stub["payload"],
        thresholds=tiny_run_stub["thresholds"],
    )
    text = path.read_text(encoding="utf-8")
    for phrase in (
        "Domain shift from social-media text",
        "Known limitations",
        "No native-speaker review",
        "Fairness across the languages",
        "Never a safety decision",
        "Reproduction",
    ):
        assert phrase in text, phrase


@pytest.fixture
def tiny_run_stub() -> dict[str, Any]:
    """A minimal finished-run shape for the card writer (no training involved)."""

    class _Threshold:
        split = "validation"
        metric = "f1"
        grid = (0.05, 0.5, 0.95)

        class _Item:
            def __init__(self, label: str):
                self.label = label
                self.threshold = 0.5
                self.support = 10
                self.tuned = True
                self.reason = "f1 on validation"

        def __init__(self) -> None:
            self.per_label = {label: self._Item(label) for label in EMOTIONS}
            self.thresholds = dict.fromkeys(EMOTIONS, 0.5)

    def payload() -> dict[str, Any]:
        return {
            "split": "test",
            "rows": 174,
            "labels": list(EMOTIONS),
            "macro_f1": 0.42,
            "macro_f1_ci": [0.35, 0.49],
            "micro_f1": 0.5,
            "micro_f1_ci": [0.4, 0.6],
            "subset_accuracy": 0.2,
            "subset_accuracy_ci": [0.1, 0.3],
            "ece": 0.18,
            "per_language": {
                language: {"rows": 58, "macro_f1": 0.4, "macro_f1_ci": [0.3, 0.5]}
                for language in ("en", "hi", "bn")
            },
        }

    return {
        "manifest": {
            "config": "emotion_lora_smoke",
            "config_sha256": "b" * 64,
            "base_model": "tiny://xlm-roberta",
            "lora": {"rank": 4, "alpha": 8, "dropout": 0.1},
            "data": {
                "train": {"rows": 60, "languages": {"en": 20, "hi": 20, "bn": 20}},
                "validation": {"rows": 24},
                "test": {"rows": 174},
            },
            "labels": list(EMOTIONS),
            "unreachable_labels": ["loneliness"],
            "seed": 1,
            "device": "cpu",
            "mixed_precision": "off",
        },
        "payload": payload(),
        "thresholds": _Threshold(),
    }


def test_the_ml_package_does_not_import_the_backend() -> None:
    """`ml/` stays installable on its own, even though the baseline harness reaches
    for the backend *inside a function* (the same trick `evals/*.py` uses).

    A fresh interpreter imports every emotion module and then reports whether
    anything under ``app`` came along for the ride.
    """

    code = (
        "import sys;"
        "import manovia_ml.training.emotion.taxonomy, manovia_ml.training.emotion.data,"
        "manovia_ml.training.emotion.model, manovia_ml.training.emotion.train,"
        "manovia_ml.training.emotion.evaluate, manovia_ml.training.emotion.baselines,"
        "manovia_ml.training.emotion.report, manovia_ml.training.emotion.tune,"
        "manovia_ml.training.emotion.thresholds;"
        "print([name for name in sys.modules if name == 'app' or name.startswith('app.')])"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=300
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]", completed.stdout
