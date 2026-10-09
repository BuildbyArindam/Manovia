"""Tiny offline lexicons; no downloads, training data, or clinical claims."""

import re

from app.services.nlp.base import Emotion, EmotionAnalyzer, EmotionResult, from_scores

LEXICON: dict[Emotion, tuple[str, ...]] = {
    "joy": ("happy", "joy", "smiling", "smile", "excited", "got the job", "khush", "खुश", "খুশি"),
    "sadness": ("sad", "cry", "crying", "unhappy", "udas", "उदास", "দুঃখ"),
    "anger": ("angry", "furious", "hate", "gussa", "गुस्सा", "রাগ"),
    "fear": ("afraid", "scared", "terrified", "dar", "डर", "ভয়"),
    "anxiety": ("anxious", "worried", "panic", "nervous", "chinta", "चिंता", "চিন্তা"),
    "shame": ("ashamed", "embarrassed", "shame", "sharm"),
    "loneliness": ("alone", "lonely", "isolated", "akela", "अकेला", "একা"),
    "calm": ("calm", "peaceful", "relaxed", "shant", "शांत"),
    "neutral": (),
}


def keyword_scores(text: str) -> dict[Emotion, float]:
    words = re.findall(r"[\w\u0900-\u09ff]+|n't", text.lower())
    scores: dict[Emotion, float] = {}
    for label, phrases in LEXICON.items():
        for phrase in phrases:
            tokens = phrase.split()
            for index in range(len(words) - len(tokens) + 1):
                # A small local negation window, not a syntactic parser.
                if words[index : index + len(tokens)] == tokens and not (
                    set(words[max(0, index - 2) : index]) & {"not", "never", "no", "n't"}
                ):
                    scores[label] = 0.9
    return scores


class SentimentAnalyzer:
    """Polarity fallback inspired by lexicon sentiment, NOT an unread legacy port."""

    def analyze(self, text: str, lang: str | None = None) -> float:
        return from_scores(keyword_scores(text)).valence


class KeywordFallbackAnalyzer(EmotionAnalyzer):
    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        return from_scores(keyword_scores(text))


class FakeEmotionAnalyzer(EmotionAnalyzer):
    """Deterministic offline keyword fake behind the same contract."""

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        return from_scores(keyword_scores(text))
