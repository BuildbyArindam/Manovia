"""Deterministic output-safety checks for whole LLM replies.

The guard runs after input crisis triage and before a generated response can be
persisted or sent. Patterns, supportive-check-in phrases and safe copy live in
``app/content/safety/output_patterns.yaml`` so a content review does not require
a Python edit. The model-facing implementation is deterministic and offline; a
judge model is intentionally not enabled.

A rejection exposes only closed category codes. It never returns matched text,
a snippet, or a hash of the reply. The orchestrator records those codes as
metadata-only ``SafetyEvent(source=llm_output)`` rows and asks the provider for
one stricter regeneration before using the local safe template.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Final, Protocol, runtime_checkable

import yaml

from app.models.enums import SafetyEventCategory
from app.services.llm.prompts import DEFAULT_MAX_WORDS
from app.services.nlp.language import detect_language, script_of
from app.services.safety.base import RiskLevel
from app.services.safety.normalise import (
    ClauseIndex,
    index_clauses,
    strip_invisible,
    to_clause_text,
    to_primary,
)

_PATTERN_FILE: Final = "safety/output_patterns.yaml"
_SUPPORTED_LOCALES: Final = frozenset({"en", "hi", "bn"})
_RULE_KEYS: Final = frozenset({"id", "category", "kind", "value", "negation_sensitive", "script"})
_TOP_KEYS: Final = frozenset(
    {"version", "safe_replies", "trimmed_closings", "check_in_phrases", "negation_cues", "rules"}
)


#: Day 29 red team (RT-I-SP-01..03): a reply that recites the system prompt.
#: Measured as *coverage*: how many words of the reply sit inside an
#: :data:`PROMPT_ECHO_WORDS`-word run copied verbatim from any shipped prompt.
#: A single run is not enough — "one trusted person, a friend, a family
#: member, a helpline" is good advice that the prompt itself contains — so
#: the reply must reuse at least :data:`PROMPT_ECHO_MIN_COVERED` words that way.
PROMPT_ECHO_WORDS: Final = 8
PROMPT_ECHO_MIN_COVERED: Final = 20
_PROMPT_DIR: Final = "prompts"
_PLACEHOLDER: Final = re.compile(r"\{[a-z_]+\}")


def load_prompt_shingles(size: int = PROMPT_ECHO_WORDS) -> frozenset[tuple[str, ...]]:
    """Every ``size``-word run of every shipped ``system_v*.md`` prompt.

    Placeholders split the text: a run never spans ``{max_words}`` or
    ``{language}``, whose substituted values are not known here.
    """
    shingles: set[tuple[str, ...]] = set()
    directory = files("app.content").joinpath(_PROMPT_DIR)
    for entry in directory.iterdir():
        if not (entry.name.startswith("system_v") and entry.name.endswith(".md")):
            continue
        for segment in _PLACEHOLDER.split(entry.read_text(encoding="utf-8")):
            tokens = to_primary(segment).split()
            shingles.update(tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1))
    return frozenset(shingles)


def echoes_prompt(
    normalized: str,
    shingles: frozenset[tuple[str, ...]],
    size: int = PROMPT_ECHO_WORDS,
    min_covered: int = PROMPT_ECHO_MIN_COVERED,
) -> bool:
    """True when at least ``min_covered`` words of the reply are verbatim prompt runs."""
    tokens = normalized.split()
    covered: set[int] = set()
    for i in range(len(tokens) - size + 1):
        if tuple(tokens[i : i + size]) in shingles:
            covered.update(range(i, i + size))
    return len(covered) >= min_covered


@dataclass(frozen=True)
class GuardVerdict:
    """The guard's decision about one model reply; it contains no prose evidence."""

    text: str
    passed: bool = True
    reason: str | None = None
    categories: tuple[SafetyEventCategory, ...] = ()


@runtime_checkable
class OutputGuard(Protocol):
    """Contract for a full-reply output safety check."""

    requires_full_text: bool

    async def check(
        self,
        text: str,
        *,
        risk: RiskLevel,
        expected_language: str | None = None,
    ) -> GuardVerdict: ...


@dataclass(frozen=True)
class _CompiledRule:
    id: str
    category: SafetyEventCategory
    pattern: re.Pattern[str]
    negation_sensitive: bool


@dataclass(frozen=True)
class _PatternSet:
    safe_replies: Mapping[str, str]
    trimmed_closings: Mapping[str, str]
    check_in_patterns: tuple[re.Pattern[str], ...]
    negation_cues: tuple[tuple[str, ...], ...]
    rules: tuple[_CompiledRule, ...]


