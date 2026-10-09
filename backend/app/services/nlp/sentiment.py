"""Polarity sentiment - the fallback carried over from the legacy chatbot.

``legacy/chatbot-1/`` is **not present in this repository** (ADR 0001 says it
should be kept there when the Flask source is supplied; it has not been yet,
and PROGRESS.md lists restoring it in the parking lot). So this module ports
the *idea* that source was known for rather than its code: two weighted word
lists, a running score, and a normalisation into ``[-1, 1]`` - the shape every
rule-based chatbot sentiment helper takes.

It is kept separate from :class:`~app.services.nlp.keyword.KeywordFallbackAnalyzer`
because it answers a different question. The keyword analyzer asks "which of
nine emotions?" and can say ``anger`` or ``loneliness``; this one asks "how
pleasant is this?" and is better at that, but it can only ever report
joy/calm/neutral/sadness. The chain therefore runs the keyword analyzer first
and keeps this one behind it, so a text with a polarity word but no emotion
word still gets a defensible valence instead of a flat ``neutral``.

Known blind spot, stated up front: negation, contrast ("fine, **but** I can't
sleep") and sarcasm are handled crudely or not at all. That is why it is a
fallback and never the first choice.
"""

from __future__ import annotations

from collections.abc import Mapping
from math import tanh
from typing import Final

from app.services.nlp.base import (
    EMOTION_DIMENSIONS,
    EmotionAnalyzer,
    EmotionResult,
    neutral_result,
    normalize_scores,
)
from app.services.nlp.lexicon import NEGATION_FACTOR, emphasis, scan

POSITIVE_TERMS: Final[Mapping[str, float]] = {
    "happy": 1.0,
    "glad": 0.8,
    "good": 0.6,
    "great": 0.9,
    "excellent": 1.0,
    "amazing": 1.0,
    "wonderful": 1.0,
    "love": 0.9,
    "loved": 0.9,
    "like": 0.4,
    "enjoy": 0.8,
    "enjoying": 0.8,
    "fun": 0.7,
    "calm": 0.8,
    "peaceful": 0.9,
    "relaxed": 0.8,
    "relieved": 0.8,
    "safe": 0.7,
    "supported": 0.8,
    "hopeful": 0.8,
    "better": 0.6,
    "improving": 0.7,
    "proud": 0.8,
    "grateful": 0.9,
    "thankful": 0.8,
    "okay": 0.3,
    "ok": 0.25,
    "fine": 0.3,
    "alright": 0.3,
    "fine today": 0.4,
    "slept well": 0.9,
    "rested": 0.7,
    "smiling": 0.9,
    "laughing": 0.8,
    "khush": 1.0,
    "acha": 0.5,
    "accha": 0.5,
    "badhiya": 0.9,
    "theek": 0.4,
    "thik": 0.35,
    "shant": 0.9,
    "sukoon": 0.9,
    "bhalo": 0.6,
    "valo": 0.6,
    "ভালো": 0.6,
    "अच्छा": 0.5,
}

NEGATIVE_TERMS: Final[Mapping[str, float]] = {
    "sad": 1.0,
    "unhappy": 1.0,
    "miserable": 1.0,
    "depressed": 1.0,
    "hopeless": 1.0,
    "helpless": 0.9,
    "lonely": 1.0,
    "alone": 0.8,
    "isolated": 0.9,
    "angry": 0.9,
    "furious": 1.0,
    "annoyed": 0.7,
    "frustrated": 0.8,
    "hate": 0.9,
    "scared": 0.9,
    "afraid": 0.9,
    "terrified": 1.0,
    "anxious": 1.0,
    "nervous": 0.8,
    "worried": 0.9,
    "stressed": 0.9,
    "stress": 0.8,
    "tired": 0.5,
    "exhausted": 0.7,
    "drained": 0.7,
    "crying": 0.9,
    "cry": 0.8,
    "tears": 0.7,
    "hurt": 0.7,
    "broken": 0.8,
    "empty": 0.7,
    "numb": 0.8,
    "worthless": 1.0,
    "useless": 0.9,
    "ashamed": 0.9,
    "guilty": 0.9,
    "failure": 0.8,
    "failed": 0.7,
    "disappointed": 0.8,
    "bad": 0.8,
    "awful": 1.0,
    "terrible": 1.0,
    "horrible": 1.0,
    "worst": 1.0,
    "painful": 0.9,
    "pain": 0.8,
    "suffering": 1.0,
    "struggling": 0.7,
    "cant sleep": 0.8,
    "insomnia": 0.8,
    "udaas": 1.0,
    "dukhi": 1.0,
    "dukh": 0.9,
    "pareshan": 0.9,
    "akela": 1.0,
    "akeli": 1.0,
    "gussa": 0.9,
    "dar": 0.8,
    "darr": 0.9,
    "thak": 0.6,
    "bekar": 0.8,
    "kosto": 1.0,
    "kharap": 0.8,
    "উদাস": 1.0,
    "কষ্ট": 1.0,
    "उदास": 1.0,
}

