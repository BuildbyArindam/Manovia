"""Load, validate and compile the safety pattern data files.

The vocabulary lives in ``app/content/safety/patterns_*.yaml`` — never in code —
because the people who must be able to fix a missed phrase (a clinician, a
native speaker, a support engineer) should not have to read Python, and because a
content change must not look like a code change in review.

What this module adds on top of YAML:

* **compilation once**. Every regex is compiled at load, and a broken one is a
  startup error rather than a silent miss at the moment somebody needs it.
* **shape checks the YAML cannot make**: unique dotted ids, a known category and
  level, values written for *normalised* text (so a capital letter or a comma in
  a pattern is caught here instead of never matching anything), and a phrase that
  is ASCII matched on word boundaries while a phrase in Indic script is matched
  as a substring (combining vowel signs make ``\\b`` unreliable there).

The loader raises ``ValueError`` with the offending ids. A safety pattern file
that does not hold together must stop the app, exactly like a broken helpline
file must.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, Final, Literal

import yaml

from app.services.safety.base import RiskCategory, RiskLevel

#: Where the pattern files live, relative to ``app/content``.
PATTERN_DIR: Final = "safety"

#: Loaded in this order. ``patterns_context.yaml`` carries pragmatics (negation,
#: third-person markers, benign frames); the other three carry risk vocabulary.
PATTERN_FILES: Final[tuple[str, ...]] = (
    "patterns_context.yaml",
    "patterns_core.yaml",
    "patterns_euphemisms.yaml",
    "patterns_indic.yaml",
)

CONTEXT_FILE: Final = "patterns_context.yaml"

Kind = Literal["phrase", "regex"]
Spelling = Literal["fused", "obfuscated"]

#: A level a *pattern* may carry. ``none`` is not one of them: a pattern that
#: matches has found something.
_PATTERN_LEVELS: Final[frozenset[str]] = frozenset({"low", "medium", "high", "imminent"})

_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_LANGUAGE_RE: Final = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)*$")
_SCRIPTS: Final[frozenset[str]] = frozenset({"devanagari", "bengali", "latin"})
_UPPERCASE_RE: Final = re.compile(r"[A-Z]")
#: Punctuation that must not appear in a pattern value: normalisation replaces it
#: with a space, so a value containing it could never match.
_FORBIDDEN_IN_VALUE: Final = frozenset(",.!?;:'\"(){}[]<>")

_RULE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "category",
        "level",
        "kind",
        "value",
        "negation_sensitive",
        "negation_baked",
        "figurative_prone",
        "figurative_subject",
        "language",
        "script",
        "spelling",
        "note",
    }
)

_CONTEXT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "version",
        "leet_map",
        "misspellings",
        "negation_cues",
        "negation_after_cues",
        "transparent_words",
        "third_person_markers",
        "third_person_patterns",
        "fiction_frames",
        "relation_possessives",
        "relation_nouns",
        "first_person_markers",
        "timeframe_markers",
        "benign_frames",
        "benign_subjects",
    }
)


@dataclass(frozen=True)
class RulePattern:
    """One compiled detection rule."""

    id: str
    category: RiskCategory
    level: RiskLevel
    kind: Kind
    #: The value exactly as written in the YAML (normalised-form text).
    value: str
    #: Compiled matcher for the primary and leet variants.
    pattern: re.Pattern[str]
    #: Compiled matcher for the whitespace-stripped variant (phrases only):
    #: catches the fused trick, ``wanttodie``.
    squashed: re.Pattern[str] | None
    #: Compiled matcher for the repeated-letter-collapsed variant (phrases only):
    #: ``diiiie`` and ``kiiiill`` both collapse onto the same letters as the
    #: collapsed pattern value, so the trick is caught without breaking real
    #: double letters elsewhere.
    collapsed: re.Pattern[str] | None
    negation_sensitive: bool
    #: True when the rule's own pattern requires a negation cue, because the
    #: negated phrase *is* the risk: Hindi "jeena nahi chahta" ("I do not want to
    #: live") and Bengali "bachte chai na" describe ideation, not a refusal of it.
    #: Such rules are exempt from the inside-the-hit negation check, which would
    #: otherwise read their own required cue as a cancellation and invert them.
    #: Declared in the data rather than derived from the pattern text: a pattern
    #: may carry an *optional* cue (``marna (nahi)? chahta``), where the cue really
    #: does cancel, and no reading of the source can tell the two apart.
    negation_baked: bool
    figurative_prone: bool
    #: True when an inanimate subject immediately before the hit ("this
    #: **traffic** is killing me") is positive evidence of exaggeration. Opt-in
    #: and narrower than :attr:`figurative_prone`: only patterns whose figurative
    #: use is marked by their subject carry it.
    figurative_subject: bool
    #: ``hi``/``bn``/… or ``None`` for English. Enables post-verbal negation.
    language: str | None
    script: str | None
    spelling: Spelling | None
    note: str | None


@dataclass(frozen=True)
class ContextData:
    """The pragmatics vocabulary, compiled."""

    leet_map: Mapping[str, str]
    #: Compiled word-level spelling fixes, longest source first so that
    #: ``suicidalthoughts`` is not partly rewritten by ``sucide``.
    misspellings: tuple[tuple[re.Pattern[str], str], ...]
    negation_cues: frozenset[str]
    negation_after_cues: frozenset[str]
    transparent_words: frozenset[str]
    third_person_markers: tuple[str, ...]
    third_person_patterns: tuple[re.Pattern[str], ...]
    #: The two halves of a possessive-relative phrase ("my" + "friend"), kept
    #: verbatim for ``describe()`` and for tests.
    relation_possessives: tuple[str, ...]
    relation_nouns: tuple[str, ...]
    #: Frames that say the text is fiction or a report ("in a movie", "for my
    #: novel", "hypothetically"). Stronger than the person markers: these can
    #: appear anywhere in the message.
    fiction_frames: tuple[str, ...]
    first_person_markers: tuple[str, ...]
    timeframe_markers: tuple[str, ...]
    benign_frames: tuple[re.Pattern[str], ...]
    benign_subjects: frozenset[str]
    #: Pre-compiled alternations of the marker lists above, so a message is
    #: scanned once per flag instead of once per marker.
    third_person_marker_pattern: re.Pattern[str]
    fiction_frames_pattern: re.Pattern[str]
    first_person_pattern: re.Pattern[str]
    #: Possessive-relative phrases ("my friend", "the kids"). Blank these out
    #: before asking whether a clause has a first-person subject.
    relation_pattern: re.Pattern[str]
    timeframe_pattern: re.Pattern[str]
    benign_subject_pattern: re.Pattern[str]


@dataclass(frozen=True)
class PatternSet:
    """Everything the engine needs: rules plus pragmatics, plus provenance."""

    rules: tuple[RulePattern, ...]
    context: ContextData
    #: File names the set was built from, for logs and ``describe()``.
    sources: tuple[str, ...]

    def describe(self) -> dict[str, Any]:
        """Counts only — enough for a health endpoint, useless for reading text."""
        by_level: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_language: dict[str, int] = {}
        for rule in self.rules:
            by_level[rule.level.label] = by_level.get(rule.level.label, 0) + 1
            by_category[rule.category.value] = by_category.get(rule.category.value, 0) + 1
            key = rule.language or "en"
            by_language[key] = by_language.get(key, 0) + 1
        return {
            "rules": len(self.rules),
            "sources": list(self.sources),
            "by_level": dict(sorted(by_level.items())),
            "by_category": dict(sorted(by_category.items())),
            "by_language": dict(sorted(by_language.items())),
            "negation_cues": len(self.context.negation_cues),
            "third_person_markers": len(self.context.third_person_markers),
            "fiction_frames": len(self.context.fiction_frames),
            "relation_nouns": len(self.context.relation_nouns),
            "benign_frames": len(self.context.benign_frames),
            "benign_subjects": len(self.context.benign_subjects),
            "misspellings": len(self.context.misspellings),
        }


def collapse_repeats(value: str) -> str:
    """Collapse runs of two or more identical letters to one (``diiiie`` -> ``die``).

    Real double letters collapse too (``kill`` -> ``kil``), which is exactly why
    this is applied to the *pattern* as well as to the text: both sides are
    mangled identically, so the comparison stays honest.
    """
    return re.sub(r"(\w)\1+", r"\1", value)


def _compile_phrase(
    value: str,
) -> tuple[re.Pattern[str], re.Pattern[str] | None, re.Pattern[str] | None]:
    """Compile a phrase value for normalised text.

    ASCII phrases match on word boundaries with flexible internal whitespace, so
    ``want to die`` also matches ``want  to   die``. Non-ASCII phrases (Devanagari,
    Bengali script) match as substrings: those scripts write vowels as combining
    marks, and a boundary assertion in the middle of a word would never fire.

    The second return value is the same phrase with spaces removed, matched
    against the whitespace-stripped variant — that is what catches
    ``wanttodie`` and ``k i l l  m y s e l f``.
    """
    if value.isascii():
        body = re.escape(value).replace("\\ ", r"\s+")
        pattern = re.compile(rf"(?<![\w]){body}(?![\w])")
        fused = re.sub(r"\s+", "", value)
        squashed = re.compile(re.escape(fused)) if fused != value else None
    else:
        pattern, squashed = re.compile(re.escape(value)), None
    collapsed_value = collapse_repeats(value)
    collapsed = re.compile(re.escape(collapsed_value)) if collapsed_value != value else None
    return pattern, squashed, collapsed


def _validate_rule(raw: Mapping[str, Any], source: str) -> RulePattern:
    """Validate and compile one rule entry. Raises ``ValueError`` naming the id."""
    unknown = sorted(set(raw) - _RULE_KEYS)
    if unknown:
        raise ValueError(f"{source}: rule has unknown keys {unknown}")

    rule_id = raw.get("id")
    if not isinstance(rule_id, str) or not _ID_RE.match(rule_id):
        raise ValueError(f"{source}: rule id {rule_id!r} must look like 'group.snake_case'")

    category_raw = raw.get("category")
    if not isinstance(category_raw, str) or category_raw not in set(RiskCategory):
        raise ValueError(f"{source}: {rule_id} has unknown category {category_raw!r}")

    level_raw = raw.get("level")
    if not isinstance(level_raw, str) or level_raw not in _PATTERN_LEVELS:
        raise ValueError(f"{source}: {rule_id} has unknown level {level_raw!r}")

    kind = raw.get("kind")
    if kind not in ("phrase", "regex"):
        raise ValueError(f"{source}: {rule_id} has unknown kind {kind!r}")

    value = raw.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: {rule_id} has an empty value")
    if _UPPERCASE_RE.search(value):
        raise ValueError(f"{source}: {rule_id} must be written in lowercase (text is normalised)")
    if kind == "phrase" and (set(value) & _FORBIDDEN_IN_VALUE):
        raise ValueError(
            f"{source}: {rule_id} is a phrase containing punctuation that normalisation removes"
        )

    language = raw.get("language")
    if language is not None:
        if not isinstance(language, str) or not _LANGUAGE_RE.match(language):
            raise ValueError(f"{source}: {rule_id} has an invalid language {language!r}")
        language = language.strip().lower()

    script = raw.get("script")
    if script is not None and script not in _SCRIPTS:
        raise ValueError(f"{source}: {rule_id} has an unknown script {script!r}")

    spelling = raw.get("spelling")
    if spelling is not None and spelling not in ("fused", "obfuscated"):
        raise ValueError(f"{source}: {rule_id} has an unknown spelling tag {spelling!r}")

    try:
        if kind == "phrase":
            pattern, squashed, collapsed = _compile_phrase(value)
        else:
            pattern, squashed, collapsed = re.compile(value), None, None
    except re.error as exc:
        raise ValueError(f"{source}: {rule_id} is not a valid pattern: {exc}") from exc

    return RulePattern(
        id=rule_id,
        category=RiskCategory(category_raw),
        level=RiskLevel[level_raw.upper()],
        kind=kind,
        value=value,
        pattern=pattern,
        squashed=squashed,
        collapsed=collapsed,
        negation_sensitive=bool(raw.get("negation_sensitive", False)),
        negation_baked=bool(raw.get("negation_baked", False)),
        figurative_prone=bool(raw.get("figurative_prone", False)),
        figurative_subject=bool(raw.get("figurative_subject", False)),
        language=language,
        script=script,
        spelling=spelling,
        note=raw.get("note"),
    )


def _compile_context(raw: Mapping[str, Any], source: str) -> ContextData:
    """Validate and compile the pragmatics vocabulary."""
    unknown = sorted(set(raw) - _CONTEXT_KEYS)
    if unknown:
        raise ValueError(f"{source}: unknown context keys {unknown}")

    def strings(key: str) -> list[str]:
        items = raw.get(key) or []
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise ValueError(f"{source}: {key} must be a list of strings")
        return [item for item in items]

    def regexes(key: str) -> tuple[re.Pattern[str], ...]:
        compiled: list[re.Pattern[str]] = []
        for item in strings(key):
            try:
                compiled.append(re.compile(item))
            except re.error as exc:
                raise ValueError(f"{source}: {key} entry is not a valid regex: {exc}") from exc
        return tuple(compiled)

    leet_raw = raw.get("leet_map") or {}
    if not isinstance(leet_raw, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in leet_raw.items()
    ):
        raise ValueError(f"{source}: leet_map must map single characters to strings")

    misspellings_raw = raw.get("misspellings") or {}
    if not isinstance(misspellings_raw, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in misspellings_raw.items()
    ):
        raise ValueError(f"{source}: misspellings must map words to words")
    # Longest source first, so a long fused form is fixed before a short one can
    # rewrite part of it.
    misspellings = tuple(
        (re.compile(rf"(?<![\w]){re.escape(key)}(?![\w])"), value)
        for key, value in sorted(misspellings_raw.items(), key=lambda item: -len(item[0]))
    )

    markers = strings("third_person_markers")
    fiction = strings("fiction_frames")
    possessives = strings("relation_possessives")
    relations = strings("relation_nouns")
    first_person = strings("first_person_markers")
    timeframes = strings("timeframe_markers")
    subjects = strings("benign_subjects")
    return ContextData(
        leet_map={key: value for key, value in leet_raw.items()},
        misspellings=misspellings,
        negation_cues=frozenset(strings("negation_cues")),
        negation_after_cues=frozenset(strings("negation_after_cues")),
        transparent_words=frozenset(strings("transparent_words")),
        third_person_markers=tuple(markers),
        third_person_patterns=regexes("third_person_patterns"),
        fiction_frames=tuple(fiction),
        relation_possessives=tuple(possessives),
        relation_nouns=tuple(relations),
        first_person_markers=tuple(first_person),
        timeframe_markers=tuple(timeframes),
        benign_frames=regexes("benign_frames"),
        benign_subjects=frozenset(subjects),
        third_person_marker_pattern=_marker_pattern(markers),
        fiction_frames_pattern=_marker_pattern(fiction),
        first_person_pattern=_marker_pattern(first_person),
        relation_pattern=_relation_pattern(possessives, relations),
        timeframe_pattern=_marker_pattern(timeframes),
        benign_subject_pattern=_marker_pattern(subjects),
    )


def _marker_pattern(markers: Iterable[str]) -> re.Pattern[str]:
    """One alternation over a marker list, longest first, on word boundaries.

    Longest-first matters: ``my best friend`` must win over ``my`` so the whole
    marker is what matched, and word boundaries keep ``i`` from matching inside
    every word that contains one.
    """
    ordered = sorted(
        {marker.strip().lower() for marker in markers if marker.strip()}, key=len, reverse=True
    )
    if not ordered:
        return re.compile(r"(?!)")  # never matches
    body = "|".join(re.escape(marker).replace(r"\ ", r"\s+") for marker in ordered)
    # The body must be grouped: without (?:...) the lookbehind would bind to the
    # first alternative only and the lookahead to the last, and `ill` would match
    # inside "pills".
    return re.compile(rf"(?<![\w])(?:{body})(?![\w])")


def _relation_pattern(possessives: Iterable[str], nouns: Iterable[str]) -> re.Pattern[str]:
    """Compile ``<possessive> <relation noun>`` into one strippable phrase.

    "my friend" starts with a first-person word but is not about the speaker, so
    the engine blanks these phrases before looking for a first-person subject.
    Longest noun first, so ``best friend`` wins over ``friend``.
    """
    ordered_possessives = sorted(
        {item.strip().lower() for item in possessives if item.strip()}, key=len, reverse=True
    )
    ordered_nouns = sorted(
        {item.strip().lower() for item in nouns if item.strip()}, key=len, reverse=True
    )
    if not ordered_possessives or not ordered_nouns:
        return re.compile(r"(?!)")  # never matches
    left = "|".join(re.escape(item).replace(r"\ ", r"\s+") for item in ordered_possessives)
    right = "|".join(re.escape(item).replace(r"\ ", r"\s+") for item in ordered_nouns)
    return re.compile(rf"(?<![\w])(?:{left})\s+(?:{right})(?![\w])")


def parse_pattern_set(payloads: Mapping[str, Mapping[str, Any]]) -> PatternSet:
    """Build a :class:`PatternSet` from already-parsed YAML documents.

    ``payloads`` maps a file name to its document, so a test can build a tiny
    fake set without touching the shipped content.
    """
    context_payload = payloads.get(CONTEXT_FILE)
    if context_payload is None:
        raise ValueError(f"the pattern set needs {CONTEXT_FILE}")

    rules: list[RulePattern] = []
    sources: list[str] = []
    for name, payload in payloads.items():
        if not isinstance(payload, dict):
            raise ValueError(f"{name}: expected a mapping at the top level")
        sources.append(name)
        if name == CONTEXT_FILE:
            continue
        entries = payload.get("rules") or []
        if not isinstance(entries, list):
            raise ValueError(f"{name}: 'rules' must be a list")
        rules.extend(_validate_rule(entry, name) for entry in entries)

    ids = [rule.id for rule in rules]
    duplicates = sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})
    if duplicates:
        raise ValueError(f"pattern ids must be unique; duplicated: {duplicates}")
    if not rules:
        raise ValueError("the pattern set contains no rules — refusing to run a blind engine")

    return PatternSet(
        rules=tuple(rules),
        context=_compile_context(context_payload, CONTEXT_FILE),
        sources=tuple(sources),
    )


def _load_yaml(name: str) -> Mapping[str, Any]:
    raw = files("app.content").joinpath(f"{PATTERN_DIR}/{name}").read_text(encoding="utf-8")
    document = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise ValueError(f"{name}: expected a mapping at the top level")
    return document


@lru_cache(maxsize=1)
def load_patterns(*, extra_files: tuple[str, ...] = ()) -> PatternSet:
    """Load and compile the shipped pattern files (cached; they never change).

    ``extra_files`` exists so a deployment can add a local vocabulary file
    without editing the package; it is empty everywhere today.
    """
    names = [*PATTERN_FILES, *extra_files]
    return parse_pattern_set({name: _load_yaml(name) for name in names})
