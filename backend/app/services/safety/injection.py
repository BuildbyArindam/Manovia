"""Instruction-shaped text in places that should only ever hold *material*.

Day 29 red team (RT-I-MEM-01, RT-I-RAG-01, RT-I-KB-01). The model reads three
kinds of text it did not get from the person *in this turn*: curated reference
passages (retrieval), the rolling memory note (earlier turns, extractive), and
— at authoring time — the knowledge documents those passages come from. Each
of them can carry an instruction: a poisoned document, a sentence the person
typed last week ("ignore all previous instructions…") that would otherwise
become standing context in every later system prompt.

The person's *current* message is never filtered: it reaches the model as the
user turn, where the system prompt (``system_v3``) and the output guard handle
it, and dropping words someone actually wrote would be its own harm. This
module filters only the secondary channels, and only by dropping — a passage,
a memory sentence, a document — never by rewriting.

Deliberately simple and high-precision: imperative override phrases, role
markers, persona switches and "tell the reader to stop taking…" medication
directives. It is a tripwire, not a classifier; the system prompt and the
output guard remain the main defences (ADR 0030).
"""

from __future__ import annotations

import re
from typing import Final

from app.services.safety.normalise import strip_invisible, to_primary

#: Applied to :func:`~app.services.safety.normalise.to_primary` text: case
#: folded, apostrophes removed, other punctuation turned into spaces.
_NORMALISED_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern)
    for pattern in (
        # "ignore all previous instructions", "disregard your rules"
        # A qualifier is required: "ignore the rules my parents made" is life.
        r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+|any\s+)?"
        r"(?:of\s+)?(?:the\s+|your\s+|my\s+|these\s+|those\s+)?"
        r"(?:previous|prior|above|earlier|preceding|original|system|safety)\s+"
        r"(?:instructions|rules|prompt|guidelines|directions|guardrails|programming)\b",
        r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+)?(?:of\s+)?your\s+"
        r"(?:instructions|rules|prompt|guidelines|guardrails|programming)\b",
        r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all|any)\s+"
        r"(?:instructions|rules|guidelines)\b",
        r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:the\s+)?system\s+prompt\b",
        # "new instructions:", "system prompt", "developer mode", "jailbreak"
        r"\b(?:new|updated|real|hidden)\s+system\s+(?:instructions|prompt)\b",
        r"\b(?:developer|dan|god|jailbreak|unrestricted)\s+mode\b",
        r"\bjailbreak\w*\b",
        r"\breveal\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions)\b",
        # persona switches aimed at the model
        r"\byou\s+are\s+now\s+(?:a\s+|an\s+|my\s+)?(?:doctor|therapist|psychiatrist|"
        r"psychologist|counsellor|counselor|nurse|human|dan|unrestricted|unfiltered)\b",
        r"\b(?:act|behave|respond)\s+as\s+(?:a\s+|an\s+|my\s+)?(?:doctor|therapist|"
        r"psychiatrist|psychologist|counsellor|counselor)\b",
        # medication directives addressed to the reader / user
        r"\btell\s+(?:the\s+)?(?:user|reader|person|them)\s+(?:to\s+)?(?:stop|start|"
        r"quit|double|increase|decrease)\s+(?:taking\s+)?(?:their\s+|the\s+|all\s+)?"
        r"(?:medication|medicine|meds|pills|dose)",
        r"\b(?:recommend|prescribe)\s+\d+\s*mg\b",
    )
)

#: Applied to the raw text: chat-template role markers at a line start.
_RAW_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?im)^\s*(?:system|assistant|developer)\s*:"),
    re.compile(r"(?i)\b(?:new|updated)\s+instructions\s*:"),
    re.compile(r"(?i)<\|?(?:im_start|im_end|system)\|?>|\[/?INST\]"),
)

CODE_INJECTION_DROPPED: Final = "injection.dropped"


def looks_like_injection(text: str) -> bool:
    """True when ``text`` contains an instruction aimed at the model."""
    if not text or not text.strip():
        return False
    cleaned = strip_invisible(text)
    if any(pattern.search(cleaned) for pattern in _RAW_PATTERNS):
        return True
    normalised = to_primary(cleaned)
    return any(pattern.search(normalised) for pattern in _NORMALISED_PATTERNS)


__all__ = ["CODE_INJECTION_DROPPED", "looks_like_injection"]