#: Contrast markers. Whatever follows them is what the person actually means
#: ("I'm fine, but I haven't slept in three days").
CONTRAST_TERMS: Final[frozenset[str]] = frozenset(
    {"but", "however", "though", "although", "yet", "except", "magar", "lekin", "par", "kintu"}
)

#: Weight applied to the clause before / after a contrast marker.
PRE_CONTRAST_WEIGHT: Final[float] = 0.5
POST_CONTRAST_WEIGHT: Final[float] = 1.5

#: tanh divisor: raw lexicon sums saturate gently rather than pinning at ±1.
SATURATION: Final[float] = 2.5


def _clause_weights(text: str) -> list[float]:
    """One weight per token: heavier after a contrast marker, lighter before."""
    tokens = [token.casefold().replace("'", "") for token in text.replace("\n", " ").split()]
    weights = [1.0] * len(tokens)
    last_contrast = -1
    for index, token in enumerate(tokens):
        if token in CONTRAST_TERMS:
            last_contrast = index
    if last_contrast >= 0:
        for index in range(len(tokens)):
            weights[index] = POST_CONTRAST_WEIGHT if index > last_contrast else PRE_CONTRAST_WEIGHT
    return weights


def polarity(text: str) -> float:
    """Sentiment polarity in ``[-1, 1]``: negative, neutral-ish, or positive.

    A running weighted sum of polarity terms - negation flips a term, the
    clause after "but" counts for more - squashed with ``tanh`` so no single
    word can reach the extremes.
    """
    if not text or not text.strip():
        return 0.0

    terms: dict[str, float] = {**POSITIVE_TERMS, **NEGATIVE_TERMS}

    clause = _clause_weights(text)
    scale = emphasis(text)

    total = 0.0
    for hit in scan(text, terms):
        # A term listed in both lexicons is ambiguous; read it as negative,
        # because missing distress is worse than over-reading a flat message.
        negative = NEGATIVE_TERMS.get(hit.term)
        if negative is not None:
            sign, weight = -1.0, negative
        else:
            sign, weight = 1.0, POSITIVE_TERMS.get(hit.term, 0.0)
        weight *= hit.multiplier * scale
        if hit.index < len(clause):
            weight *= clause[hit.index]
        if hit.negated:
            sign = -sign
            weight *= NEGATION_FACTOR
        total += sign * weight

    return round(max(-1.0, min(1.0, tanh(total / SATURATION))), 6)


def emotion_for(polarity_score: float) -> str:
    """Map a polarity score onto the (narrow) set of emotions it can support."""
    if polarity_score >= 0.35:
        return "joy"
    if polarity_score >= 0.12:
        return "calm"
    if polarity_score > -0.12:
        return "neutral"
    return "sadness"


class SentimentAnalyzer(EmotionAnalyzer):
    """Lexicon polarity, reported as an emotion result.

    Valence *is* the polarity score (not the taxonomy anchor), because a
    continuous reading is the one thing this analyzer does well; arousal comes
    from the banded emotion's anchor, which is the honest limit of a
    polarity-only reading.
    """

    name = "sentiment"

    def __init__(
        self,
        positive: Mapping[str, float] = POSITIVE_TERMS,
        negative: Mapping[str, float] = NEGATIVE_TERMS,
    ) -> None:
        self._positive = dict(positive)
        self._negative = dict(negative)

    @property
    def term_count(self) -> int:
        return len(self._positive) + len(self._negative)

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        if not text or not text.strip():
            return neutral_result(analyzer=self.name, language=lang)

        score = polarity(text)
        emotion = emotion_for(score)
        if emotion == "neutral" and score == 0.0:
            return neutral_result(analyzer=self.name, language=lang)

        # Confidence grows with |polarity|: a flat reading is not a finding.
        confidence = min(1.0, abs(score))
        return EmotionResult(
            primary=emotion,
            scores=normalize_scores({emotion: 1.0}),
            valence=score,
            arousal=EMOTION_DIMENSIONS[emotion][1],
            analyzer=self.name,
            language=lang,
            confidence=round(confidence, 6),
        )
