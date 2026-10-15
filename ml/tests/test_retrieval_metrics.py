"""Retrieval metrics: the definitions, hand-computed, and the abstain decision.

Every number the Day-35 report quotes comes out of this module, so the tests
compute the expected values by hand from a handful of rows rather than
asserting "it ran". Two decisions are pinned here because both are easy to get
plausibly wrong:

* **The unit is the document, and recall is set-based.** The corpus is chunked,
  a query can deserve two documents, and a retriever that returns the same
  document twice has not recalled more. So ``retrieved`` holds document ids and
  ``recall@k = |relevant ∩ retrieved[:k]| / |relevant|``.
* **No-answer rows are counted, never ranked.** A query the corpus cannot
  answer has no recall, and averaging a made-up zero into Recall@5 would make
  the ranking metric move when the *abstain* behaviour changed. They are
  counted in ``n_no_answer`` and measured by :func:`score_abstain` instead.
"""

from __future__ import annotations

import math

import pytest

from manovia_ml.retrieval.metrics import (
    AbstainReport,
    QueryResult,
    evaluate_ranking,
    fit_abstain_threshold,
    percentile,
    score_abstain,
)


def result(
    query_id: str,
    relevant: tuple[str, ...],
    retrieved: tuple[str, ...],
    *,
    lang: str = "en",
    style: str = "direct",
    top_score: float | None = 0.5,
    latency_ms: float = 1.0,
    error: str | None = None,
) -> QueryResult:
    return QueryResult(
        query_id=query_id,
        lang=lang,
        style=style,
        relevant=relevant,
        retrieved=retrieved,
        top_score=top_score,
        latency_ms=latency_ms,
        error=error,
    )


