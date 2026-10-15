"""Reciprocal rank fusion: the ordering the hybrid serves, and the number it cannot.

These tests carry the Day-35 finding in their names on purpose. RRF is
*ordinal*: a chunk both systems ranked first gets confidence ~1.0 whether the
query was answerable or nonsense, so it is a good ordering and a useless
threshold. ``test_confidence_is_ordinal_not_a_relevance_estimate`` is the
regression test for the design decision that followed from measuring that
(``hybrid.fused_evidence`` exists because of it).
"""

import pytest

from app.services.rag.fusion import reciprocal_rank_fusion


class TestReciprocalRankFusion:
    def test_a_unanimous_first_place_is_confidence_one(self) -> None:
        fused = reciprocal_rank_fusion({"dense": ["a#00", "b#00"], "bm25": ["a#00", "b#00"]}, k=60)
        assert [item.id for item in fused] == ["a#00", "b#00"]
        assert fused[0].confidence == pytest.approx(1.0)

    def test_agreement_beats_a_single_systems_first_place(self) -> None:
        fused = reciprocal_rank_fusion(
            {"dense": ["a#00", "shared#00"], "bm25": ["b#00", "shared#00"]}, k=60
        )
        assert fused[0].id == "shared#00"
        assert fused[0].ranks == {"dense": 2, "bm25": 2}

    def test_the_weight_moves_the_order(self) -> None:
        # No shared candidate here, on purpose: when the two systems disagree
        # completely, the weight is the only thing that decides. (When they
        # agree, agreement wins even at 0.9/0.1 — see the test above, which is
        # the property that makes RRF worth using at all.)
        rankings = {"dense": ["a#00", "c#00"], "bm25": ["b#00", "d#00"]}
        dense_heavy = reciprocal_rank_fusion(rankings, weights={"dense": 0.9, "bm25": 0.1}, k=60)
        lexical_heavy = reciprocal_rank_fusion(rankings, weights={"dense": 0.1, "bm25": 0.9}, k=60)
        assert dense_heavy[0].id == "a#00"
        assert lexical_heavy[0].id == "b#00"

    def test_weights_are_renormalised_so_scale_is_not_a_knob(self) -> None:
        rankings = {"dense": ["a#00"], "bm25": ["b#00"]}
        small = reciprocal_rank_fusion(rankings, weights={"dense": 0.7, "bm25": 0.3}, k=60)
        large = reciprocal_rank_fusion(rankings, weights={"dense": 7.0, "bm25": 3.0}, k=60)
        assert [item.score for item in small] == pytest.approx([item.score for item in large])

    def test_a_zero_weight_switches_a_system_off(self) -> None:
        fused = reciprocal_rank_fusion(
            {"dense": ["a#00"], "bm25": ["b#00", "c#00"]},
            weights={"dense": 0.0, "bm25": 1.0},
            k=60,
        )
        assert [item.id for item in fused] == ["b#00", "c#00"]

    def test_all_zero_weights_are_refused(self) -> None:
        with pytest.raises(ValueError, match="positive weight"):
            reciprocal_rank_fusion({"dense": ["a#00"]}, weights={"dense": 0.0}, k=60)

    def test_a_negative_weight_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not be negative"):
            reciprocal_rank_fusion({"dense": ["a#00"]}, weights={"dense": -0.5}, k=60)

    def test_a_missing_weight_defaults_to_one(self) -> None:
        fused = reciprocal_rank_fusion({"dense": ["a#00"], "bm25": ["a#00"]}, k=60)
        assert fused[0].confidence == pytest.approx(1.0)

    def test_k_flattens_the_gap_between_ranks(self) -> None:
        rankings = {"dense": ["a#00", "b#00"]}
        small_k = reciprocal_rank_fusion(rankings, k=1)
        large_k = reciprocal_rank_fusion(rankings, k=1000)
        assert (small_k[0].score - small_k[1].score) > (large_k[0].score - large_k[1].score)

    def test_confidence_stays_inside_the_unit_interval(self) -> None:
        fused = reciprocal_rank_fusion(
            {"dense": ["a#00", "b#00", "c#00"], "bm25": ["c#00", "b#00", "a#00"]}, k=60
        )
        assert all(0.0 < item.confidence <= 1.0 for item in fused)

    def test_ties_break_on_the_id_so_the_output_is_a_pure_function(self) -> None:
        rankings = {"dense": ["b#00", "a#00"], "bm25": ["a#00", "b#00"]}
        first = reciprocal_rank_fusion(rankings, k=60)
        second = reciprocal_rank_fusion(rankings, k=60)
        assert [item.id for item in first] == [item.id for item in second] == ["a#00", "b#00"]

    def test_empty_and_degenerate_input(self) -> None:
        assert reciprocal_rank_fusion({}) == []
        assert reciprocal_rank_fusion({"dense": []}) == []
        with pytest.raises(ValueError, match="k must be positive"):
            reciprocal_rank_fusion({"dense": ["a#00"]}, k=0)

    def test_confidence_is_ordinal_not_a_relevance_estimate(self) -> None:
        """The measurement that made the abstain floor a separate number.

        Two rankings with the same *shape* get the same confidence, whatever the
        queries meant. Measured on the real corpus during Day 35: the
        out-of-scope query "best crypto to buy today" fused at 0.995 while the
        answerable "how do I stop a panic attack" fused at 0.984. A threshold on
        this number would have abstained on the answerable query first.
        """

        answerable = reciprocal_rank_fusion(
            {"dense": ["panic#00", "other#00"], "bm25": ["panic#00", "other#00"]}, k=60
        )
        nonsense = reciprocal_rank_fusion(
            {"dense": ["crypto#00", "other#00"], "bm25": ["crypto#00", "other#00"]}, k=60
        )
        assert answerable[0].confidence == pytest.approx(nonsense[0].confidence)
