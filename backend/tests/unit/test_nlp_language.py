"""Unit tests for language detection, including the Hinglish heuristics."""

from __future__ import annotations

import pytest

from app.services.nlp.language import (
    HINGLISH_MARKERS,
    HINGLISH_THRESHOLD,
    SUPPORTED_LANGUAGES,
    detect_language,
    hinglish_score,
    script_of,
)
from app.services.nlp.lexicon import tokenize

HINGLISH_SENTENCE = "exam kal hai, bahut dar lag raha hai"
ENGLISH_SENTENCE = "I got the job and I can't stop smiling"


class TestTokenizer:
    def test_english_words_survive_normalisation(self) -> None:
        assert tokenize("I can't stop SMILING!") == ["i", "cant", "stop", "smiling"]

    def test_devanagari_words_stay_whole(self) -> None:
        """Regression: ``\\w`` drops combining marks, which split every word.

        With the old pattern "अकेला" tokenised as "अक" + "ल" and no Hindi
        lexicon term could ever match.
        """
        assert tokenize("मैं बहुत अकेला महसूस कर रहा हूँ") == [
            "मैं",
            "बहुत",
            "अकेला",
            "महसूस",
            "कर",
            "रहा",
            "हूँ",
        ]

    def test_bengali_words_stay_whole(self) -> None:
        assert tokenize("আমি আজ খুব একা বোধ করছি") == [
            "আমি",
            "আজ",
            "খুব",
            "একা",
            "বোধ",
            "করছি",
        ]

    def test_punctuation_only_text_has_no_tokens(self) -> None:
        assert tokenize("... !!! ???") == []


class TestScript:
    def test_devanagari_is_detected(self) -> None:
        assert script_of("मैं बहुत अकेला हूँ") == "devanagari"

    def test_bengali_is_detected(self) -> None:
        assert script_of("আমি আজ খুব একা বোধ করছি") == "bengali"

    def test_latin_script_is_the_default(self) -> None:
        assert script_of(ENGLISH_SENTENCE) == "latin"

    def test_empty_and_digit_only_text_have_no_script(self) -> None:
        assert script_of("") == "none"
        assert script_of("   \n\t ") == "none"
        assert script_of("12345 !!!") == "none"

    def test_a_few_indic_characters_do_not_flip_a_latin_message(self) -> None:
        assert script_of("I am fine, mostly. om") == "latin"


class TestHinglish:
    def test_a_hinglish_sentence_scores_high(self) -> None:
        score = hinglish_score(HINGLISH_SENTENCE)
        assert score >= HINGLISH_THRESHOLD
        assert score == pytest.approx(0.7, abs=0.15)

    def test_plain_english_scores_near_zero(self) -> None:
        assert hinglish_score(ENGLISH_SENTENCE) < HINGLISH_THRESHOLD

    def test_one_borrowed_word_does_not_make_english_into_hindi(self) -> None:
        text = (
            "I have been trying to explain to my manager that the deadline is "
            "impossible and honestly yaar it is exhausting"
        )
        assert hinglish_score(text) < HINGLISH_THRESHOLD

    def test_empty_text_scores_zero(self) -> None:
        assert hinglish_score("") == 0.0
        assert hinglish_score("   ") == 0.0

    def test_every_marker_is_a_lowercase_ascii_token(self) -> None:
        """A marker with a capital or a space could never match a token."""
        for marker, weight in HINGLISH_MARKERS.items():
            assert marker == marker.casefold(), marker
            assert " " not in marker, marker
            assert 0.0 < weight <= 1.0, marker


class TestDetectLanguage:
    def test_english_prose_is_english(self) -> None:
        info = detect_language("I have been feeling really low this whole week")
        assert info.lang == "en"
        assert info.hinglish is False
        assert info.script == "latin"

    def test_hinglish_is_hindi_and_says_so(self) -> None:
        info = detect_language(HINGLISH_SENTENCE)
        assert info.lang == "hi"
        assert info.hinglish is True
        assert info.confidence > 0

    def test_devanagari_is_hindi(self) -> None:
        info = detect_language("मैं बहुत अकेला महसूस कर रहा हूँ")
        assert (info.lang, info.script, info.hinglish) == ("hi", "devanagari", False)
        assert info.confidence == pytest.approx(0.99)

    def test_bengali_script_is_bengali(self) -> None:
        info = detect_language("আমি আজ খুব একা বোধ করছি")
        assert (info.lang, info.script) == ("bn", "bengali")

    def test_short_english_misfires_are_folded_to_english_with_capped_confidence(self) -> None:
        """langdetect reads "I am fine" as Italian with probability 1.0.

        For a product that speaks en/hi/bn, plain ASCII prose is assumed
        English - but the confidence is capped so it reads as an assumption.
        """
        info = detect_language("I am fine")
        assert info.lang == "en"
        assert info.confidence <= 0.5

    def test_an_unsupported_language_stays_other(self) -> None:
        info = detect_language("こんにちは、げんきですか")
        assert info.lang == "other"

    def test_empty_text_is_other_with_no_confidence(self) -> None:
        info = detect_language("")
        assert info.lang == "other"
        assert info.confidence == 0.0
        assert info.script == "none"
        assert info.hinglish is False

    def test_punctuation_only_is_other(self) -> None:
        assert detect_language("!!! ??? ...").lang == "other"

    def test_the_answer_is_always_in_the_supported_set(self) -> None:
        for text in [ENGLISH_SENTENCE, HINGLISH_SENTENCE, "", "123", "こんにちは"]:
            assert detect_language(text).lang in SUPPORTED_LANGUAGES

    def test_detection_is_deterministic(self) -> None:
        """langdetect samples profiles unless its seed is pinned."""
        first = detect_language("I feel so alone lately")
        for _ in range(5):
            assert detect_language("I feel so alone lately") == first

    def test_the_threshold_decides_when_the_signal_is_weak(self) -> None:
        text = "kal exam hai"
        lenient = detect_language(text, threshold=0.05)
        strict = detect_language(text, threshold=0.99)
        assert (lenient.lang, lenient.hinglish) == ("hi", True)
        assert strict.hinglish is False
