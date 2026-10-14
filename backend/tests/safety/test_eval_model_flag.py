"""The `--model` flag on the safety eval harness, and the abstention table it feeds.

Message-2 asks for the trained model and the baseline side by side. The flag is
the mechanism, so these tests are about the flag being *honest* rather than
convenient:

* `--model trained` must reach the production selector. A flag that is accepted
  and then ignored — because the selector reads its choice from settings — would
  produce a report labelled "trained" whose every number came from the baseline.
* when the trained artefact is absent, the fallback must be visible in the
  classifier's version and in the report body, not just in a console line.
* the abstention table must refuse to invent numbers for a classifier that has no
  prediction set. A missing measurement is not a measurement of zero, and an
  abstention rate of 0.000 for a model that cannot abstain is a lie a reader
  cannot detect.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
EVALS_DIR = REPO_ROOT / "evals"
if str(EVALS_DIR) not in sys.path:
    sys.path.insert(0, str(EVALS_DIR))

import run_safety_eval  # type: ignore[import-not-found]  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.services.safety.base import RiskLevel  # noqa: E402
from app.services.safety.ml_classifier import MLPrediction  # noqa: E402


def _row(
    true: str, final: str, *, abstained: str | None, prediction_set: list[str]
) -> dict[str, Any]:
    """One result row in the shape ``assess_all`` writes."""

    row: dict[str, Any] = {
        "id": "x",
        "lang": "en",
        "true": true,
        "rules": "NONE",
        "final": final,
        "source": "ml",
        "notes": "",
        "category": "none",
        "ml_version": "day33-test",
        "ml_confidence": 0.8,
        "ml_prediction_set": prediction_set,
    }
    row["ml_abstained_level"] = abstained
    return row


# --------------------------------------------------------------------------- #
# The flag reaches the selector                                                #
# --------------------------------------------------------------------------- #


def test_the_trained_flag_reaches_the_production_selector() -> None:
    """A `--model trained` run must not be able to score the baseline silently."""

    settings = Settings(_env_file=None)
    classifier = run_safety_eval.build_classifier("trained", settings)

    # The committed checkout has no packaged Day-33 model, so the honest outcome
    # is the fallback — and the fallback says so in its version.
    assert classifier is not None
    assert classifier.version.endswith("+trained-unavailable"), classifier.version


def test_the_baseline_flag_keeps_the_historical_path() -> None:
    settings = Settings(_env_file=None)

    classifier = run_safety_eval.build_classifier("baseline", settings)

    assert classifier is not None
    assert not classifier.version.endswith("+trained-unavailable")


def test_an_unknown_model_choice_is_not_silently_the_baseline() -> None:
    settings = Settings(_env_file=None)

    with pytest.raises(ValueError):
        run_safety_eval.build_classifier("imaginary", settings)


def test_every_result_row_records_which_model_produced_it() -> None:
    """A report has to be able to say which model it measured."""

    cases = run_safety_eval.load_split("dev")[:5]
    results = run_safety_eval.assess_all(cases, ml_enabled=True, model="trained")

    assert len(results) == len(cases)
    for row in results:
        assert row["ml_version"], row
        assert "ml_prediction_set" in row
        assert "ml_abstained_level" in row


# --------------------------------------------------------------------------- #
# The abstention table                                                         #
# --------------------------------------------------------------------------- #


def test_a_baseline_run_has_no_abstention_numbers_to_report() -> None:
    """No prediction set means no abstention rate — not a zero one."""

    stats = run_safety_eval.abstention_metrics(
        [_row("HIGH", "HIGH", abstained=None, prediction_set=[])]
    )

    assert stats["available"] is False
    assert stats["abstention_rate"] is None
    assert stats["policy_recall"] is None
    assert stats["raised"] == 0


def test_a_trained_run_reports_the_policy_separately_from_the_point() -> None:
    rows = [
        # The point prediction says NONE, the set could not rule out MEDIUM, and
        # the policy therefore acts at MEDIUM.
        _row("MEDIUM", "NONE", abstained="MEDIUM", prediction_set=["none", "medium"]),
        # Confident and correct: a singleton set, no abstention.
        _row("HIGH", "HIGH", abstained="HIGH", prediction_set=["high"]),
        # The point prediction is right and the policy leaves it alone.
        _row("NONE", "NONE", abstained="NONE", prediction_set=["none", "low"]),
    ]

    stats = run_safety_eval.abstention_metrics(rows)

    assert stats["available"] is True
    assert stats["cases"] == 3
    assert stats["abstention_rate"] == pytest.approx(2 / 3)
    assert stats["raised"] == 1
    # The policy's recall is 2/3 (HIGH and MEDIUM, missing NONE as a crisis case
    # would be wrong — NONE is not a crisis level, so recall counts the two
    # HIGH+IMMINENT-adjacent truths it got right).
    assert stats["policy_recall"] is not None
    assert stats["policy_recall_ci"][0] is not None


def test_a_point_level_the_model_never_raised_is_reported_as_unchanged() -> None:
    rows = [_row("HIGH", "HIGH", abstained="HIGH", prediction_set=["high"])]

    stats = run_safety_eval.abstention_metrics(rows)

    assert stats["raised"] == 0
    assert stats["abstention_rate"] == 0.0


def test_the_abstention_section_refuses_to_invent_numbers_for_the_baseline() -> None:
    lines = run_safety_eval.abstention_section(
        [_row("HIGH", "HIGH", abstained=None, prediction_set=[])]
    )

    text = "\n".join(lines)
    assert "Not applicable" in text
    assert "0.000" not in text, "a model that cannot abstain must not show a 0.000 rate"


def test_the_abstention_section_states_what_the_numbers_do_not_show() -> None:
    lines = run_safety_eval.abstention_section(
        [_row("MEDIUM", "NONE", abstained="MEDIUM", prediction_set=["none", "medium"])]
    )

    text = "\n".join(lines)
    assert "abstention rate" in text
    assert "do **not** show" in text
    assert "MEDIUM floor" in text


# --------------------------------------------------------------------------- #
# The report says which model it measured                                      #
# --------------------------------------------------------------------------- #


def test_the_report_names_the_model_and_warns_when_it_fell_back() -> None:
    cases = run_safety_eval.load_split("dev")[:8]
    results = run_safety_eval.assess_all(cases, ml_enabled=True, model="trained")

    report = run_safety_eval.build_report("dev", cases, results, ml_enabled=True, model="trained")

    assert "`--model trained`" in report
    assert "was not loaded" in report
    assert "must not be quoted as one" in report


def test_a_baseline_report_carries_no_fallback_warning() -> None:
    cases = run_safety_eval.load_split("dev")[:8]
    results = run_safety_eval.assess_all(cases, ml_enabled=True, model="baseline")

    report = run_safety_eval.build_report("dev", cases, results, ml_enabled=True, model="baseline")

    assert "`--model baseline`" in report
    assert "was not loaded" not in report
    assert "Day-9 TF-IDF" in report


def test_the_json_summary_carries_the_model_and_the_abstention_block() -> None:
    cases = run_safety_eval.load_split("dev")[:8]
    results = run_safety_eval.assess_all(cases, ml_enabled=True, model="trained")
    thresholds, boot = run_safety_eval.load_thresholds()
    gate = run_safety_eval.evaluate_gate(results, thresholds, [])

    summary = run_safety_eval.build_json_summary(
        "dev",
        cases,
        results,
        ml_enabled=True,
        thresholds=thresholds,
        boot=boot,
        previous_path=None,
        diff_rows=[],
        gate_verdict=gate,
        model="trained",
    )

    assert summary["model"] == "trained"
    abstention = summary["abstention"]
    assert isinstance(abstention, dict)
    assert abstention["available"] is False, "the baseline fallback has no prediction sets"


def test_the_ml_prediction_type_still_carries_the_abstention_fields() -> None:
    """The bridge the evaluator reads: point level, abstained level, set."""

    prediction = MLPrediction(
        probabilities={"NONE": 0.4, "MEDIUM": 0.35},
        level=RiskLevel.NONE,
        confidence=0.4,
        version="day33-test",
        abstained_level=RiskLevel.MEDIUM,
        # The set carries level *names* (what the conformal quantile compares
        # against); only ``abstained_level`` is a RiskLevel.
        prediction_set=("NONE", "MEDIUM"),
    )

    assert prediction.abstained_level is RiskLevel.MEDIUM
    assert list(prediction.prediction_set) == ["NONE", "MEDIUM"]
