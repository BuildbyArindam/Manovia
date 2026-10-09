"""Unit tests for the shared lexical rules: tokens, negation, emphasis."""

from __future__ import annotations

import pytest

from app.services.nlp.lexicon import (
    NEGATION_FACTOR,
    NEGATIONS,
    TRANSPARENT,
    Hit,
    emphasis,
    normalize,
    scan,
)

TERMS = {"happy": 1.0, "sad": 1.0, "alone": 1.0, "fed up": 1.0, "up": 1.0}


def hits(text: str, terms: dict[str, float] | None = None) -> list[Hit]:
    return list(scan(text, terms if terms is not None else TERMS))


class TestNormalize:
    def test_case_and_apostrophes_are_folded(self) -> None:
        assert normalize("I CAN'T Stop") == "i cant stop"
        assert normalize("I can\u2019t stop") == "i cant stop"

    def test_indic_text_is_left_readable(self) -> None:
        assert normalize("मैं अकेला हूँ") == "मैं अकेला हूँ"


class TestScan:
    def test_a_term_is_found_with_its_index(self) -> None:
        found = hits("today I am happy")
        assert [(hit.term, hit.index) for hit in found] == [("happy", 3)]

    def test_a_two_word_phrase_wins_over_its_first_word(self) -> None:
        found = hits("I am fed up with everything")
        assert [hit.term for hit in found] == ["fed up"]

    def test_a_word_that_is_not_a_phrase_still_matches_alone(self) -> None:
        assert [hit.term for hit in hits("cheer up")] == ["up"]

    def test_no_terms_means_no_hits(self) -> None:
        assert hits("the quick brown fox") == []
        assert hits("") == []
        assert hits("   ") == []

    def test_repeated_terms_produce_repeated_hits(self) -> None:
        assert len(hits("happy happy happy")) == 3


class TestNegation:
    @pytest.mark.parametrize("negation", ["not", "never", "cant", "dont", "isnt"])
    def test_a_direct_negation_flips_the_term(self, negation: str) -> None:
        found = hits(f"I am {negation} happy")
        assert len(found) == 1
        assert found[0].negated is True

    def test_negation_reaches_across_transparent_words(self) -> None:
        assert hits("I am not feeling happy")[0].negated is True
        assert hits("I am not at all happy")[0].negated is True

    def test_a_content_word_stops_the_negation_scan(self) -> None:
        """The case that matters: "can't stop smiling" is not negated."""
        found = hits("I can't stop being happy", {"happy": 1.0, "stop": 1.0})
        happy = next(hit for hit in found if hit.term == "happy")
        assert happy.negated is False

    def test_negation_does_not_reach_backwards_forever(self) -> None:
        assert hits("not really, but today I am happy")[0].negated is False

    def test_romanised_negations_are_recognised(self) -> None:
        assert hits("main khush nahi happy")[0].negated is True

    def test_the_negation_factor_weakens_rather_than_deletes(self) -> None:
        assert 0.0 < NEGATION_FACTOR < 1.0

    def test_negations_and_transparent_words_do_not_overlap(self) -> None:
        """A word in both sets would make the scan contradict itself."""
        assert not (NEGATIONS & TRANSPARENT)


class TestIntensifiers:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("so happy", 1.3), ("very happy", 1.4), ("a bit happy", 0.7), ("happy", 1.0)],
    )
    def test_the_preceding_word_scales_the_hit(self, text: str, expected: float) -> None:
        assert hits(text)[0].multiplier == pytest.approx(expected)

    def test_an_intensifier_two_words_back_does_nothing(self) -> None:
        assert hits("so incredibly extremely happy")[0].multiplier == pytest.approx(1.5)
        assert hits("very much happy")[0].multiplier == pytest.approx(1.0)


class TestEmphasis:
    def test_plain_text_gets_no_boost(self) -> None:
        assert emphasis("I am fine today") == 1.0

    def test_exclamation_marks_add_a_capped_boost(self) -> None:
        assert emphasis("help!") == pytest.approx(1.02)
        assert emphasis("help!!!!!!!!!!") == pytest.approx(1.1)
        assert emphasis("help" + "!" * 50) == pytest.approx(1.1)  # still capped

    def test_shouting_adds_a_capped_boost(self) -> None:
        assert emphasis("I AM SO ANGRY") > emphasis("I am so angry")
        assert emphasis("I AM SO ANGRY") <= 1.2

    def test_empty_text_is_not_emphasised(self) -> None:
        assert emphasis("") == 1.0
        assert emphasis("   ") == 1.0
