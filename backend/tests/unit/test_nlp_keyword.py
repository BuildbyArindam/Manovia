"""Unit tests for the keyword fallback analyzer - the last line of defence."""

from __future__ import annotations

import unicodedata

import pytest

from app.services.nlp.base import EMOTIONS, NEUTRAL
from app.services.nlp.keyword import LEXICON, OPPOSITE, KeywordFallbackAnalyzer


@pytest.fixture
def analyzer() -> KeywordFallbackAnalyzer:
    return KeywordFallbackAnalyzer()


class TestEachEmotion:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("I got the job and I can't stop smiling", "joy"),
            ("I feel so alone lately", "loneliness"),
            ("everything makes me sad and I keep crying", "sadness"),
            ("I am so angry about what happened", "anger"),
            ("I am scared of what comes next", "fear"),
            ("I feel anxious and I cannot stop overthinking", "anxiety"),
            ("I feel ashamed of myself", "shame"),
            ("today I feel calm and rested", "calm"),
        ],
    )
    def test_english(self, analyzer: KeywordFallbackAnalyzer, text: str, expected: str) -> None:
        assert analyzer.analyze(text).primary == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("exam kal hai, bahut dar lag raha hai", "fear"),
            ("main aaj bahut akela feel kar raha hoon", "loneliness"),
            ("mujhe bahut chinta ho rahi hai", "anxiety"),
            ("aaj main khush hoon", "joy"),
            ("main bahut udaas hoon", "sadness"),
            ("mujhe gussa aa raha hai", "anger"),
            ("sab theek hai", "calm"),
        ],
    )
    def test_hinglish(self, analyzer: KeywordFallbackAnalyzer, text: str, expected: str) -> None:
        assert analyzer.analyze(text).primary == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("मैं बहुत अकेला महसूस कर रहा हूँ", "loneliness"),
            ("आज मेरा मन खराब है", "sadness"),
            ("मुझे डर लग रहा है", "fear"),
            ("मैं शांत हूँ", "calm"),
            ("আমি আজ খুব একা বোধ করছি", "loneliness"),
            ("আমার মন খারাপ", "sadness"),
            ("আমি ভালো আছি", "joy"),
        ],
    )
    def test_indic_scripts(
        self, analyzer: KeywordFallbackAnalyzer, text: str, expected: str
    ) -> None:
        """Regression: these all read as neutral until the tokenizer kept the
        combining marks inside the word."""
        assert analyzer.analyze(text).primary == expected


class TestEmptyAndUnknown:
    @pytest.mark.parametrize("text", ["", "   ", "\n\t ", "..."])
    def test_nothing_to_read_is_neutral(self, analyzer: KeywordFallbackAnalyzer, text: str) -> None:
        result = analyzer.analyze(text)
        assert result.primary == NEUTRAL
        assert result.valence == 0.0
        assert result.confidence == 0.0
        assert result.is_informative is False

    def test_text_with_no_lexicon_terms_is_neutral(self, analyzer: KeywordFallbackAnalyzer) -> None:
        result = analyzer.analyze("the quarterly report is on the desk by Tuesday")
        assert result.primary == NEUTRAL
        assert result.confidence == 0.0

    def test_the_language_hint_is_carried_through(self, analyzer: KeywordFallbackAnalyzer) -> None:
        assert analyzer.analyze("I am sad", "en").language == "en"
        assert analyzer.analyze("I am sad").language is None

    def test_the_analyzer_name_and_model(self, analyzer: KeywordFallbackAnalyzer) -> None:
        assert analyzer.name == "keyword"
        assert analyzer.model_id is None
        assert analyzer.term_count > 100


class TestPragmatics:
    def test_negation_moves_the_reading_to_the_opposite_emotion(
        self, analyzer: KeywordFallbackAnalyzer
    ) -> None:
        assert analyzer.analyze("I am happy today").primary == "joy"
        negated = analyzer.analyze("I am not happy")
        assert negated.primary == "sadness"
        assert negated.valence < 0

    def test_negating_a_negative_word_softens_it(self, analyzer: KeywordFallbackAnalyzer) -> None:
        assert analyzer.analyze("I am not alone").primary == "joy"

    def test_an_intensifier_beats_a_bare_word(self, analyzer: KeywordFallbackAnalyzer) -> None:
        plain = analyzer.analyze("I am ok, a little sad")
        assert plain.primary == "sadness"
        assert analyzer.analyze("I am so ok").primary == "calm"

    def test_confidence_is_capped_below_model_grade(
        self, analyzer: KeywordFallbackAnalyzer
    ) -> None:
        """A keyword hit is not a probability and must not look like one."""
        result = analyzer.analyze("I am absolutely terrified and furious")
        assert 0.0 < result.confidence <= 0.6

    def test_results_are_deterministic(self, analyzer: KeywordFallbackAnalyzer) -> None:
        text = "I feel so alone and I cannot sleep"
        assert analyzer.analyze(text) == analyzer.analyze(text)

    def test_valence_and_arousal_follow_the_primary_label(
        self, analyzer: KeywordFallbackAnalyzer
    ) -> None:
        sad = analyzer.analyze("I am so sad")
        joy = analyzer.analyze("I am so happy")
        assert sad.valence < 0 < joy.valence
        assert sad.arousal < joy.arousal

    def test_the_top_score_matches_the_primary(self, analyzer: KeywordFallbackAnalyzer) -> None:
        result = analyzer.analyze("I am scared and a little sad")
        assert result.scores[result.primary] == max(result.scores.values())


class TestLexiconData:
    def test_every_lexicon_bucket_is_a_real_emotion(self) -> None:
        assert set(LEXICON) <= set(EMOTIONS)
        assert NEUTRAL not in LEXICON  # neutral is the absence of a hit

    def test_every_weight_is_in_range(self) -> None:
        for emotion, terms in LEXICON.items():
            for term, weight in terms.items():
                assert 0.0 < weight <= 1.0, (emotion, term)

    def test_no_term_is_mixed_script(self) -> None:
        """A term blending Devanagari and Bengali codepoints matches nothing."""
        for emotion, terms in LEXICON.items():
            for term in terms:
                if term.isascii():
                    continue
                scripts = {unicodedata.name(char, "?").split()[0] for char in term}
                assert len(scripts) == 1, (emotion, repr(term), scripts)

    def test_terms_are_lowercase_and_have_no_stray_whitespace(self) -> None:
        for terms in LEXICON.values():
            for term in terms:
                assert term == term.casefold(), term
                assert term == term.strip(), term
                assert "  " not in term, term

    def test_phrases_are_at_most_two_words(self) -> None:
        for terms in LEXICON.values():
            for term in terms:
                assert len(term.split()) <= 2, term

    def test_every_emotion_can_be_reached(self) -> None:
        """An emotion with an empty bucket could never be reported."""
        for emotion in EMOTIONS:
            if emotion == NEUTRAL:
                continue
            assert LEXICON[emotion], emotion

    def test_opposite_covers_every_label(self) -> None:
        assert set(OPPOSITE) == set(EMOTIONS)
        for target in OPPOSITE.values():
            assert target in EMOTIONS

    def test_a_custom_lexicon_can_be_injected(self) -> None:
        custom = KeywordFallbackAnalyzer({"joy": {"whee": 1.0}})
        assert custom.analyze("whee!").primary == "joy"
        assert custom.analyze("I am sad").primary == NEUTRAL
        assert custom.term_count == 1
