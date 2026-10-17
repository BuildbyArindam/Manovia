"""Day 45: the deterministic meditation-script linter.

The lint is the gate the brief asks for: forbidden content (trance and
dissociation inductions, medical claims, promises of outcomes, closed-eye
instructions while the person may be moving), structure (safety preface,
stop cue, context fit), length and pacing (word budget, sentence length,
pause markers) and clarity (closed jargon list).

Everything is deterministic and offline — no model in the loop. The
optional LLM judge in ``evals/meditation`` is strictly advisory and never
replaces this gate (the same convention as the Day 14/24 output guard).

Findings carry closed codes only; ``detail`` names the code and the count,
never the matched text, so a report can be logged without logging content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.services.meditation.content import content_for
from app.services.meditation.types import Context, Language

# --- forbidden content ------------------------------------------------------


class LintCategory(StrEnum):
    TRANCE_INDUCTION = "trance_induction"
    MEDICAL_CLAIM = "medical_claim"
    PROMISE = "promise"
    EYES_CLOSED = "eyes_closed"
    JARGON = "jargon"
    CONTEXT_MISMATCH = "context_mismatch"
    MISSING_STOP_CUE = "missing_stop_cue"
    MISSING_SAFETY_PREFACE = "missing_safety_preface"
    WORD_BUDGET = "word_budget"
    SENTENCE_TOO_LONG = "sentence_too_long"
    PUNCTUATION_NOISE = "punctuation_noise"


# Patterns are matched against casefolded, primary-normalised text. The
# English sets are the strictest; hi/bn add script-specific markers for the
# same categories (draft-translation backstop — native review is the real
# backstop, recorded in translations/STATUS.md).
TRANCE: Final[frozenset[str]] = frozenset(
    {
        # en
        r"\btrance\b",
        r"\bhypnot",
        r"\bdissociat",
        r"float(ing)? (away|up(ward)?)",
        r"drift(ing)? away",
        r"out of your body",
        r"out-of-body",
        r"sinking (into|down|away)",
        r"you are sinking",
        r"altered state",
        r"enter (a|the|into a|into the) (deep )?(state|place|void)",
        r"mind (goes|going|is) blank",
        r"empty your mind",
        r"let (your )?mind (go|become) blank",
        r"lose (track of time|yourself|count of time)",
        r"you are (no longer|not) here",
        # hi
        r"निद्रालु",
        r"हाइपनोसाइज़",
        r"हाइपनो",
        r"शरीर से बाहर निकल",
        # bn
        r"মাদক",
        r"নিদ্রালু",
        r"হায়্পনো",
        r"শরীর থেকে বাইরে",
    }
)

MEDICAL: Final[frozenset[str]] = frozenset(
    {
        # en — negation-aware entries are in NEGATED_MEDICAL
        r"\bcure(d|s)?\b",
        r"\bdiagnos",
        r"\bmedication\b",
        r"\bmedicines\b",
        r"\bdoses?\b",
        r"\bprescrib",
        r"treatment (for|of|your)",
        r"\btreat (your|this|me|my)\b",
        r"\bheal(ing)? (your|me|my)\b",
        r"blood pressure",
        r"heart (condition|disease)",
        r"\banxiety disorder\b",
        r"\bpanic disorder\b",
        r"\bdepression\b",
        r"\bsymptoms?\b",
        r"\bclinical(ly)?\b",
        r"\bdoctor(s| says| advises)?\b",
        r"\btherapist\b",
        # hi
        r"दवाइयाँ",
        r"दवा ",
        r"डॉक्टर",
        r"रक्तचाप",
        r"हृदय की बीमारी",
        r"एनजायटी डिसऑर्डर",
        r"डिप्रेशन",
        r"लक्षण",
        r"निदान",
        # bn
        r"ঔষধ",
        r"ডাক্তার",
        r"রক্তচাপ",
        r"হৃদরোগ",
        r"চিকিৎসক",
    }
)

#: Terms that are fine negated ("not treatment", "not medical advice", "not
#: therapy") — the preface's own disclosure. Flagged only when not negated.
NEGATED_MEDICAL: Final[frozenset[str]] = frozenset(
    {
        r"\btherapy\b",
        r"\btherapies\b",
        r"\bmedical advice\b",
        r"\btreatment\b",
        r"\bइलाज\b",
        r"\bचिकित्सा\b",
        r"চিকিৎসা",
    }
)

PROMISES: Final[frozenset[str]] = frozenset(
    {
        # en — promises of outcomes. Stating what the app will *not* do
        # ("nothing here will continue on its own") is negated and passes.
        r"\bguarantee(d|s|d)?\b",
        r"\bpromise(d|s)? to\b",
        r"\bthis (will|shall|'ll) (reduce|lower|calm|heal|fix|stop|improve|give|bring|make)\b",
        r"\b(you|it|we) (will|shall|'ll) (feel|be|get|become) "
        r"(calm|relaxed|peaceful|better|fine|safe|less anxious)\b",
        r"\b(you|it) (will|'ll) (reduce|lower|calm|heal|fix|stop|improve|prevent|eliminate)\b",
        r"\bdefinitely\b",
        r"\bcertainly\b",
        r"\bsure to\b",
        r"\bbound to\b",
        r"\bno doubt\b",
        r"\b100 ?% (effective|works|safe)\b",
        r"\balways (works|helps|reduces)\b",
        r"\bin (just )?(minutes?|a few minutes|seconds)\b, (you|you will|we will)\b",
        # hi
        r"गारंटी",
        r"पक्का (है|मिलेगा|होगा)",
        r"आश्वासन",
        r"ज़रूर (शांत|सुधर|ठीक)",
        # bn
        r"গ্যারান্টি",
        r"আশ্বাস",
        r"নিশ্চিত",
    }
)

EYES_CLOSED: Final[frozenset[str]] = frozenset(
    {
        # en
        r"close (your|his|her) eyes",
        r"closing (your|his|her) eyes",
        r"eyes closed",
        r"keep your eyes (closed|shut)",
        r"shut (your|his|her) eyes",
        # hi
        r"आँखें बंद",
        r"अपनी आँखें बंद",
        # bn
        r"চোখ বন্ধ",
    }
)

JARGON: Final[frozenset[str]] = frozenset(
    {
        r"parasympathetic",
        r"sympathetic nervous",
        r"\bvagus\b",
        r"\bvagal\b",
        r"\bcortisol\b",
        r"\bamygdala\b",
        r"endorphin",
        r"\boxytocin\b",
        r"neurotransmit",
        r"prefrontal",
        r"default mode network",
        r"\bautonomic\b",
        r"heart rate variability",
        r"\bhrv\b",
        r"biofeedback",
        r"neural(ly)?",
    }
)

#: Positions the script must not assume (walking or driving).
CONTEXT_ASSUMING_STILLNESS: Final[frozenset[str]] = frozenset(
    {
        r"\blie (down|back)\b",
        r"lying down",
        r"lie on (your|the) back",
        r"get (into|in) (your|the) bed",
        r"settle (into|back) (your|the) bed",
        r"put your feet up",
        # hi
        r"लेट जाएँ",
        r"लेटकर",
        # bn
        r"লেট",
    }
)

#: Words that negate a match within the same sentence (English first; the
#: Indic cues cover the draft-translation negations used in the prefaces).
NEGATION_CUES: Final[frozenset[str]] = frozenset(
    {
        "not",
        "never",
        "no",
        "non-",
        "without",
        "don't",
        "dont",
        "can't",
        "cant",
        "avoid",
        "refrain",
        "nothing",
        "none",
        "नहीं",
        "मत",
        "कभी नहीं",
        "না",
        "নয়",
        "করবেন না",
        "এড়িয়ে",
    }
)

_MAX_SENTENCE_WORDS = 40
_MAX_SENTENCES_TOTAL = 90
_EXCLAMATION_MAX = 2


@dataclass(frozen=True)
class Finding:
    code: str
    category: LintCategory
    count: int = 1

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "category": self.category.value, "count": self.count}


@dataclass(frozen=True)
class LintReport:
    passed: bool
    findings: tuple[Finding, ...]
    word_count: int
    sentence_count: int

    def count(self, category: LintCategory) -> int:
        return sum(f.count for f in self.findings if f.category is category)

    def by_category(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for finding in self.findings:
            out[finding.category.value] = out.get(finding.category.value, 0) + finding.count
        return out

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "findings": [f.as_dict() for f in self.findings],
            "by_category": self.by_category(),
            "word_count": self.word_count,
            "sentence_count": self.sentence_count,
        }


def _casefold(text: str) -> str:
    return text.casefold()


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?।])\s+", text.strip())
    return [p for p in parts if p]


def _anchor_cue(cue: str) -> re.Pattern[str]:
    """Compile a cue so it matches as a whole word.

    ``\\b`` is wrong for two reasons: substring matching made "no" negate
    "NOW close your eyes" (a false negative in the category that must not
    fail open), and Python's ``\\w`` excludes Indic combining marks, so
    ``\\bनहीं\\b`` can never match — the cue ends in a mark, the next char
    is a space, and no boundary exists. Side lookarounds test only the
    neighbouring character, which is what "whole word" actually means here."""
    escaped = re.escape(cue)
    pre = r"(?<!\w)" if re.match(r"\w", cue[0]) is not None else ""
    # A prefix cue ("non-") wants a word after the hyphen; everything else
    # wants the next character to be a non-word character.
    post = r"(?=\S)" if cue[-1] == "-" else r"(?!\w)"
    return re.compile(pre + escaped + post)


_NEGATION_RES: Final[tuple[re.Pattern[str], ...]] = tuple(
    _anchor_cue(cue) for cue in sorted(NEGATION_CUES)
)


def _has_negation(sentence: str) -> bool:
    lowered = sentence.casefold()
    return any(pattern.search(lowered) is not None for pattern in _NEGATION_RES)


def _scan(
    text: str,
    patterns: frozenset[str],
    *,
    negation_aware: bool = False,
) -> int:
    """Count occurrences; with ``negation_aware`` a match inside a negated
    sentence is not a hit ("not treatment" is the preface, not a claim).

    Matching runs on casefolded text: the pattern sets are lowercase, and a
    model that writes "You Will Feel Calm" must not escape the gate on
    capitalisation."""
    text = text.casefold()
    hits = 0
    sentences = _sentences(text)
    for pattern in patterns:
        compiled = re.compile(pattern)
        if negation_aware:
            for sentence in sentences:
                if _has_negation(sentence):
                    continue
                hits += len(compiled.findall(sentence))
        else:
            hits += len(compiled.findall(text))
    return hits


def _required_stop_phrase(language: Language) -> str:
    return content_for(language).stop_cue


def lint_script(
    text: str,
    *,
    language: Language,
    context: Context,
    eyes_open: bool,
    word_budget: int,
    min_achievable_words: int | None = None,
) -> LintReport:
    """Lint one full script. ``word_budget`` is the duration-derived target;
    the total must sit in [floor, 1.20*budget] where the floor is
    0.75*budget, or ``min_achievable_words`` when the curated pool (plus the
    LLM where configured) cannot reach 0.75*budget — long offline scripts are
    then honestly shorter, and the estimate is disclosed, not padded."""
    findings: list[Finding] = []

    def add(category: LintCategory, code: str, count: int) -> None:
        if count > 0:
            findings.append(Finding(code=code, category=category, count=count))

    add(LintCategory.TRANCE_INDUCTION, "trance_induction", _scan(text, TRANCE))
    add(LintCategory.MEDICAL_CLAIM, "medical_claim", _scan(text, MEDICAL))
    add(
        LintCategory.MEDICAL_CLAIM,
        "medical_claim_negated_term",
        _scan(text, NEGATED_MEDICAL, negation_aware=True),
    )
    add(LintCategory.PROMISE, "promise", _scan(text, PROMISES))
    if eyes_open:
        # Eyes-open variant: any closed-eye instruction is a finding. The
        # preface's "no closing your eyes" (walking/driving) is negated and
        # must not trip this, so the scan is negation-aware. In the seated
        # eyes-closed variant (request validated as seated upstream) closed
        # eyes are the point, so the category does not apply.
        add(
            LintCategory.EYES_CLOSED,
            "eyes_closed_instruction",
            _scan(text, EYES_CLOSED, negation_aware=True),
        )
    add(LintCategory.JARGON, "jargon", _scan(text, JARGON))
    if context in {"walking", "driving"}:
        add(
            LintCategory.CONTEXT_MISMATCH,
            "assumes_stillness",
            _scan(text, CONTEXT_ASSUMING_STILLNESS, negation_aware=True),
        )

    # --- structure -------------------------------------------------------
    stop_phrase = _required_stop_phrase(language).split(",")[0].strip().lower()
    if stop_phrase not in text.casefold():
        findings.append(Finding("missing_stop_cue", LintCategory.MISSING_STOP_CUE))
    driving_warning_required = context in {"driving", "unknown"}
    if driving_warning_required:
        marker = {"en": "driving", "hi": "गाड़ी", "bn": "গাড়ি"}[language]
        if marker not in text.casefold() and marker not in text:
            findings.append(Finding("missing_driving_warning", LintCategory.MISSING_SAFETY_PREFACE))
    frame_marker = {"en": "self-guided", "hi": "स्वयं-निर्देशित", "bn": "নিজ-নির্দেশিত"}[language]
    if frame_marker not in text.casefold() and frame_marker not in text:
        # The preface names what this is (self-guided, not treatment); a
        # script without it is missing the safety frame entirely.
        findings.append(Finding("missing_safety_frame", LintCategory.MISSING_SAFETY_PREFACE))

    # --- length and pacing -------------------------------------------------
    words = text.split()
    word_count = len(words)
    sentences = _sentences(text)
    longest = max((len(s.split()) for s in sentences), default=0)
    if longest > _MAX_SENTENCE_WORDS:
        add(LintCategory.SENTENCE_TOO_LONG, "sentence_too_long", longest - _MAX_SENTENCE_WORDS)
    floor = min(int(word_budget * 0.75), min_achievable_words if min_achievable_words else 10**9)
    if word_count > word_budget * 1.20 or word_count < floor:
        add(LintCategory.WORD_BUDGET, "word_budget", max(abs(word_count - word_budget), 1))
    if text.count("!") > _EXCLAMATION_MAX:
        add(LintCategory.PUNCTUATION_NOISE, "exclamations", text.count("!") - _EXCLAMATION_MAX)

    return LintReport(
        passed=not findings,
        findings=tuple(findings),
        word_count=word_count,
        sentence_count=len(sentences),
    )


def lint_passage(text: str, *, language: Language, min_words: int, max_words: int) -> LintReport:
    """Lint one candidate LLM passage before it may enter a script.

    Only the forbidden-content categories apply (structure and budget belong
    to the whole script). ``language`` selects the script-specific marker
    sets; the English sets always run too, because loanwords cross over.
    """
    del language
    findings: list[Finding] = []

    def add(category: LintCategory, code: str, count: int) -> None:
        if count > 0:
            findings.append(Finding(code=code, category=category, count=count))

    add(LintCategory.TRANCE_INDUCTION, "trance_induction", _scan(text, TRANCE))
    add(LintCategory.MEDICAL_CLAIM, "medical_claim", _scan(text, MEDICAL))
    add(
        LintCategory.MEDICAL_CLAIM,
        "medical_claim_negated_term",
        _scan(text, NEGATED_MEDICAL, negation_aware=True),
    )
    add(LintCategory.PROMISE, "promise", _scan(text, PROMISES))
    add(LintCategory.EYES_CLOSED, "eyes_closed_instruction", _scan(text, EYES_CLOSED))
    add(LintCategory.JARGON, "jargon", _scan(text, JARGON))

    word_count = len(text.split())
    if not (min_words <= word_count <= max_words):
        findings.append(Finding("passage_length", LintCategory.WORD_BUDGET))
    sentences = _sentences(text)
    if max((len(s.split()) for s in sentences), default=0) > _MAX_SENTENCE_WORDS:
        findings.append(Finding("sentence_too_long", LintCategory.SENTENCE_TOO_LONG))

    return LintReport(
        passed=not findings,
        findings=tuple(findings),
        word_count=word_count,
        sentence_count=len(sentences),
    )


__all__ = [
    "EYES_CLOSED",
    "JARGON",
    "MEDICAL",
    "PROMISES",
    "TRANCE",
    "Finding",
    "LintCategory",
    "LintReport",
    "lint_passage",
    "lint_script",
]