class TestDefinitions:
    def test_recall_is_set_based_over_documents(self) -> None:
        report = evaluate_ranking(
            [result("rt-1", ("a", "b"), ("b", "c", "a"))], setting="test", ks=(1, 3, 5)
        )
        # "b" is relevant and sits at rank 1, so one of the two deserved
        # documents is already covered.
        assert report.recall[1] == pytest.approx(0.5)
        assert report.recall[3] == pytest.approx(1.0)
        # k beyond the returned list cannot recall more than the list holds.
        assert report.recall[5] == pytest.approx(1.0)

    def test_a_document_returned_twice_is_not_recalled_twice(self) -> None:
        report = evaluate_ranking(
            [result("rt-1", ("a", "b"), ("a", "a", "a"))], setting="test", ks=(3,)
        )
        assert report.recall[3] == pytest.approx(0.5)

    def test_hit_and_precision_at_k(self) -> None:
        report = evaluate_ranking(
            [result("rt-1", ("a", "b"), ("b", "c", "a"))], setting="test", ks=(1, 3)
        )
        assert report.hit[1] == pytest.approx(1.0)
        assert report.precision[3] == pytest.approx(2 / 3)

    def test_mrr_is_the_reciprocal_of_the_first_relevant_rank(self) -> None:
        report = evaluate_ranking(
            [
                result("rt-1", ("a",), ("x", "a")),  # 1/2
                result("rt-2", ("a",), ("a", "x")),  # 1
                result("rt-3", ("a",), ("x", "y")),  # 0
            ],
            setting="test",
        )
        assert report.mrr == pytest.approx((0.5 + 1.0 + 0.0) / 3)

    def test_ndcg_rewards_putting_the_relevant_document_first(self) -> None:
        first = evaluate_ranking([result("rt-1", ("a",), ("a", "b", "c"))], setting="test", ks=(3,))
        later = evaluate_ranking([result("rt-1", ("a",), ("b", "c", "a"))], setting="test", ks=(3,))
        assert first.ndcg[3] == pytest.approx(1.0)
        assert later.ndcg[3] == pytest.approx(1 / math.log2(4))
        assert first.ndcg[3] > later.ndcg[3]

    def test_no_answer_rows_are_counted_but_never_ranked(self) -> None:
        report = evaluate_ranking(
            [
                result("rt-1", ("a",), ("a",)),
                result("rt-2", (), (), style="no-answer"),
            ],
            setting="test",
        )
        assert report.n == 2
        assert report.n_answerable == 1
        assert report.n_no_answer == 1
        # The perfect answerable row is not diluted by the row that has none.
        assert report.recall[1] == pytest.approx(1.0)
        assert report.mrr == pytest.approx(1.0)

    def test_errors_are_reported_not_averaged_away(self) -> None:
        report = evaluate_ranking(
            [
                result("rt-1", ("a",), ("a",)),
                result("rt-2", ("a",), (), error="EncoderUnavailable: no checkpoint"),
            ],
            setting="test",
        )
        assert report.n_errors == 1

    def test_the_slices_the_brief_asks_for_are_keyed_by_language_and_style(self) -> None:
        report = evaluate_ranking(
            [
                result("rt-1", ("a",), ("a",), lang="hi", style="hinglish"),
                result("rt-2", ("a",), ("x",), lang="bn", style="romanised"),
            ],
            setting="test",
        )
        assert report.slices["lang:hi"]["n"] == 1
        assert report.slices["lang:bn"]["recall_at_1"] == pytest.approx(0.0)
        assert report.slices["style:hinglish"]["mrr"] == pytest.approx(1.0)
        assert report.slices["style:romanised"]["hit_at_3"] == pytest.approx(0.0)

    def test_latency_percentiles_are_over_queries(self) -> None:
        rows = [
            result(f"rt-{index}", ("a",), ("a",), latency_ms=float(index))
            for index in range(1, 101)
        ]
        report = evaluate_ranking(rows, setting="test")
        assert report.latency_ms["p50"] == pytest.approx(
            percentile([float(i) for i in range(1, 101)], 50)
        )
        assert report.latency_ms["p95"] > report.latency_ms["p50"]
        assert report.latency_ms["p99"] >= report.latency_ms["p95"]

    def test_a_bootstrap_interval_brackets_the_point_estimate(self) -> None:
        rows = [
            result(f"rt-{index}", ("a",), ("a",) if index % 2 else ("x",)) for index in range(40)
        ]
        plain = evaluate_ranking(rows, setting="test")
        bootstrapped = evaluate_ranking(rows, setting="test", bootstrap=200, seed=7)
        low, high = bootstrapped.ci95["mrr"]
        assert low <= plain.mrr <= high
        assert high - low > 0.0
        assert plain.ci95 == {}

    def test_the_bootstrap_is_seeded(self) -> None:
        rows = [
            result(f"rt-{index}", ("a",), ("a",) if index % 2 else ("x",)) for index in range(40)
        ]
        first = evaluate_ranking(rows, setting="test", bootstrap=100, seed=7)
        second = evaluate_ranking(rows, setting="test", bootstrap=100, seed=7)
        assert first.ci95 == second.ci95

    def test_as_dict_is_json_shaped_for_the_report_and_the_run_log(self) -> None:
        payload = evaluate_ranking([result("rt-1", ("a",), ("a",))], setting="hybrid").as_dict()
        assert payload["setting"] == "hybrid"
        assert payload["recall_at"]["1"] == 1.0
        assert "mrr" in payload and "latency_ms" in payload and "slices" in payload


