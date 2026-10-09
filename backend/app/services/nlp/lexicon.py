"""Shared lexical rules: tokenising, negation, intensifiers, emphasis.

Both lexicon analyzers (:mod:`app.services.nlp.keyword` and
:mod:`app.services.nlp.sentiment`) need the same four crude-but-honest pieces
of English/Hinglish pragmatics, so they live here once:

* **Normalisation** - apostrophes are removed so ``can't`` matches ``cant``,
  because a lexicon that misses contractions misses most real typing.
* **Negation** - a negation within a short backwards window flips a term's
  meaning. The window skips *transparent* words ("not **feeling** happy") but
  stops at the first content word, which is what keeps "can't **stop** smiling"
  positive: the scan hits "stop" and gives up before it reaches "can't".
* **Intensifiers / downtoners** - "so", "bahut", "thoda" scale a hit.
* **Emphasis** - exclamation marks and ALL-CAPS words nudge the whole reading.

This is deliberately a small, inspectable rule set. It is a *fallback*: it
exists so the product still answers when the model is unavailable, not so it
can compete with one.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Final

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

#: Negations, apostrophes already stripped by :func:`normalize`.
NEGATIONS: Final[frozenset[str]] = frozenset(
    {
        "not",
        "no",
        "never",
        "none",
        "nobody",
        "nothing",
        "cant",
        "cannot",
        "dont",
        "doesnt",
        "didnt",
        "wont",
        "isnt",
        "arent",
        "wasnt",
        "werent",
        "hasnt",
        "havent",
        "hardly",
        "barely",
        "rarely",
        # Romanised Hindi / Banglish negations.
        "nahi",
        "nahin",
        "nhi",
        "na",
        "mat",
        "nei",
        "naa",
    }
)

#: Words that do not break a negation window ("not *feeling* happy").
TRANSPARENT: Final[frozenset[str]] = frozenset(
    {
        "i",
        "im",
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "feel",
        "feels",
        "feeling",
        "felt",
        "get",
        "gets",
        "getting",
        "got",
        "so",
        "very",
        "really",
        "quite",
        "just",
        "still",
        "even",
        "a",
        "an",
        "the",
        "bit",
        "little",
        "of",
        "at",
        "all",
        "that",
        "this",
        "me",
        "myself",
        "hu",
        "hun",
        "hoon",
        "hai",
        "ho",
        "raha",
        "rahi",
        "lag",
        "lagta",
        "lagti",
        "feelings",
    }
)

#: Intensifiers: multiply a hit's weight. Romanised forms included.
INTENSIFIERS: Final[Mapping[str, float]] = {
    "so": 1.3,
    "very": 1.4,
    "really": 1.35,
    "extremely": 1.5,
    "incredibly": 1.5,
    "super": 1.3,
    "totally": 1.3,
    "completely": 1.3,
    "absolutely": 1.4,
    "utterly": 1.4,
    "deeply": 1.4,
    "too": 1.15,
    "constantly": 1.3,
    "always": 1.25,
    "bahut": 1.5,
    "bohot": 1.5,
    "bhot": 1.45,
    "bhaut": 1.45,
    "bhaari": 1.3,
    "khub": 1.4,
    "onek": 1.4,
    "sob": 1.1,
}

#: Downtoners: shrink a hit's weight.
DOWNTONERS: Final[Mapping[str, float]] = {
    "slightly": 0.6,
    "somewhat": 0.65,
    "kinda": 0.7,
    "sorta": 0.7,
    "kind": 0.7,
    "sort": 0.7,
    "little": 0.65,
    "bit": 0.7,
    "thoda": 0.7,
    "thora": 0.7,
    "ektu": 0.7,
    "maybe": 0.8,
    "sometimes": 0.8,
}

#: How far back to look for a negation (transparent words do not count).
NEGATION_WINDOW: Final[int] = 3

#: Negation reverses a term but weakly: "not happy" is evidence of sadness, not
#: proof of it.
NEGATION_FACTOR: Final[float] = 0.6


@dataclass(frozen=True, slots=True)
class Hit:
    """One lexicon term found in the text."""

    term: str
    index: int
    negated: bool
    multiplier: float


def normalize(text: str) -> str:
    """Casefold and drop apostrophes so contractions match the lexicon."""
    return text.casefold().replace("'", "").replace("\u2019", "")


def tokenize(text: str) -> list[str]:
    """Word tokens of :func:`normalize`-ed text."""
    return _WORD.findall(normalize(text))


def _negated(tokens: list[str], index: int) -> bool:
    examined = 0
    cursor = index - 1
    while cursor >= 0 and examined < NEGATION_WINDOW:
        token = tokens[cursor]
        if token in NEGATIONS:
            return True
        if token in TRANSPARENT:
            cursor -= 1
            continue
        return False
    return False


def _multiplier(tokens: list[str], index: int) -> float:
    if index == 0:
        return 1.0
    previous = tokens[index - 1]
    if previous in INTENSIFIERS:
        return INTENSIFIERS[previous]
    if previous in DOWNTONERS:
        return DOWNTONERS[previous]
    return 1.0


def scan(text: str, terms: Mapping[str, float]) -> Iterator[Hit]:
    """Yield every occurrence of a term (single word or two-word phrase).

    Two-word phrases are preferred over their first word, so "fed up" is one
    hit rather than "fed" and "up".
    """
    tokens = tokenize(text)
    if not tokens:
        return
    consumed = -1
    for index, token in enumerate(tokens):
        if index <= consumed:
            continue
        phrase = f"{token} {tokens[index + 1]}" if index + 1 < len(tokens) else None
        if phrase is not None and phrase in terms:
            yield Hit(phrase, index, _negated(tokens, index), _multiplier(tokens, index))
            consumed = index + 1  # "fed up" must not also report "up"
            continue
        if token in terms:
            yield Hit(token, index, _negated(tokens, index), _multiplier(tokens, index))


def emphasis(text: str) -> float:
    """A gentle 1.0-1.2 multiplier from punctuation and SHOUTING.

    Capped, because exclamation marks say "loud", not "clinically significant".
    """
    if not text.strip():
        return 1.0
    bonus = min(0.1, 0.02 * text.count("!"))
    words = [word for word in text.split() if word.isalpha() and len(word) > 2]
    if words:
        shouted = sum(1 for word in words if word.isupper())
        bonus += min(0.1, 0.05 * (shouted / len(words)))
    return round(1.0 + bonus, 4)