def _compile_phrase(phrase: str, *, indic: bool = False) -> re.Pattern[str]:
    """Compile a literal phrase against the output normaliser's projection."""
    normalized = to_primary(phrase)
    if not normalized:
        raise ValueError("output phrase must not be empty")
    literal = r"\s+".join(re.escape(part) for part in normalized.split())
    if indic:
        return re.compile(literal, re.IGNORECASE)
    return re.compile(rf"(?<![a-z0-9_]){literal}(?![a-z0-9_])", re.IGNORECASE)


def _string_mapping(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != _SUPPORTED_LOCALES:
        raise ValueError(f"{field} must define exactly en, hi and bn")
    if not all(
        isinstance(key, str) and isinstance(text, str) and text.strip()
        for key, text in value.items()
    ):
        raise ValueError(f"{field} values must be non-empty strings")
    return {key: text.strip() for key, text in value.items()}


def _parse_pattern_set(payload: Any) -> _PatternSet:
    if not isinstance(payload, dict):
        raise ValueError("output pattern document must be a mapping")
    unknown = set(payload) - _TOP_KEYS
    missing = _TOP_KEYS - set(payload)
    if unknown or missing:
        raise ValueError(
            f"output pattern keys: missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if payload["version"] != 1:
        raise ValueError("unsupported output pattern version")

    safe_replies = _string_mapping(payload["safe_replies"], field="safe_replies")
    trimmed_closings = _string_mapping(payload["trimmed_closings"], field="trimmed_closings")
    phrases = payload["check_in_phrases"]
    cues = payload["negation_cues"]
    raw_rules = payload["rules"]
    if (
        not isinstance(phrases, list)
        or not phrases
        or not all(isinstance(item, str) for item in phrases)
    ):
        raise ValueError("check_in_phrases must be a non-empty list of strings")
    if not isinstance(cues, list) or not cues or not all(isinstance(item, str) for item in cues):
        raise ValueError("negation_cues must be a non-empty list of strings")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("rules must be a non-empty list")

    compiled_rules: list[_CompiledRule] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise ValueError(f"rule {index} must be a mapping")
        if set(raw) - _RULE_KEYS or not {"id", "category", "kind", "value"} <= set(raw):
            raise ValueError(f"rule {index} has missing or unknown fields")
        identifier = raw["id"]
        if not isinstance(identifier, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]*\.[a-z0-9_]+", identifier
        ):
            raise ValueError(f"rule {index} has invalid id")
        if identifier in seen_ids:
            raise ValueError(f"duplicate output rule id: {identifier}")
        seen_ids.add(identifier)
        try:
            category = SafetyEventCategory(raw["category"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"rule {identifier} has unknown category") from exc
        kind = raw["kind"]
        value = raw["value"]
        if kind not in {"phrase", "regex"} or not isinstance(value, str) or not value.strip():
            raise ValueError(f"rule {identifier} has invalid kind or value")
        indic = raw.get("script") == "indic"
        if "script" in raw and raw["script"] != "indic":
            raise ValueError(f"rule {identifier} has unknown script")
        try:
            # YAML's folded blocks add visual whitespace between regex pieces.
            # Every intended message-space is explicit as ``\\s+``; remove the
            # formatting whitespace before compiling the expression.
            expression = re.sub(r"\s+", "", value)
            pattern = (
                _compile_phrase(value, indic=indic)
                if kind == "phrase"
                else re.compile(expression, re.IGNORECASE)
            )
        except re.error as exc:
            raise ValueError(f"rule {identifier} does not compile") from exc
        negation_sensitive = raw.get("negation_sensitive", False)
        if not isinstance(negation_sensitive, bool):
            raise ValueError(f"rule {identifier} negation_sensitive must be boolean")
        compiled_rules.append(
            _CompiledRule(
                id=identifier,
                category=category,
                pattern=pattern,
                negation_sensitive=negation_sensitive,
            )
        )

    compiled_phrases = tuple(
        _compile_phrase(phrase, indic=any(not char.isascii() for char in phrase))
        for phrase in phrases
    )
    cue_tokens = tuple(
        tuple(to_primary(cue).split())
        for cue in sorted(cues, key=len, reverse=True)
        if to_primary(cue)
    )
    return _PatternSet(
        safe_replies=safe_replies,
        trimmed_closings=trimmed_closings,
        check_in_patterns=compiled_phrases,
        negation_cues=cue_tokens,
        rules=tuple(compiled_rules),
    )


def load_output_patterns() -> _PatternSet:
    """Load and compile output policy data once; malformed safety content fails closed."""
    resource = files("app.content").joinpath(_PATTERN_FILE)
    payload = yaml.safe_load(resource.read_text(encoding="utf-8"))
    return _parse_pattern_set(payload)


def _locale(language: str | None) -> str:
    code = (language or "en").split("-", maxsplit=1)[0].strip().casefold()
    return code if code in _SUPPORTED_LOCALES else "en"


def _is_negated(
    text: str,
    position: int,
    cues: tuple[tuple[str, ...], ...],
    clause_index: ClauseIndex,
) -> bool:
    clause = clause_index.clause_at(position)
    prefix = text[clause.start : position].split()
    tail = prefix[-8:]
    return any(len(cue) <= len(tail) and tuple(tail[-len(cue) :]) == cue for cue in cues)


def _language_mismatch(text: str, expected_language: str | None) -> bool:
    expected = _locale(expected_language)
    script = script_of(text)
    if expected == "en" and script in {"devanagari", "bengali"}:
        return True
    if expected == "hi" and script == "bengali":
        return True
    if expected == "bn" and script == "devanagari":
        return True

    detected = detect_language(text)
    # Romanised Hindi is intentionally accepted as Hindi: users code-switch and
    # the system prompt explicitly permits matching that register.
    if detected.lang == expected:
        return False
    if detected.lang == "other":
        # The product has no Spanish/French/other-language voice. The detector
        # reports ``other`` with high confidence for long unsupported outputs;
        # short or genuinely uncertain text is left alone to avoid false blocks.
        return len(text.split()) >= 6 and detected.confidence >= 0.80
    return detected.confidence >= 0.75


class RuleBasedOutputGuard:
    """YAML-backed deterministic output rules, evaluated before any text is released."""

    requires_full_text = True

    def __init__(self, *, max_words: int = DEFAULT_MAX_WORDS) -> None:
        if max_words < 1:
            raise ValueError("max_words must be at least 1")
        self._max_words = max_words
        self._patterns = load_output_patterns()
        self._prompt_shingles = load_prompt_shingles()

    @property
    def max_words(self) -> int:
        return self._max_words

    def safe_reply(self, language: str | None = None) -> str:
        """The pre-written local reply for a second guard failure or a guard outage."""
        return self._patterns.safe_replies[_locale(language)]

    async def check(
        self,
        text: str,
        *,
        risk: RiskLevel,
        expected_language: str | None = None,
    ) -> GuardVerdict:
        """Return the input only when it satisfies every deterministic policy."""
        cleaned = strip_invisible(text)
        normalized = to_primary(cleaned)
        clause_index = index_clauses(normalized, marked=to_clause_text(cleaned))
        categories: list[SafetyEventCategory] = []

        def add(category: SafetyEventCategory) -> None:
            if category not in categories:
                categories.append(category)

        for rule in self._patterns.rules:
            match = rule.pattern.search(normalized)
            if match is not None and not (
                rule.negation_sensitive
                and _is_negated(
                    normalized,
                    match.start(),
                    self._patterns.negation_cues,
                    clause_index,
                )
            ):
                add(rule.category)

        if echoes_prompt(normalized, self._prompt_shingles):
            add(SafetyEventCategory.PROMPT_LEAK)
        if len(normalized.split()) > self._max_words:
            add(SafetyEventCategory.TOO_LONG)
        if expected_language is not None and _language_mismatch(cleaned, expected_language):
            add(SafetyEventCategory.WRONG_LANGUAGE)
        if risk >= RiskLevel.MEDIUM and not any(
            pattern.search(normalized) for pattern in self._patterns.check_in_patterns
        ):
            add(SafetyEventCategory.MISSING_CHECK_IN)

        if not categories:
            return GuardVerdict(text=text, passed=True)
        category = categories[0]
        return GuardVerdict(
            text=self.safe_reply(expected_language),
            passed=False,
            reason=category.value,
            categories=tuple(categories),
        )


__all__ = [
    "PROMPT_ECHO_MIN_COVERED",
    "PROMPT_ECHO_WORDS",
    "RuleBasedOutputGuard",
    "echoes_prompt",
    "load_output_patterns",
    "load_prompt_shingles",
]
