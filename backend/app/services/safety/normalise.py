"""Normalisation and pragmatics for the safety rules engine.

A rules engine over free text lives or dies on what it does *before* matching.
Everything here exists so that a phrase written one way in the data file matches
the dozens of ways a distressed person actually types it — and so that the same
phrase in a sentence that means the opposite does not.

The pipeline (:func:`normalise`):

1. **Invisible characters out.** Zero-width spaces, joiners, soft hyphens and
   BOMs are the cheapest way to hide a phrase from a matcher.
2. **Apostrophes deleted, not spaced** — ``don't`` and ``dont`` must be the same
   token, because every English contraction can be typed without one.
3. **Case folded.** Shouting is a signal for the emotion service, not for risk:
   the patterns are lowercase and so is everything they are matched against.
4. **Punctuation becomes a space**, except Indic combining marks, which are kept
   (stripping a Devanagari matra destroys the word it belongs to — the same trap
   ``app/services/nlp/lexicon.py`` documents).
5. **Three variants** are produced, deduplicated, and *all* of them searched:
   plain, leet-substituted (``k1ll`` -> ``kill``) and spelling-corrected
   (``sucide`` -> ``suicide``). Phrase rules get two more projections: whitespace
   stripped (``w a n t  t o  d i e`` -> ``wanttodie``) and repeated letters
   collapsed (``diiiie`` -> ``die``).

Then the pragmatics, applied per hit rather than per message:

* **Clauses.** Text is split on sentence punctuation and on the conjunctions that
  reverse a meaning ("but", "although"), so "I don't want to die but I can't go
  on" reads as two statements, not one contradictory blur.
* **Negation.** A plain negation immediately before a negation-sensitive hit
  cancels it. ``cant``/``cannot`` are *not* cues, on purpose: "I can't stop
  thinking about killing myself" must still fire.
* **Figurative speech.** Suppressed only on *positive* evidence — a benign frame
  from the data ("dying to know") or an inanimate subject right before the hit
  ("this traffic is killing me"). Suspicion is never enough to drop a hit.
* **Quoted / third-person text.** A risky span that exists *only* inside
  quotation marks, or in a message plainly about somebody else, is flagged — never
  discarded: someone reporting a friend's plan still needs a real response, just
  a differently shaped one.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from app.services.safety.patterns import ContextData, collapse_repeats

#: The engine's hard input cap. Longer input is cut, and the assessment says so
#: (``context.truncated``) rather than pretending it read everything.
DEFAULT_MAX_INPUT_CHARS: Final = 4_000

#: How far back a negation may reach, in tokens, before it is too far away to be
#: about this hit.
MAX_BACKWARD_TOKENS: Final = 8
#: Transparent words ("not *feeling* better") that may be skipped in that window.
MAX_TRANSPARENT_SKIPS: Final = 5
#: How far forward a post-verbal (Bengali) negation may reach.
MAX_FORWARD_TOKENS: Final = 3
#: How many words immediately before a hit are examined for an inanimate subject.
#: Short on purpose: an unpunctuated message is one clause, and a subject near
#: its start says nothing about a hit near its end.
SUBJECT_WINDOW: Final = 3

#: Zero-width and formatting characters, deleted outright.
_INVISIBLE: Final = dict.fromkeys(
    map(
        ord,
        "\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad\u180e",
    ),
    None,
)

#: Apostrophes in every shape a keyboard or a word processor produces. Deleted,
#: not replaced by a space: ``don't`` must become ``dont``.
_APOSTROPHES: Final = "\u0027\u2018\u2019\u02bc\u02b9\u00b4`"

#: Character ranges that survive normalisation: word characters, whitespace, and
#: the combining marks Indic scripts are written with.
_MARKS: Final = (
    "\u0300-\u036f"  # combining diacriticals (Latin transliterations)
    "\u0900-\u097f"  # Devanagari
    "\u0980-\u09ff"  # Bengali
    "\u0a00-\u0a7f"  # Gurmukhi
    "\u0b00-\u0b7f"  # Oriya
    "\u0c00-\u0c7f"  # Telugu
)

#: Punctuation -> space.
_PUNCTUATION: Final = re.compile(rf"[^\w\s{_MARKS}]")
#: Punctuation -> space, except the clause bar, which survives (see
#: :func:`to_clause_text`).
_PUNCTUATION_KEEPING_BAR: Final = re.compile(rf"[^\w\s|{_MARKS}]")

#: Sentence punctuation: these become clause boundaries.
_CLAUSE_PUNCTUATION: Final = re.compile(r"[,;:!?.\n\r\t]+")

#: The word that marks a clause boundary in :func:`to_clause_text`. Chosen
#: because normalisation turns a literal ``|`` into a space everywhere else, so
#: it cannot appear by accident.
_CLAUSE_BAR: Final = "|"

_WHITESPACE: Final = re.compile(r"\s+")
_SEPARATORS: Final = re.compile(r"[\s|]+")

#: Conjunctions that start a new thought even without punctuation.
_CLAUSE_WORDS: Final = re.compile(
    r"\b(?:and|but|though|although|because|so|while|whereas|except|however|anyway)\b"
)

#: Quoted spans. A single-quoted span must contain a space, so an
#: apostrophe-heavy sentence is not misread as a quotation.
_QUOTES: Final = re.compile(r'"([^"]{2,})"|\u201c([^\u201d]{2,})\u201d|\'([^\']*\s[^\']*)\'')


@dataclass(frozen=True)
class Clause:
    """One stretch of text that should be read as a single statement."""

    text: str
    start: int
    end: int


@dataclass(frozen=True)
class ClauseIndex:
    """A text and its clauses, with offsets that are valid *in that text*."""

    text: str
    clauses: tuple[Clause, ...]

    def clause_at(self, position: int) -> Clause:
        """The clause containing ``position`` (falls back to the first)."""
        for clause in self.clauses:
            if clause.start <= position < clause.end:
                return clause
        return self.clauses[0]


@dataclass(frozen=True)
class NormalisedMessage:
    """The projections of one input that the engine searches."""

    #: Punctuation-spaced, apostrophe-free, case-folded text.
    primary: str
    #: Everything to search: ``primary`` plus the leet and spelling variants.
    variants: tuple[str, ...]
    #: ``primary`` with all whitespace removed (fused-spelling matching).
    squashed: str
    #: ``primary`` with repeated letters collapsed (``diiiie`` matching).
    collapsed: str
    #: Clause boundaries for ``primary``, at the same character offsets.
    index: ClauseIndex
    #: True when the input was longer than the cap and has been cut.
    truncated: bool
    #: Length of the original input, in characters. Metadata, not content.
    char_length: int

    def index_for(self, variant: str) -> ClauseIndex:
        """The clause index for one variant.

        ``primary`` keeps the punctuation-derived boundaries computed by
        :func:`normalise`; the other variants have already lost their
        punctuation, so they are split on conjunctions only. That is enough for
        negation, which looks at a handful of tokens, and it is honest about what
        the variant can still tell us.
        """
        if variant == self.primary:
            return self.index
        return index_clauses(variant)


def strip_invisible(text: str) -> str:
    """Remove zero-width characters and apply NFKC (so matras compose consistently)."""
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE)


def apply_leet(text: str, leet_map: Mapping[str, str]) -> str:
    """Substitute look-alike characters (``k1ll`` -> ``kill``).

    Applied to the raw text *before* punctuation handling, so ``k!ll`` works too.
    Every value in the map must be a single character: the variant stays
    offset-aligned with :attr:`primary`, which is what lets one clause index
    serve both.
    """
    return "".join(leet_map.get(char, char) for char in text)


def _fold(text: str) -> str:
    """Apostrophes out, case folded — the shared first step of both projections."""
    return "".join(char for char in text if char not in _APOSTROPHES).casefold()


def to_primary(text: str) -> str:
    """Case-fold, drop apostrophes, turn punctuation into spaces, collapse gaps."""
    spaced = _PUNCTUATION.sub(" ", _fold(text))
    return _WHITESPACE.sub(" ", spaced).strip()


def to_clause_text(text: str) -> str:
    """Like :func:`to_primary`, but sentence punctuation becomes a clause bar.

    The result is the same length as ``to_primary(text)`` for the same input —
    every run of punctuation or whitespace collapses to exactly one character in
    both — so a match offset found in one is valid in the other.
    """
    marked = _CLAUSE_PUNCTUATION.sub(_CLAUSE_BAR, _fold(text))
    spaced = _PUNCTUATION_KEEPING_BAR.sub(" ", marked)
    collapsed = _SEPARATORS.sub(
        lambda match: _CLAUSE_BAR if _CLAUSE_BAR in match.group(0) else " ", spaced
    )
    return collapsed.strip(_CLAUSE_BAR + " ")


def correct_spellings(primary: str, context: ContextData) -> str:
    """Apply the curated misspelling map, longest source first."""
    corrected = primary
    for pattern, replacement in context.misspellings:
        corrected = pattern.sub(replacement, corrected)
    return corrected


def squash(text: str) -> str:
    """Remove every space: the projection fused spellings are matched against."""
    return _WHITESPACE.sub("", text)


def index_clauses(text: str, *, marked: str | None = None) -> ClauseIndex:
    """Split ``text`` into clauses and record their offsets.

    ``marked`` is :func:`to_clause_text` output for the same text; when given,
    its bars are clause boundaries too. Clause text always comes from ``text``,
    so tokens are plain words.
    """
    if not text:
        return ClauseIndex(text="", clauses=(Clause(text="", start=0, end=0),))

    cuts: list[tuple[int, int]] = []
    if marked is not None:
        cuts += [(match.start(), match.end()) for match in re.finditer(r"\|", marked)]
    cuts += [(match.start(), match.end()) for match in _CLAUSE_WORDS.finditer(text)]
    cuts.sort()

    boundaries: list[int] = [0]
    for start, end in cuts:
        if start > boundaries[-1]:
            boundaries.append(start)
        boundaries.append(end)
    boundaries.append(len(text))

    clauses: list[Clause] = []
    for left, right in pairwise(boundaries):
        chunk = text[left:right]
        stripped = chunk.strip()
        if not stripped:
            continue
        offset = left + chunk.index(stripped[0])
        clauses.append(Clause(text=stripped, start=offset, end=offset + len(stripped)))
    if not clauses:
        clauses.append(Clause(text=text, start=0, end=len(text)))
    return ClauseIndex(text=text, clauses=tuple(clauses))


def is_negated(
    clause: Clause,
    start: int,
    end: int,
    context: ContextData,
    *,
    language: str | None = None,
    negates_itself: bool = False,
) -> bool:
    """True when a negation cancels the hit inside this clause.

    Two directions, because the languages differ:

    * **before** the hit, for English and Hindi ("I *don't* want to die").
      Transparent words are skipped, so "not feeling any better" still counts as
      negated; the scan stops at the first content word, which keeps a distant
      "not" from cancelling something it says nothing about.
    * **after** the hit, only for patterns tagged with a language, because
      Bengali negates post-verbally ("ami bachte chai *na*"). An English hit can
      never be cancelled by a trailing word.
    * **inside** the hit, again only for language-tagged patterns, because Hindi
      puts the cue between the verb stem and its auxiliary: "main marna *nahi*
      chahta". A pattern that spans the phrase would otherwise see the cue in
      neither direction and read a refusal as a wish.

    ``cant``/``cannot`` are deliberately absent from the cue list: inability is
    not absence.
    """
    prefix = clause.text[: max(0, start - clause.start)].split()[-MAX_BACKWARD_TOKENS:]
    skipped = 0
    for token in reversed(prefix):
        if token in context.transparent_words:
            skipped += 1
            if skipped > MAX_TRANSPARENT_SKIPS:
                break
            continue
        if token in context.negation_cues:
            return True
        # First content word and it is not a cue: stop looking backwards. Do not
        # return yet — a language that negates later in the phrase ("main marna
        # *nahi* chahta", "o morte chay *na*") still has its say below.
        break

    if language:
        if not negates_itself:
            inside = clause.text[max(0, start - clause.start) : max(0, end - clause.start)].split()
            cues = context.negation_cues | context.negation_after_cues
            if any(token in cues for token in inside):
                return True

        suffix = clause.text[max(0, end - clause.start) :].split()[:MAX_FORWARD_TOKENS]
        for token in suffix:
            if token in context.transparent_words:
                continue
            return token in context.negation_after_cues
    return False


def looks_figurative(
    clause: Clause,
    start: int,
    context: ContextData,
    *,
    subject_evidence: bool = False,
) -> bool:
    """True only on positive evidence that the wording is exaggeration.

    Two kinds of evidence, both from ``patterns_context.yaml``:

    * a benign frame somewhere in the clause ("dying to know", "bored to death",
      "killing it") — checked for every figurative-prone rule;
    * an inanimate subject among the last few words before the hit ("this
      **traffic** is killing me") — checked only when the rule opts in with
      ``subject_evidence``.

    The subject test is opt-in because applying it everywhere would swallow real
    statements in unpunctuated text: "this exam is killing me i want to die" is
    one clause, and a subject at its start says nothing about a hit at its end.
    Absence of evidence is never treated as evidence of a joke.
    """
    if any(frame.search(clause.text) for frame in context.benign_frames):
        return True
    if not subject_evidence:
        return False

    tokens = clause.text[: max(0, start - clause.start)].split()[-SUBJECT_WINDOW:]
    if not tokens:
        return False
    return context.benign_subject_pattern.search(" ".join(tokens)) is not None


def has_first_person(text: str, context: ContextData) -> bool:
    """True when ``text`` has a first-person subject that really is one.

    Possessive-relative phrases are blanked out first. "my friend says she wants
    to die" contains the word "my" but is not about the speaker; "I want to die,
    my mum doesn't care" keeps its "I" and stays about the speaker.

    Blanking — rather than refusing to look — matters because the phrase is only
    evidence *against* the speaker. Whether it is evidence *for* somebody else is
    a separate question, answered by the third-person markers, which read the
    original text.
    """
    stripped = context.relation_pattern.sub(" ", text)
    return context.first_person_pattern.search(stripped) is not None


def split_quotes(raw: str) -> tuple[str, str]:
    """Split raw text into (outside the quotes, inside the quotes).

    Used to answer one question: does the risky wording exist *only* inside a
    quotation? If it does, the speaker is reporting somebody else's words.
    """
    quoted: list[str] = []

    def replace(match: re.Match[str]) -> str:
        quoted.append(match.group(0))
        return " "

    remainder = _QUOTES.sub(replace, raw)
    return remainder, " ".join(quoted)


def normalise(
    text: str,
    context: ContextData,
    *,
    max_chars: int = DEFAULT_MAX_INPUT_CHARS,
) -> NormalisedMessage:
    """Produce every projection of ``text`` that the engine will search."""
    char_length = len(text)
    truncated = char_length > max_chars
    working = text[:max_chars] if truncated else text

    cleaned = strip_invisible(working)
    primary = to_primary(cleaned)
    leet_variant = to_primary(apply_leet(cleaned, context.leet_map))
    corrected = correct_spellings(primary, context)
    # Stretched letters *and* a typo in the same message ("kiiiillll meee") need
    # both fixes, in that order: collapsing first turns the stretch into a word
    # the spelling map recognises. This is a variant rather than a projection, so
    # it keeps its clause boundaries and its pragmatics — negation and figurative
    # sense are still readable in it, because collapsing never joins two words.
    collapsed_variant = correct_spellings(collapse_repeats(primary), context)

    variants: list[str] = []
    for candidate in (primary, leet_variant, corrected, collapsed_variant):
        if candidate and candidate not in variants:
            variants.append(candidate)

    return NormalisedMessage(
        primary=primary,
        variants=tuple(variants),
        squashed=squash(primary),
        collapsed=collapse_repeats(primary),
        index=index_clauses(primary, marked=to_clause_text(cleaned)),
        truncated=truncated,
        char_length=char_length,
    )