class TestAbstain:
    def rows(self) -> list[QueryResult]:
        return [
            # Two answerable queries: one the system is confident about, one it
            # is not. Two no-answer queries: one correctly low, one that fused
            # high anyway (the false answer every threshold has to trade off).
            result("rt-good", ("a",), ("a",), top_score=0.9),
            result("rt-weak", ("a",), ("x",), top_score=0.2),
            result("rt-none-low", (), (), style="no-answer", top_score=0.1),
            result("rt-none-high", (), ("x",), style="no-answer", top_score=0.8),
        ]

    def test_the_confusion_counts_are_the_decision_not_the_ranking(self) -> None:
        report = score_abstain(self.rows(), threshold=0.5)
        assert report.confusion == {
            "true_abstain": 1,  # rt-none-low
            "false_abstain": 1,  # rt-weak (answerable, refused)
            "true_answer": 1,  # rt-good
            "false_answer": 1,  # rt-none-high
        }
        assert report.abstain_precision == pytest.approx(0.5)
        assert report.abstain_recall == pytest.approx(0.5)
        # Answered: rt-good (relevant, hit) and rt-none-high (nothing to hit).
        # So half the turns that showed a card showed a useful one — the number
        # a product actually feels, and the reason the floor exists.
        assert report.answer_precision == pytest.approx(0.5)
        assert report.answer_recall == pytest.approx(0.5)

    def test_a_threshold_above_every_score_refuses_everything(self) -> None:
        report = score_abstain(self.rows(), threshold=1.5)
        assert report.confusion["true_answer"] == 0
        assert report.abstain_recall == pytest.approx(1.0)
        assert report.answer_recall == pytest.approx(0.0)

    def test_a_threshold_below_every_score_answers_everything(self) -> None:
        report = score_abstain(self.rows(), threshold=0.0)
        assert report.confusion["false_answer"] == 2
        assert report.abstain_recall == pytest.approx(0.0)
        assert report.answer_recall == pytest.approx(1.0)

    def test_a_query_with_no_candidate_abstains_by_construction(self) -> None:
        report = score_abstain([result("rt-empty", ("a",), (), top_score=None)], threshold=0.0)
        assert report.confusion["false_abstain"] == 1

    def test_per_language_abstain_recall_exposes_a_narrowed_corpus(self) -> None:
        rows = [
            result("rt-en", (), (), style="no-answer", top_score=0.1, lang="en"),
            result("rt-hi", (), (), style="no-answer", top_score=0.9, lang="hi"),
        ]
        report = score_abstain(rows, threshold=0.5)
        assert report.abstain_recall_by_lang["en"] == pytest.approx(1.0)
        assert report.abstain_recall_by_lang["hi"] == pytest.approx(0.0)

    def test_as_dict_round_trips_the_report_fields(self) -> None:
        payload = score_abstain(self.rows(), threshold=0.5).as_dict()
        assert payload["threshold"] == 0.5
        assert payload["confusion"]["true_answer"] == 1


