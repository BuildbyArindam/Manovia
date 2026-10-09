"""Language detection: ``en`` / ``hi`` / ``bn`` / ``other``, plus Hinglish.

Three signals, in the order they are trustworthy:

1. **Script.** Devanagari means Hindi and Bengali script means Bengali with
   near-certainty, and it costs nothing. Script always wins.
2. **Hinglish heuristics.** Most of Manovia's Hindi users type Latin script
   ("exam kal hai, bahut dar lag raha hai"). No statistical detector can label
   that as Hindi, because it is not Hindi orthography - it is a code-mixed
   register. So a weighted marker list decides it, and the result is flagged
   ``hinglish=True`` so callers know the signal is lexical, not statistical.
3. **``langdetect``** for everything else, folded to the four supported codes.

``langdetect`` is deterministic only with a fixed seed (it samples profiles),
so :data:`DetectorFactory.seed` is pinned at import time; otherwise the same
sentence can flip between runs and tests would be flaky.
"""

from __future__ import annotations

import re
from typing import Any, Final

from pydantic import BaseModel, Field

SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ("en", "hi", "bn", "other")
OTHER: Final[str] = "other"


# Fixed seed: langdetect picks among language profiles pseudo-randomly, so an
# unpinned seed makes the same short text detect differently run to run.
def _load_langdetect() -> tuple[Any, Any, Any, bool]:
    """Import ``langdetect`` and pin its seed.

    Returns ``(DetectorFactory, exception type, detect_langs, available)``; a
    missing library is not an error, it just means script and Hinglish
    heuristics are all that is left.
    """
    try:
        from langdetect import DetectorFactory, LangDetectException, detect_langs
    except ImportError:  # pragma: no cover - only on an install without langdetect
        return None, Exception, None, False
    # Without a fixed seed langdetect samples language profiles and the same
    # short sentence can detect differently between runs.
    DetectorFactory.seed = 0
    return DetectorFactory, LangDetectException, detect_langs, True


_DetectorFactory, _LangDetectException, _detect_langs, LANGDETECT_AVAILABLE = _load_langdetect()

_DEVANAGARI: Final = re.compile(r"[\u0900-\u097F]")
_BENGALI: Final = re.compile(r"[\u0980-\u09FF]")
# Word characters *plus* the combining marks that Indic scripts are written
# with. ``\w`` alone matches Devanagari and Bengali *letters* but not their
# vowel signs and matras (Unicode category Mn), so "अकेला" tokenises as
# "अक" + "ल" and no Indic lexicon term can ever match. The mark ranges below
# are what makes the Hindi/Bengali lexicons reachable at all.
_MARKS: Final[str] = (
    "\u0300-\u036f"  # combining diacriticals (Latin transliterations)
    "\u0900-\u097f"  # Devanagari
    "\u0980-\u09ff"  # Bengali
    "\u0a00-\u0a7f"  # Gurmukhi
    "\u0b00-\u0b7f"  # Oriya
)
# Both apostrophes: ASCII and the curly one word processors substitute.
_APOSTROPHES: Final[str] = "'\u2019"
_WORD: Final = re.compile(rf"(?:[^\W\d_]|[{_MARKS}{_APOSTROPHES}])+", re.UNICODE)

# Romanised-Hindi markers. Weighted: a function word ("hai", "nahi") is weak
# evidence on its own, a content word ("udaas", "pareshan") is strong.
HINGLISH_MARKERS: Final[dict[str, float]] = {
    # Copula / function words: common, but also common in pidgin English.
    "hai": 0.6,
    "hain": 0.6,
    "hun": 0.6,
    "hoon": 0.8,
    "tha": 0.6,
    "thi": 0.6,
    "the": 0.2,
    "raha": 0.8,
    "rahi": 0.8,
    "rahe": 0.7,
    "kya": 0.7,
    "kyun": 0.8,
    "kyu": 0.6,
    "kyon": 0.8,
    "nahi": 1.0,
    "nahin": 1.0,
    "nhi": 0.9,
    "mat": 0.5,
    "aur": 0.6,
    "ya": 0.2,
    "toh": 0.9,
    "to": 0.1,
    "bhi": 0.9,
    "ka": 0.4,
    "ki": 0.4,
    "ke": 0.3,
    "ko": 0.6,
    "se": 0.4,
    "mein": 0.8,
    "me": 0.1,
    "par": 0.4,
    "phir": 0.9,
    "abhi": 0.9,
    "kal": 0.9,
    "aaj": 0.8,
    "waise": 1.0,
    "matlab": 0.9,
    "acha": 0.9,
    "accha": 0.9,
    "achha": 0.9,
    "theek": 0.9,
    "thik": 0.8,
    "zyada": 0.9,
    "kam": 0.5,
    "sab": 0.7,
    "kuch": 0.8,
    "koi": 0.7,
    "mujhe": 1.0,
    "mera": 0.9,
    "meri": 0.9,
    "tum": 0.8,
    "tumhe": 0.9,
    "aap": 0.7,
    "hum": 0.8,
    "yaar": 1.0,
    "dost": 0.9,
    "bahut": 1.0,
    "bohot": 1.0,
    "bhot": 1.0,
    "thoda": 0.9,
    # Mental-health vocabulary in romanised Hindi - strong and on-domain.
    "pareshan": 1.0,
    "pareshani": 1.0,
    "udaas": 1.0,
    "udas": 1.0,
    "dukhi": 1.0,
    "dukh": 1.0,
    "ghabra": 0.9,
    "ghabrahat": 1.0,
    "bechain": 1.0,
    "chinta": 1.0,
    "tension": 0.6,
    "dar": 0.9,
    "darr": 1.0,
    "dara": 0.9,
    "akela": 1.0,
    "akeli": 1.0,
    "tanha": 1.0,
    "akelapan": 1.0,
    "gussa": 1.0,
    "naraaz": 1.0,
    "sharminda": 1.0,
    "thak": 0.8,
    "thaka": 0.8,
    "thaki": 0.8,
    "neend": 1.0,
    "sukoon": 1.0,
    "shant": 1.0,
    "dil": 0.8,
    "zindagi": 1.0,
    "zindgi": 1.0,
    "lag": 0.8,
    "lagta": 0.9,
    "lagti": 0.9,
    "rah": 0.5,
    "karta": 0.8,
    "karti": 0.8,
    "karna": 0.7,
    "chahiye": 1.0,
    "sakta": 0.8,
    "sakti": 0.8,
    "jata": 0.7,
    "jati": 0.7,
}

