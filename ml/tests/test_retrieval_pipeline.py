"""The retrieval pipeline stages: tuning file, abstain calibration, report.

These are the modules that turn measurements into the two artefacts a deployment
actually consumes — ``tuning.json`` and ``reports/day35-retrieval.md`` — so the
properties worth pinning are the ones that would silently produce a *plausible*
artefact and a wrong system:

* **``tuning.json`` cannot smuggle a knob the backend does not have.** Everything
  inside ``dev`` that is not a ``HybridTuning`` field starts with ``_``, because
  the backend's loader ignores those by design and warns about anything else.
* **The two stages merge.** The fusion sweep runs before the reranker exists and
  the calibration after; the second must not delete the first.
* **The backend artefact path is inside the checkout.** It was not, once: a
  ``resolve("..").parent`` wrote ``backend/app/ml_artifacts`` next to the
  repository, which looks like success and ships nothing.
* **A failure cause is computed from evidence, not chosen by hand.** Ten failure
  cases in a report are only worth reading if "cross-lingual" means *no labelled
  document is in the query's language* and "missed-relevant" means *it is there
  and shares words and was still not retrieved*.
* **A row with no calibrated threshold cannot be recommended**, however good its
  MRR is — and when a non-recommendable row measured better, the recommendation
  says so instead of letting the table imply the winner was the best number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from manovia_ml.evaluation.retrieval import (
    ABSTAIN_SETTINGS,
    EvaluationSummary,
    FailureCase,
    SettingResult,
    classify_failure,
    collect_failures,
    pick_recommendation,
    render_report,
    write_report,
)
from manovia_ml.paths import ml_root
from manovia_ml.retrieval.corpus import Passage
from manovia_ml.retrieval.data import RetrievalQuery
from manovia_ml.retrieval.harness import read_tuning
from manovia_ml.retrieval.metrics import QueryResult
from manovia_ml.retrieval.tuning import (
    BACKEND_RELPATH,
    THRESHOLD_OBJECTIVES,
    CalibrationSummary,
    FusionSummary,
    backend_artifact_dir,
    install_backend,
    write_tuning,
)

try:  # the ml package installs without the backend (CI's ml job does exactly that)
    from app.services.rag.rerank import FEATURE_NAMES

    BACKEND = True
except ImportError:  # pragma: no cover - depends on the machine
    BACKEND = False

backend_only = pytest.mark.skipif(
    not BACKEND, reason="the backend is not installed here (pip install -e ../backend)"
)


def passage(doc_id: str, text: str, *, language: str = "en", tags: tuple[str, ...] = ()) -> Passage:
    return Passage(
        chunk_id=f"{doc_id}#00",
        doc_id=doc_id,
        title=doc_id.replace("-", " "),
        text=text,
        embed_text=f"{doc_id.replace('-', ' ')} {text}",
        language=language,
        tags=tags,
        chunk_index=0,
        chunk_total=1,
    )


def result(
    query_id: str,
    relevant: tuple[str, ...],
    retrieved: tuple[str, ...],
    *,
    lang: str = "en",
    style: str = "direct",
    top_score: float | None = 0.5,
) -> QueryResult:
    return QueryResult(
        query_id=query_id,
        lang=lang,
        style=style,
        relevant=relevant,
        retrieved=retrieved,
        top_score=top_score,
        latency_ms=1.0,
    )


def query_row(query_id: str, text: str, relevant: tuple[str, ...], *, lang: str = "en") -> Any:
    payload: dict[str, Any] = {
        "id": query_id,
        "query": text,
        "lang": lang,
        "style": "direct" if relevant else "no-answer",
        "relevant_docs": list(relevant),
    }
    if not relevant:
        payload["has_answer"] = False
        payload["no_answer_reason"] = "out-of-scope"
    return RetrievalQuery.model_validate(payload)


@dataclass(frozen=True)
class StubAbstain:
    """The attributes the recommendation and the report read, at chosen values.

    ``score_abstain`` derives its precision from the rows it is given, which is
    the right behaviour for the metric and useless for a test that needs "a row
    whose answer precision is exactly 0.62".
    """

    answer_precision: float
    abstain_precision: float = 0.4
    abstain_recall: float = 1.0
    abstain_f1: float = 0.57
    answer_recall: float = 0.6
    threshold: float = 0.5
    score_field: str = "top_score"
    n: int = 10
    n_no_answer: int = 2
    n_answerable: int = 8
    confusion: dict[str, int] = field(
        default_factory=lambda: {
            "true_abstain": 10,
            "false_abstain": 14,
            "true_answer": 26,
            "false_answer": 2,
        }
    )
    abstain_recall_by_lang: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "answer_precision": self.answer_precision,
            "abstain_precision": self.abstain_precision,
            "abstain_recall": self.abstain_recall,
            "abstain_f1": self.abstain_f1,
            "answer_recall": self.answer_recall,
            "confusion": dict(self.confusion),
        }


def setting(
    name: str,
    *,
    mrr: float,
    recall: dict[int, float],
    latency: float = 3.0,
    answer_precision: float | None = None,
    status: str = "measured",
    reason: str = "",
) -> SettingResult:
    from manovia_ml.retrieval.metrics import RankingReport

    abstain = None
    threshold = None
    if answer_precision is not None:
        abstain = StubAbstain(answer_precision=answer_precision)
        threshold = 0.5
    return SettingResult(
        name=name,
        label=name,
        status=status,
        reason=reason,
        report=RankingReport(
            setting=name,
            n=10,
            n_answerable=8,
            n_no_answer=2,
            n_errors=0,
            recall=recall,
            hit={3: recall.get(3, 0.0)},
            mrr=mrr,
            latency_ms={"p50": latency, "p95": latency + 1.0},
        )
        if status == "measured"
        else None,
        abstain=abstain,
        threshold=threshold,
    )


# ------------------------------------------------------------------ tuning.json #


def fusion_summary(weight: float = 0.2) -> FusionSummary:
    return FusionSummary(
        split="dev",
        metric="mrr",
        best_weight=weight,
        best_score=0.487,
        n_queries=85,
        grid=((0.0, 0.44), (weight, 0.487), (1.0, 0.48)),
        table=({"dense_weight": weight, "mrr": 0.487},),
        embedder_kind="tfidf",
        fingerprints={"dev": "b9dc4cbb", "test": "091a469c"},
        seconds=1.0,
    )


def calibration_summary(threshold: float = 0.72) -> CalibrationSummary:
    return CalibrationSummary(
        split="calibration",
        objective="answer_precision",
        min_answer_precision=0.7,
        top_k_for_answer=3,
        thresholds={"hybrid": 0.66, "hybrid_rerank": threshold},
        reports={"hybrid_rerank": {"threshold": threshold, "answer_precision": 0.7}},
        alternatives={"hybrid_rerank:f1": {"threshold": 0.6, "answer_precision": 0.64}},
        fingerprints={"calibration": "1ce6ecf9", "test": "091a469c"},
        seconds=1.0,
    )


class TestTuningFile:
    def test_the_fusion_stage_writes_the_dev_block(self, tmp_path: Path) -> None:
        path = tmp_path / "tuning.json"
        payload = write_tuning(path, fusion=fusion_summary())
        assert payload["dev"]["dense_weight"] == 0.2
        assert payload["dev"]["_grid"] == [[0.0, 0.44], [0.2, 0.487], [1.0, 0.48]]
        assert payload["embedder_kind"] == "tfidf"
        assert payload["splits"]["dev"] == "b9dc4cbb"
        assert json.loads(path.read_text(encoding="utf-8")) == payload

    def test_the_calibration_stage_keeps_what_the_sweep_wrote(self, tmp_path: Path) -> None:
        # The sweep runs before the reranker exists, the calibration after. If the
        # second stage replaced the file, the deployment would boot with a default
        # fusion weight and a calibrated threshold — two numbers from two
        # different systems.
        path = tmp_path / "tuning.json"
        write_tuning(path, fusion=fusion_summary())
        payload = write_tuning(path, calibration=calibration_summary())
        assert payload["dev"]["dense_weight"] == 0.2
        assert payload["abstain"]["hybrid_rerank"] == 0.72
        assert payload["abstain"]["hybrid"] == 0.66
        assert payload["abstain"]["_alternatives"]["hybrid_rerank:f1"]["threshold"] == 0.6
        assert set(payload["splits"]) == {"dev", "test", "calibration"}

    def test_every_provenance_key_is_one_the_backend_ignores(self, tmp_path: Path) -> None:
        # `load_tuning` keeps only keys that are HybridTuning fields and skips
        # anything starting with `_`. A provenance key without the underscore
        # would be an unknown knob in the served retriever, warned about at every
        # boot — or worse, coerced into one.
        payload = write_tuning(
            tmp_path / "tuning.json", fusion=fusion_summary(), calibration=calibration_summary()
        )
        if BACKEND:
            from app.services.rag.hybrid import HybridTuning

            fields = set(HybridTuning.__dataclass_fields__)
            extra = [key for key in payload["dev"] if key not in fields]
            assert extra, "the file should carry its provenance beside the knobs"
            assert all(key.startswith("_") for key in extra), extra
        else:  # pragma: no cover - only on a machine without the backend
            assert all(key.startswith("_") or key in {"dense_weight"} for key in payload["dev"])

    def test_the_backend_destination_is_inside_the_repository(self) -> None:
        # Regression: `resolve("..").parent` is the *parent* of the checkout, so
        # `--install-backend` wrote backend/app/ml_artifacts/retrieval_v1 next to
        # the repository. It printed a path, so it looked like it worked.
        destination = backend_artifact_dir()
        assert destination == ml_root().parent / BACKEND_RELPATH
        assert destination.is_relative_to(ml_root().parent)
        assert destination.parts[-3:] == ("app", "ml_artifacts", "retrieval_v1")

    def test_install_writes_a_file_the_backend_can_read(self, tmp_path: Path) -> None:
        payload = write_tuning(
            tmp_path / "source" / "tuning.json",
            fusion=fusion_summary(),
            calibration=calibration_summary(),
        )
        installed = install_backend(payload, repo_root=tmp_path)
        assert installed == tmp_path / BACKEND_RELPATH / "tuning.json"
        assert installed.is_file()
        assert read_tuning(installed)["dev"]["dense_weight"] == 0.2
        assert read_tuning(installed)["abstain"]["hybrid_rerank"] == 0.72

    def test_the_objectives_a_threshold_can_be_fitted_under_are_the_three_measured(
        self,
    ) -> None:
        assert set(THRESHOLD_OBJECTIVES) == {"answer_precision", "f1", "abstain_recall"}
        assert set(ABSTAIN_SETTINGS) == {"hybrid", "hybrid_rerank"}


# ------------------------------------------------------------- failure causes #


class TestFailureCauses:
    def passages(self) -> tuple[Passage, ...]:
        return (
            passage("sleep-rhythm", "keep a steady sleep rhythm and dim the evenings"),
            passage("sleep-rhythm-hi", "नींद का ढाँचा बनाएँ", language="hi"),
            passage("worry-window", "give the worries one slot of the day"),
        )

    def test_a_relevant_document_that_was_retrieved_second_is_a_ranking_failure(self) -> None:
        row = result("rt-1", ("sleep-rhythm",), ("worry-window", "sleep-rhythm"))
        cause, evidence = classify_failure(
            row, query="steady sleep rhythm", passages=self.passages(), threshold=None
        )
        assert cause == "ranking"
        assert evidence["first_relevant_rank"] == 2

    def test_the_first_relevant_document_is_not_a_failure(self) -> None:
        row = result("rt-1", ("sleep-rhythm",), ("sleep-rhythm", "worry-window"))
        cause, _ = classify_failure(
            row, query="steady sleep rhythm", passages=self.passages(), threshold=None
        )
        assert cause == "not-a-failure"

    def test_a_missed_document_that_shares_words_is_a_recall_failure_not_a_gap(self) -> None:
        # "corpus-gap" would tell a reader the document does not exist. It does;
        # the first stage just did not retrieve it. The distinction matters
        # because the fixes are different (a wider pool versus a new document).
        row = result("rt-1", ("sleep-rhythm",), ("worry-window",))
        cause, evidence = classify_failure(
            row, query="steady sleep rhythm evenings", passages=self.passages(), threshold=None
        )
        assert cause == "missed-relevant"
        assert evidence["shared_content_tokens"]["sleep-rhythm"] > 0
        assert evidence["answer_available_in_query_language"] is True

    def test_no_shared_words_in_the_same_language_is_a_vocabulary_failure(self) -> None:
        row = result("rt-1", ("sleep-rhythm",), ("worry-window",))
        cause, evidence = classify_failure(
            row, query="zzz qqq xxx", passages=self.passages(), threshold=None
        )
        assert cause == "vocabulary"
        assert evidence["answer_available_in_query_language"] is True

    def test_no_shared_words_and_no_document_in_that_language_is_cross_lingual(self) -> None:
        row = result("rt-1", ("sleep-rhythm",), ("worry-window",), lang="bn")
        cause, evidence = classify_failure(
            row, query="ঘুমের সময়সূচি", passages=self.passages(), threshold=None
        )
        assert cause == "cross-lingual"
        assert evidence["answer_available_in_query_language"] is False

    def test_a_same_language_answer_missed_in_a_multilingual_label_set_is_not_cross_lingual(
        self,
    ) -> None:
        # One of the two labelled documents *is* Hindi, so the answer was
        # available in the query's language and the failure is recall.
        row = result("rt-1", ("sleep-rhythm", "sleep-rhythm-hi"), ("worry-window",), lang="hi")
        cause, evidence = classify_failure(
            row, query="zzz qqq xxx", passages=self.passages(), threshold=None
        )
        assert cause == "vocabulary"
        assert evidence["answer_available_in_query_language"] is True

    def test_an_answered_no_answer_query_is_an_abstain_miss_with_its_margin(self) -> None:
        row = result("rt-na", (), ("worry-window",), style="no-answer", top_score=0.74)
        cause, evidence = classify_failure(
            row, query="is crypto a good idea", passages=self.passages(), threshold=0.72
        )
        assert cause == "abstain-miss"
        assert evidence["margin_over_threshold"] == pytest.approx(0.02)

    def test_a_refused_no_answer_query_is_a_correct_refusal(self) -> None:
        row = result("rt-na", (), ("worry-window",), style="no-answer", top_score=0.30)
        cause, _ = classify_failure(
            row, query="is crypto a good idea", passages=self.passages(), threshold=0.72
        )
        assert cause == "correct-refusal"

    def test_nothing_retrieved_at_all_says_so(self) -> None:
        row = result("rt-1", ("sleep-rhythm",), ())
        cause, _ = classify_failure(
            row, query="steady sleep rhythm", passages=self.passages(), threshold=None
        )
        assert cause == "no-candidates"

    def test_collect_failures_drops_the_successes_and_orders_by_severity(self) -> None:
        rows = [
            query_row("rt-1", "steady sleep rhythm", ("sleep-rhythm",)),
            query_row("rt-2", "zzz qqq", ("sleep-rhythm",)),
            query_row("rt-na", "crypto", ()),
        ]
        results = [
            result("rt-1", ("sleep-rhythm",), ("sleep-rhythm",)),
            result("rt-2", ("sleep-rhythm",), ("worry-window",)),
            result("rt-na", (), ("worry-window",), style="no-answer", top_score=0.9),
        ]
        row = setting(
            "hybrid_rerank", mrr=0.5, recall={1: 0.5, 3: 0.5, 5: 0.5}, answer_precision=0.7
        )
        measured = SettingResult(
            name=row.name,
            label=row.label,
            status="measured",
            report=row.report,
            abstain=row.abstain,
            threshold=0.72,
            results=tuple(results),
        )
        cases = collect_failures(
            measured,
            passages=(
                passage("sleep-rhythm", "steady sleep rhythm"),
                passage("worry-window", "worries"),
            ),
            rows=rows,
            limit=10,
        )
        assert [case.query_id for case in cases] == ["rt-na", "rt-2"]
        assert [case.cause for case in cases] == ["abstain-miss", "vocabulary"]
        assert cases[0].query == "crypto"
        assert isinstance(cases[0], FailureCase)


# ---------------------------------------------------------------- recommendation #


class TestRecommendation:
    def test_a_row_without_a_calibrated_threshold_cannot_be_recommended(self) -> None:
        rows = [
            setting("hybrid_finetuned_rerank", mrr=0.90, recall={1: 0.9, 3: 0.9, 5: 0.9}),
            setting(
                "hybrid_rerank", mrr=0.60, recall={1: 0.6, 3: 0.6, 5: 0.6}, answer_precision=0.8
            ),
        ]
        name, reason = pick_recommendation(rows, min_answer_precision=0.7)
        assert name == "hybrid_rerank"
        assert "hybrid_finetuned_rerank" in reason
        assert "not recommendable" in reason

    def test_the_recommendation_quotes_the_measured_numbers(self) -> None:
        rows = [
            setting("baseline", mrr=0.50, recall={1: 0.3, 3: 0.4, 5: 0.5}, latency=2.0),
            setting(
                "hybrid_rerank",
                mrr=0.66,
                recall={1: 0.35, 3: 0.47, 5: 0.53},
                latency=9.0,
                answer_precision=0.8,
            ),
        ]
        name, reason = pick_recommendation(rows, min_answer_precision=0.7)
        assert name == "hybrid_rerank"
        assert "MRR 0.660" in reason
        assert "p95 10.0 ms" in reason
        assert "Day-13 baseline" in reason

    def test_a_floor_nothing_clears_is_reported_as_such(self) -> None:
        rows = [
            setting(
                "hybrid_rerank", mrr=0.66, recall={1: 0.35, 3: 0.47, 5: 0.53}, answer_precision=0.8
            ),
        ]
        name, reason = pick_recommendation(rows, min_answer_precision=0.99)
        assert name == "hybrid_rerank"
        assert "no setting reached min_answer_precision=0.99" in reason

    def test_an_unmeasurable_row_is_never_the_winner(self) -> None:
        rows = [
            setting(
                "dense_finetuned",
                mrr=0.0,
                recall={},
                status="unavailable",
                reason="no fine-tuned encoder artefact",
            ),
            setting("hybrid", mrr=0.5, recall={1: 0.4, 3: 0.5, 5: 0.5}, answer_precision=0.8),
        ]
        name, reason = pick_recommendation(rows, min_answer_precision=0.7)
        assert name == "hybrid"
        assert "unavailable" not in reason

    def test_with_nothing_measured_it_says_so_rather_than_picking(self) -> None:
        rows = [setting("hybrid", mrr=0.0, recall={}, status="unavailable", reason="no backend")]
        assert pick_recommendation(rows, min_answer_precision=0.7) == (
            "",
            "nothing could be measured in this environment",
        )


# ---------------------------------------------------------------------- report #


class TestReport:
    def summary(self, *, smoke: bool = False) -> EvaluationSummary:
        rows = [
            setting("baseline", mrr=0.64, recall={1: 0.29, 3: 0.50, 5: 0.59}, latency=3.1),
            setting(
                "hybrid_rerank",
                mrr=0.66,
                recall={1: 0.35, 3: 0.47, 5: 0.53},
                latency=9.0,
                answer_precision=0.8,
            ),
            setting(
                "dense_finetuned",
                mrr=0.0,
                recall={},
                status="unavailable",
                reason="no fine-tuned encoder at data/retrieval_artifacts/x",
            ),
        ]
        return EvaluationSummary(
            split="test",
            n_rows=52,
            ks=(1, 3, 5),
            settings=tuple(rows),
            recommended="hybrid_rerank",
            recommendation="measured best",
            failures=(
                FailureCase(
                    query_id="rt-na-015",
                    query="is that normal",
                    lang="en",
                    style="no-answer",
                    relevant=(),
                    retrieved=("worry-window",),
                    top_score=0.74,
                    threshold=0.72,
                    cause="abstain-miss",
                    evidence={"margin_over_threshold": 0.02},
                ),
            ),
            splits={
                "train": "c186b664" * 8,
                "dev": "b9dc4cbb" * 8,
                "calibration": "1ce6ecf9" * 8,
                "test": "091a469c" * 8,
                "counts": {"train": 97, "dev": 85, "calibration": 20, "test": 52},
            },
            tuning={
                "dev": {"dense_weight": 0.2, "_grid": [[0.0, 0.44], [0.2, 0.49]]},
                "abstain": {"hybrid": 0.66, "hybrid_rerank": 0.72, "_split": "calibration"},
            },
            corpus={
                "documents": 40,
                "passages": 59,
                "fingerprint": "0c015523",
                "latency_repeats": 3,
            },
            smoke=smoke,
        )

    def test_the_report_names_the_split_and_the_recommendation(self) -> None:
        text = render_report(self.summary(), config_name="retrieval_eval")
        assert "**test** split (52 queries" in text
        assert "RETRIEVER=hybrid_rerank" in text
        assert "`baseline`" in text and "`hybrid_rerank`" in text

    def test_a_row_that_could_not_be_measured_appears_with_its_reason(self) -> None:
        text = render_report(self.summary(), config_name="retrieval_eval")
        assert "dense_finetuned" in text
        assert "no fine-tuned encoder at data/retrieval_artifacts/x" in text

    def test_the_split_fingerprints_are_printed_in_full(self) -> None:
        # This is the "test was never tuned on" evidence; a truncated hash is not
        # evidence, so the four fingerprints appear complete in their own table.
        text = render_report(self.summary(), config_name="retrieval_eval")
        for digest in ("c186b664" * 8, "b9dc4cbb" * 8, "1ce6ecf9" * 8, "091a469c" * 8):
            assert digest in text
        assert "| train | 97 |" in text
        assert "| test | 52 |" in text

    def test_the_failure_case_carries_its_evidence_into_the_report(self) -> None:
        text = render_report(self.summary(), config_name="retrieval_eval")
        assert "rt-na-015" in text
        assert "abstain-miss" in text
        assert "margin_over_threshold" in text

    def test_the_dev_sweep_curve_is_printed(self) -> None:
        text = render_report(self.summary(), config_name="retrieval_eval")
        assert "| 0.20 | 0.4900 |" in text

    def test_a_smoke_run_says_so_in_its_title(self) -> None:
        text = render_report(self.summary(smoke=True), config_name="retrieval_smoke")
        assert "SMOKE" in text.splitlines()[0]
        assert "SMOKE" not in render_report(self.summary(), config_name="retrieval_eval")

    def test_the_report_is_written_where_it_is_asked_for(self, tmp_path: Path) -> None:
        path = write_report(
            self.summary(), tmp_path / "nested" / "report.md", config_name="retrieval_eval"
        )
        assert path.is_file()
        assert path.read_text(encoding="utf-8").startswith("# Day 35 retrieval report")

    def test_a_comparison_needs_both_of_its_rows(self) -> None:
        # `baseline -> hybrid_rerank` is computable without `hybrid`, so the
        # section appears; the comparison that needs the missing row does not, and
        # the table shows a subtraction nobody can perform.
        without = render_report(self.summary(), config_name="retrieval_eval")
        assert "## What each stage costs and buys" in without
        assert "`hybrid` → `hybrid_rerank`" not in without
        assert "`baseline` → `hybrid_rerank`" in without
        base = self.summary()
        rebuilt = EvaluationSummary(
            split=base.split,
            n_rows=base.n_rows,
            ks=base.ks,
            settings=(
                *base.settings,
                setting("hybrid", mrr=0.62, recall={1: 0.30, 3: 0.48, 5: 0.52}, latency=3.3),
            ),
            recommended=base.recommended,
            recommendation=base.recommendation,
            splits=base.splits,
            tuning=base.tuning,
            corpus=base.corpus,
        )
        text = render_report(rebuilt, config_name="retrieval_eval")
        assert "## What each stage costs and buys" in text
        assert "`hybrid` → `hybrid_rerank`" in text
        assert "+0.040" in text  # ΔMRR: 0.66 - 0.62


# --------------------------------------------------------------------- reranker #


@backend_only
class TestRerankerFitting:
    def example(self, *, label: int, dense: float, overlap: float) -> Any:
        from manovia_ml.retrieval.reranker import RerankerExample

        features = [1.0] * len(FEATURE_NAMES)
        features[FEATURE_NAMES.index("dense_cosine")] = dense
        features[FEATURE_NAMES.index("token_overlap")] = overlap
        features[FEATURE_NAMES.index("fused_confidence")] = 0.5
        return RerankerExample(
            query_id=f"rt-{label}",
            query="steady sleep rhythm",
            lang="en",
            doc_id="sleep-rhythm" if label else "worry-window",
            title="a steady sleep rhythm",
            features=tuple(features),
            label=label,
            fused_rank=1,
            evidence=0.5,
        )

    def examples(self) -> list[Any]:
        return [
            self.example(label=1, dense=0.8, overlap=0.7),
            self.example(label=1, dense=0.7, overlap=0.6),
            self.example(label=0, dense=0.1, overlap=0.1),
            self.example(label=0, dense=0.2, overlap=0.0),
        ]

    def test_the_coefficients_are_one_per_feature_in_the_builds_order(self) -> None:
        from manovia_ml.retrieval.reranker import fit_coefficients

        coefficients = fit_coefficients(self.examples(), c=1.0, seed=7)
        assert len(coefficients) == len(FEATURE_NAMES)
        assert all(isinstance(value, float) for value in coefficients)

    def test_a_separable_set_is_scored_relevant_first(self) -> None:
        from manovia_ml.retrieval.reranker import fit_coefficients, score_examples

        coefficients = fit_coefficients(self.examples(), c=1.0, seed=7)
        metrics = score_examples(self.examples(), coefficients)
        assert metrics["relevant_first"] == 1.0
        assert metrics["mrr"] == 1.0
        assert metrics["roc_auc"] == 1.0

    def test_a_single_class_is_refused_rather_than_fitted(self) -> None:
        from manovia_ml.retrieval.reranker import fit_coefficients

        with pytest.raises(ValueError, match="same label"):
            fit_coefficients([self.example(label=0, dense=0.1, overlap=0.1)] * 3)

    def test_no_examples_is_an_error_not_an_empty_model(self) -> None:
        from manovia_ml.retrieval.reranker import fit_coefficients

        with pytest.raises(ValueError, match="no reranker examples"):
            fit_coefficients([])

    def test_the_artefact_round_trips_through_the_backend_loader(self, tmp_path: Path) -> None:
        # The whole point of the JSON layout: what ml writes is what serving
        # loads, with the feature version and the BM25 pivot checked on the way.
        from app.services.rag.rerank import load_cross_encoder

        from manovia_ml.retrieval.reranker import (
            RerankerSummary,
            fit_coefficients,
            save_artifact,
        )

        coefficients = fit_coefficients(self.examples(), c=1.0, seed=7)
        summary = RerankerSummary(
            artifact_dir=str(tmp_path),
            fitted_on="train",
            reported_on="dev",
            rows=4,
            positives=2,
            c=1.0,
        )
        save_artifact(tmp_path, coefficients, summary)
        loaded = load_cross_encoder(tmp_path)
        assert list(loaded.weights) == [round(value, 6) for value in coefficients]
        assert loaded.metadata["trained"] is True
        assert loaded.metadata["fitted_on_split"] == "train"
        payload = json.loads((tmp_path / "reranker_weights.json").read_text(encoding="utf-8"))
        assert payload["features"] == list(FEATURE_NAMES)