class TestThresholdFitting:
    def test_f1_picks_the_split_that_best_separates_the_two_kinds(self) -> None:
        rows = [
            result("rt-a1", ("a",), ("a",), top_score=0.9),
            result("rt-a2", ("a",), ("a",), top_score=0.7),
            result("rt-n1", (), (), style="no-answer", top_score=0.2),
            result("rt-n2", (), (), style="no-answer", top_score=0.1),
        ]
        fitted = fit_abstain_threshold(rows, objective="f1")
        assert isinstance(fitted, AbstainReport)
        assert 0.2 < fitted.threshold < 0.7
        assert fitted.confusion["true_abstain"] == 2
        assert fitted.confusion["true_answer"] == 2

    def test_answer_precision_holds_the_line_at_the_widest_coverage(self) -> None:
        rows = [
            result("rt-a1", ("a",), ("a",), top_score=0.9),
            result("rt-a2", ("a",), ("a",), top_score=0.6),
            result("rt-n1", (), ("x",), style="no-answer", top_score=0.3),
        ]
        strict = fit_abstain_threshold(rows, objective="answer_precision", min_answer_precision=1.0)
        # Every card it shows is relevant, and it still answers both answerable
        # queries: the constraint is on precision, the objective is coverage.
        assert strict.answer_precision == pytest.approx(1.0)
        assert strict.answer_recall == pytest.approx(1.0)
        assert strict.threshold == pytest.approx(0.45)
        # And it is the line, not a lucky one: one grid step lower answers the
        # out-of-scope query too and precision drops.
        assert score_abstain(rows, threshold=0.15).answer_precision == pytest.approx(2 / 3)

    def test_the_f1_objective_really_does_maximise_f1_over_the_grid(self) -> None:
        rows = [
            result("rt-a1", ("a",), ("a",), top_score=0.9),
            result("rt-a2", ("a",), ("x",), top_score=0.5),
            result("rt-n1", (), ("y",), style="no-answer", top_score=0.7),
            result("rt-n2", (), (), style="no-answer", top_score=0.1),
        ]
        fitted = fit_abstain_threshold(rows, objective="f1")
        grid = [0.0, 0.05, 0.3, 0.4, 0.6, 0.8, 0.9 + 1e-6]
        assert all(
            fitted.abstain_f1 + 1e-9 >= score_abstain(rows, threshold=t).abstain_f1 for t in grid
        )

    def test_the_abstain_recall_objective_refuses_every_no_answer_query(self) -> None:
        rows = [
            result("rt-a1", ("a",), ("a",), top_score=0.9),
            result("rt-n1", (), ("y",), style="no-answer", top_score=0.7),
            result("rt-n2", (), (), style="no-answer", top_score=0.1),
        ]
        recalled = fit_abstain_threshold(rows, objective="abstain_recall")
        assert recalled.abstain_recall == pytest.approx(1.0)
        assert recalled.threshold > 0.7
        # Here the answerable row scores above both refusals, so full abstain
        # recall costs nothing — which is exactly why this objective cannot be
        # the default. The next test is the case where it does cost.
        assert recalled.answer_recall == pytest.approx(1.0)

    def test_overlapping_scores_leave_no_threshold_that_separates_them(self) -> None:
        # The answerable query scores *below* the out-of-scope one, so every
        # threshold either answers both or refuses both. Both objectives land on
        # "refuse everything", and the confusion counts say why: the answerable
        # row is a false abstain. This is the shape of a corpus gap, and no
        # threshold search can fix it — which is why the report prints the
        # confusion matrix next to the threshold instead of the threshold alone.
        rows = [
            result("rt-a1", ("a",), ("a",), top_score=0.6),
            result("rt-n1", (), ("y",), style="no-answer", top_score=0.7),
        ]
        recalled = fit_abstain_threshold(rows, objective="abstain_recall")
        balanced = fit_abstain_threshold(rows, objective="f1")
        assert recalled.threshold == balanced.threshold == pytest.approx(0.700001)
        assert recalled.abstain_recall == pytest.approx(1.0)
        assert recalled.answer_recall == pytest.approx(0.0)
        assert balanced.confusion["false_abstain"] == 1

    def test_the_abstain_recall_objective_maximises_recall_over_the_grid(self) -> None:
        rows = [
            result("rt-a1", ("a",), ("a",), top_score=0.9),
            result("rt-a2", ("a",), ("a",), top_score=0.5),
            result("rt-n1", (), ("y",), style="no-answer", top_score=0.7),
            result("rt-n2", (), (), style="no-answer", top_score=0.2),
        ]
        fitted = fit_abstain_threshold(rows, objective="abstain_recall")
        grid = [0.0, 0.1, 0.35, 0.6, 0.8, 0.95, 1.5]
        best = max(score_abstain(rows, threshold=t).abstain_recall for t in grid)
        assert fitted.abstain_recall == pytest.approx(best)
        # Ties break toward the lower threshold: staying silent too often is a
        # feature nobody experiences as help.
        assert (
            fitted.threshold
            <= min(
                t
                for t in grid
                if score_abstain(rows, threshold=t).abstain_recall == pytest.approx(best)
            )
            + 0.25
        )

    def test_a_split_with_no_scores_cannot_be_calibrated(self) -> None:
        fitted = fit_abstain_threshold([result("rt-1", ("a",), (), top_score=None)])
        assert fitted.threshold in (0.0, math.inf)


class TestPercentile:
    def test_the_ends_and_the_middle(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]
        assert percentile(values, 0) == pytest.approx(1.0)
        assert percentile(values, 50) == pytest.approx(2.5)
        assert percentile(values, 100) == pytest.approx(4.0)

    def test_an_empty_sample_is_zero_not_a_crash(self) -> None:
        assert percentile([], 95) == 0.0
