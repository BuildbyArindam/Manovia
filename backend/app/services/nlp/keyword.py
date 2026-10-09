"""A tiny lexicon-based emotion analyzer - the last line of defence.

Used when the model is unavailable, too slow, or throws. It is intentionally
dumb: a weighted word list per emotion with the negation/intensifier rules from
:mod:`app.services.nlp.lexicon`. It cannot understand context, sarcasm or
anything it has not been told about, and it says so by keeping
``confidence`` low. What it must never do is crash or invent a strong reading:
unknown text yields ``neutral`` with valence 0.

Coverage is English plus romanised Hindi (Hinglish) and some Banglish, because
that is what the product's users actually type. Devanagari and Bengali script
terms are included too - they are free to add and the script detector already
routes those messages here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from app.services.nlp.base import (
    NEUTRAL,
    EmotionAnalyzer,
    EmotionResult,
    build_result,
    neutral_result,
)
from app.services.nlp.lexicon import NEGATION_FACTOR, emphasis, scan

#: Per-emotion lexicon. Weights are "how strongly this word implies the
#: emotion", roughly 0.4 = weak/contextual, 1.0 = near-definitive.
LEXICON: Final[dict[str, dict[str, float]]] = {
    "joy": {
        "happy": 0.9,
        "happiness": 0.9,
        "joy": 0.95,
        "glad": 0.7,
        "great": 0.6,
        "good": 0.4,
        "well": 0.3,
        "love": 0.7,
        "loved": 0.7,
        "loving": 0.6,
        "amazing": 0.8,
        "wonderful": 0.8,
        "excited": 0.8,
        "excitement": 0.8,
        "thrilled": 0.9,
        "grateful": 0.8,
        "gratitude": 0.8,
        "thankful": 0.7,
        "proud": 0.7,
        "pride": 0.6,
        "smile": 0.8,
        "smiling": 0.85,
        "laugh": 0.7,
        "laughing": 0.7,
        "laughed": 0.7,
        "celebrate": 0.8,
        "celebrating": 0.8,
        "celebrated": 0.8,
        "blessed": 0.7,
        "content": 0.6,
        "delighted": 0.9,
        "pleased": 0.6,
        "fantastic": 0.8,
        "awesome": 0.8,
        "hopeful": 0.7,
        "hope": 0.5,
        "succeeded": 0.8,
        "success": 0.7,
        "passed": 0.7,
        "promoted": 0.8,
        "got the job": 0.9,
        "better": 0.4,
        "positive": 0.5,
        "enjoy": 0.7,
        "enjoying": 0.7,
        "fun": 0.6,
        # Romanised Hindi / Banglish
        "khush": 0.9,
        "khushi": 0.9,
        "acha": 0.5,
        "accha": 0.5,
        "achha": 0.5,
        "badhiya": 0.8,
        "zabardast": 0.9,
        "mast": 0.7,
        "maza": 0.7,
        "shukriya": 0.7,
        "garv": 0.7,
        "bhalo": 0.5,
        "valo": 0.5,
        "anondo": 0.9,
        # Devanagari / Bengali script
        "खुश": 0.9,
        "खुशी": 0.9,
        "अच्छा": 0.5,
        "ভালো": 0.5,
    },
    "sadness": {
        "sad": 0.9,
        "sadness": 0.9,
        "unhappy": 0.85,
        "miserable": 0.9,
        "depressed": 0.9,
        "depressing": 0.8,
        "down": 0.5,
        "low": 0.4,
        "hopeless": 0.9,
        "helpless": 0.8,
        "crying": 0.85,
        "cry": 0.8,
        "cried": 0.8,
        "tears": 0.7,
        "tearful": 0.8,
        "heartbroken": 0.95,
        "empty": 0.6,
        "numb": 0.7,
        "grief": 0.9,
        "grieving": 0.9,
        "disappointed": 0.7,
        "disappointing": 0.6,
        "failure": 0.6,
        "failed": 0.6,
        "worthless": 0.8,
        "useless": 0.7,
        "exhausted": 0.5,
        "tired": 0.4,
        "gloomy": 0.8,
        "hurt": 0.6,
        "broken": 0.6,
        "giving up": 0.8,
        "give up": 0.6,
        "lost": 0.5,
        "miss": 0.4,
        "udaas": 0.9,
        "udas": 0.9,
        "dukhi": 0.9,
        "dukh": 0.8,
        "mayus": 0.8,
        "niraash": 0.8,
        "nirash": 0.8,
        "rona": 0.8,
        "ro": 0.5,
        "bekar": 0.6,
        "bekaar": 0.6,
        "thak": 0.5,
        "thaka": 0.5,
        "kosto": 0.9,
        "kharap": 0.7,
        "kanna": 0.8,
        "उदास": 0.9,
        "दुख": 0.8,
        "কষ্ট": 0.9,
        "खराब": 0.7,
        "খারাপ": 0.7,
    },
    "anger": {
        "angry": 0.95,
        "anger": 0.95,
        "furious": 0.95,
        "rage": 0.9,
        "mad": 0.6,
        "annoyed": 0.8,
        "annoying": 0.6,
        "irritated": 0.8,
        "frustrated": 0.8,
        "frustrating": 0.7,
        "frustration": 0.8,
        "hate": 0.85,
        "hated": 0.8,
        "hatred": 0.9,
        "resentful": 0.8,
        "livid": 0.95,
        "insulted": 0.8,
        "unfair": 0.6,
        "fed up": 0.85,
        "sick of": 0.7,
        "gussa": 0.95,
        "gussaa": 0.9,
        "naraaz": 0.85,
        "naraz": 0.85,
        "jhallaya": 0.8,
        "jalan": 0.6,
        "nafrat": 0.9,
        "rag": 0.8,
        "raga": 0.8,
        "गुस्सा": 0.95,
        "राग": 0.8,
    },
    "fear": {
        "scared": 0.9,
        "scary": 0.8,
        "fear": 0.9,
        "afraid": 0.9,
        "terrified": 0.95,
        "panic": 0.9,
        "panicking": 0.95,
        "panicked": 0.9,
        "dread": 0.85,
        "frightened": 0.9,
        "nightmare": 0.7,
        "nightmares": 0.7,
        "danger": 0.7,
        "unsafe": 0.85,
        "threat": 0.8,
        "threatened": 0.85,
        "dar": 0.85,
        "darr": 0.9,
        "dara": 0.85,
        "darna": 0.85,
        "khauf": 0.9,
        "bhay": 0.85,
        "ghabra": 0.8,
        "voy": 0.9,
        "bhoy": 0.9,
        "डर": 0.9,
        "ভয়": 0.9,
    },
    "anxiety": {
        "anxious": 0.95,
        "anxiety": 0.95,
        "nervous": 0.85,
        "nerves": 0.7,
        "worried": 0.9,
        "worry": 0.85,
        "worrying": 0.85,
        "stress": 0.85,
        "stressed": 0.9,
        "stressful": 0.85,
        "tense": 0.7,
        "restless": 0.8,
        "overthinking": 0.9,
        "uneasy": 0.8,
        "unease": 0.8,
        "pressure": 0.6,
        "on edge": 0.85,
        "cant sleep": 0.7,
        "cant relax": 0.8,
        "chinta": 0.9,
        "tension": 0.8,
        "pareshan": 0.9,
        "pareshani": 0.9,
        "bechain": 0.9,
        "ghabrahat": 0.85,
        "exam": 0.5,
        "চিন্তা": 0.9,
        "चिंता": 0.9,
        "परेशान": 0.9,
    },
    "shame": {
        "ashamed": 0.95,
        "shame": 0.85,
        "shameful": 0.9,
        "embarrassed": 0.9,
        "embarrassing": 0.8,
        "humiliated": 0.95,
        "humiliation": 0.9,
        "guilty": 0.9,
        "guilt": 0.85,
        "disgrace": 0.85,
        "stupid": 0.6,
        "pathetic": 0.7,
        "let everyone down": 0.9,
        "let down": 0.6,
        "burden": 0.7,
        "sharminda": 0.95,
        "sharm": 0.8,
        "apmaan": 0.85,
        "bezzat": 0.9,
        "galti": 0.5,
        "शर्म": 0.85,
    },
    "loneliness": {
        "lonely": 0.95,
        "loneliness": 0.95,
        "alone": 0.85,
        "isolated": 0.9,
        "isolation": 0.9,
        "abandoned": 0.85,
        "unseen": 0.8,
        "unheard": 0.8,
        "disconnected": 0.8,
        "left out": 0.85,
        "ignored": 0.7,
        "no one": 0.7,
        "nobody": 0.7,
        "no friends": 0.85,
        "on my own": 0.5,
        "by myself": 0.5,
        "akela": 0.95,
        "akeli": 0.95,
        "tanha": 0.9,
        "akelapan": 0.95,
        "koi nahi": 0.85,
        "ekla": 0.9,
        "একা": 0.95,
        "अकेला": 0.95,
    },
    "calm": {
        "calm": 0.9,
        "calmer": 0.85,
        "peaceful": 0.9,
        "peace": 0.7,
        "relaxed": 0.85,
        "relax": 0.6,
        "okay": 0.4,
        "ok": 0.35,
        "alright": 0.45,
        "fine": 0.45,
        "steady": 0.6,
        "quiet": 0.5,
        "rested": 0.7,
        "slept well": 0.8,
        "relieved": 0.8,
        "relief": 0.7,
        "comfortable": 0.6,
        "safe": 0.7,
        "grounded": 0.7,
        "mindful": 0.6,
        "manageable": 0.6,
        "theek": 0.6,
        "thik": 0.55,
        "shant": 0.9,
        "sukoon": 0.9,
        "araam": 0.7,
        "aaram": 0.7,
        "shanti": 0.85,
        "normal": 0.3,
        "ठीक": 0.6,
        "শান্ত": 0.9,
        "शांत": 0.9,
    },
}

#: Where a negated term lands instead: "not happy" leans sadness, "not alone"
#: leans joy, "not scared" leans calm.
OPPOSITE: Final[Mapping[str, str]] = {
    "joy": "sadness",
    "sadness": "joy",
    "anger": "calm",
    "fear": "calm",
    "anxiety": "calm",
    "shame": "joy",
    "loneliness": "joy",
    "calm": "anxiety",
    NEUTRAL: NEUTRAL,
}


class KeywordFallbackAnalyzer(EmotionAnalyzer):
    """Lexicon lookup over the nine-label taxonomy. No model, no network."""

    name = "keyword"

    def __init__(self, lexicon: Mapping[str, Mapping[str, float]] = LEXICON) -> None:
        self._lexicon: dict[str, dict[str, float]] = {
            emotion: dict(terms) for emotion, terms in lexicon.items()
        }
        self._terms: dict[str, float] = {}
        for terms in self._lexicon.values():
            self._terms.update(terms)
        self._by_term: dict[str, str] = {}
        for emotion, terms in self._lexicon.items():
            for term in terms:
                # First emotion wins on a shared term, so behaviour stays
                # deterministic regardless of dict ordering.
                self._by_term.setdefault(term, emotion)

    @property
    def term_count(self) -> int:
        """How many lexicon entries are loaded (used by tests and logs)."""
        return len(self._terms)

    def analyze(self, text: str, lang: str | None = None) -> EmotionResult:
        if not text or not text.strip():
            return neutral_result(analyzer=self.name, language=lang)

        weights: dict[str, float] = {emotion: 0.0 for emotion in self._lexicon}
        scale = emphasis(text)
        best_raw = 0.0
        for hit in scan(text, self._terms):
            emotion = self._by_term[hit.term]
            weight = self._terms[hit.term] * hit.multiplier * scale
            if hit.negated:
                emotion = OPPOSITE.get(emotion, NEUTRAL)
                weight *= NEGATION_FACTOR
            weights[emotion] = min(1.0, weights[emotion] + weight)
            best_raw = max(best_raw, min(1.0, weight))

        if max(weights.values()) <= 0.0:
            return neutral_result(analyzer=self.name, language=lang)

        return build_result(
            weights,
            analyzer=self.name,
            language=lang,
            # A lexicon hit is not a probability: cap it so downstream never
            # treats a keyword match as model-grade confidence.
            confidence=min(0.6, best_raw),
        )