#: Score at or above which Latin-script text is read as Hinglish.
HINGLISH_THRESHOLD: Final[float] = 0.18

#: Short texts carry no reliable statistical signal.
_MIN_STATISTICAL_LENGTH: Final[int] = 4

#: Plain-ASCII prose with at least this many words is assumed English when the
#: statistical detector returns a language Manovia does not support.
_MIN_ASCII_TOKENS: Final[int] = 3

#: Confidence cap for an *assumed* language (never presented as detected).
_ASCII_ENGLISH_CONFIDENCE: Final[float] = 0.5


class LanguageInfo(BaseModel):
    """What detection concluded, and how much it is worth."""

    lang: str = Field(description="One of en, hi, bn, other.")
    confidence: float = Field(ge=0.0, le=1.0, description="Detector confidence, 0..1.")
    hinglish: bool = Field(default=False, description="Romanised Hindi detected lexically.")
    script: str = Field(default="latin", description="latin, devanagari, bengali, other, none.")


def tokenize(text: str) -> list[str]:
    """Lowercased word tokens, keeping Devanagari/Bengali words intact."""
    return [token.casefold() for token in _WORD.findall(text)]


def hinglish_score(text: str) -> float:
    """Weighted share of tokens that are romanised-Hindi markers (0..1).

    Weighted per marker and divided by the token count, so a long English
    message containing one borrowed word does not become Hindi.
    """
    tokens = tokenize(text)
    if not tokens:
        return 0.0
    hits = sum(HINGLISH_MARKERS.get(token, 0.0) for token in tokens)
    return min(1.0, hits / len(tokens))


def script_of(text: str) -> str:
    """The dominant non-Latin script in ``text``, or ``latin``/``none``."""
    if not text.strip():
        return "none"
    devanagari = len(_DEVANAGARI.findall(text))
    bengali = len(_BENGALI.findall(text))
    letters = sum(1 for character in text if character.isalpha())
    if letters == 0:
        return "none"
    if devanagari / letters >= 0.3:
        return "devanagari"
    if bengali / letters >= 0.3:
        return "bengali"
    return "latin"


def _statistical(text: str) -> tuple[str, float]:
    """langdetect's answer, folded to the supported set. ``("other", 0.0)``
    when the library is missing, the text is too short, or it is unsure."""
    if not LANGDETECT_AVAILABLE or len(text.strip()) < _MIN_STATISTICAL_LENGTH:
        return OTHER, 0.0
    try:
        candidates = _detect_langs(text)
    except _LangDetectException:  # pragma: no cover - langdetect's own parse error
        return OTHER, 0.0
    if not candidates:  # pragma: no cover - defensive
        return OTHER, 0.0
    best = candidates[0]
    code = str(best.lang).split("-", maxsplit=1)[0].casefold()
    probability = round(max(0.0, min(1.0, float(best.prob))), 4)
    if code in SUPPORTED_LANGUAGES:
        return code, probability
    # An unsupported answer on plain ASCII prose is usually a short-text
    # misfire: langdetect reads "I am fine" as Italian with probability 1.0.
    # The product's languages are en/hi/bn, so assume English - but cap the
    # confidence to say "assumed", never "detected".
    tokens = tokenize(text)
    if tokens and all(token.isascii() for token in tokens) and len(tokens) >= _MIN_ASCII_TOKENS:
        return "en", min(probability, _ASCII_ENGLISH_CONFIDENCE)
    return OTHER, probability


def detect_language(text: str, *, threshold: float = HINGLISH_THRESHOLD) -> LanguageInfo:
    """Detect the language of ``text``.

    Never raises and never returns anything outside :data:`SUPPORTED_LANGUAGES`.
    """
    script = script_of(text)
    if script == "none":
        return LanguageInfo(lang=OTHER, confidence=0.0, hinglish=False, script=script)

    if script == "devanagari":
        return LanguageInfo(lang="hi", confidence=0.99, hinglish=False, script=script)
    if script == "bengali":
        return LanguageInfo(lang="bn", confidence=0.99, hinglish=False, script=script)

    score = hinglish_score(text)
    if score >= threshold:
        # Lexical, not statistical: confidence tracks the marker share, and the
        # hinglish flag says so out loud.
        return LanguageInfo(
            lang="hi", confidence=round(min(1.0, score * 2.0), 4), hinglish=True, script=script
        )

    lang, confidence = _statistical(text)
    return LanguageInfo(lang=lang, confidence=confidence, hinglish=False, script=script)
