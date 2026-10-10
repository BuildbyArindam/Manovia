"""Versioned, reviewable system prompts loaded from ``app/content/prompts/``.

The prompt is product surface, not source code: it is the thing that decides
how the companion speaks to somebody at 2 a.m. So it lives in a Markdown file a
non-engineer can read and review, it is versioned by filename, it is hashed
when loaded, and it is validated against the safety rules it is supposed to
carry.

Three things are enforced at load time, and every one of them is a startup
failure rather than a silent change:

* **placeholders** — only ``{max_words}`` and ``{language}`` exist. A stray
  ``{user_name}`` is refused, because a template that can interpolate arbitrary
  fields is a template that can interpolate user text into instructions.
* **required rules** — :data:`REQUIRED_RULES` names the rules from the Day 10
  brief. A prompt that stops saying "you are an AI" does not load.
* **no missing version** — asking for a version with no file is an error, not a
  silent fall back to whatever happens to exist.

Requests never override the prompt. ``render_system_prompt`` takes only the two
knobs the file asks for, and both are bounded.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from importlib.resources import files
from typing import Final

from pydantic import BaseModel, Field

#: Directory inside ``app/content`` holding one Markdown file per version.
PROMPTS_DIR: Final = "prompts"

#: The version the product ships with today. Changing the prompt means adding a
#: file and bumping this (or setting ``LLM_PROMPT_VERSION``); see
#: ``app/content/prompts/CHANGELOG.md``.
PROMPT_VERSION: Final = "system_v1"

#: Reply-length budget named by the ``{max_words}`` placeholder (Day 10 brief:
#: "warm and brief (max ~120 words by default)").
DEFAULT_MAX_WORDS: Final = 120
MIN_MAX_WORDS: Final = 30
MAX_MAX_WORDS: Final = 400

#: Language codes the prompt knows how to name, mapped to the name it uses.
#: Anything else — including a failed or non-committal detection — renders as
#: :data:`UNKNOWN_LANGUAGE_PHRASE`, because telling a model "reply in other" is
#: worse than telling it nothing: "other" is not a language, and the instruction
#: would be noise where the model's own reading of the text is better.
LANGUAGE_NAMES: Final[dict[str, str]] = {"en": "English", "hi": "Hindi", "bn": "Bengali"}

#: What ``{language}`` becomes when the language is not known.
UNKNOWN_LANGUAGE_PHRASE: Final = "the language they wrote in"

#: The code recorded when detection could not name a language.
UNDETERMINED: Final = "und"

DEFAULT_LANGUAGE: Final = UNDETERMINED

#: The only substitutions a prompt file may use. Mirrors the whitelist in
#: ``app.content.i18n``, for the same reason: a typo must not become a hole.
_ALLOWED_PLACEHOLDERS: Final[frozenset[str]] = frozenset({"max_words", "language"})
_PLACEHOLDER_RE: Final = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

#: Rules every system prompt must carry, as ``(rule id, regex)``. The regexes
#: are deliberately loose — they catch "somebody deleted a paragraph", not
#: stylistic drift — and each one is asserted by ``tests/llm/test_prompts.py``
#: with the same id, so the changelog table and the test suite cannot disagree.
REQUIRED_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("discloses_ai", r"(?i)\b(you are|i am|i'm)\b[^.\n]*\bai\b"),
    ("brief", r"(?i)\{max_words\}\s*words"),
    ("validates_without_toxic_positivity", r"(?i)validate the feeling"),
    ("at_most_one_question", r"(?i)at most one .*question"),
    ("no_diagnosis", r"(?i)never diagnose"),
    ("no_medication", r"(?i)never discuss medication"),
    ("no_self_harm_instructions", r"(?i)never give instructions"),
    ("encourages_professional_help", r"(?i)professional help and one trusted person"),
    (
        "declines_roleplay",
        r"(?i)never (claim to be|pretend to be) (a )?therapist|role-?play as one",
    ),
    ("responds_in_user_language", r"(?i)reply in \{language\}"),
)


class PromptError(RuntimeError):
    """A prompt file is missing, malformed, or missing a required rule."""


class PromptDocument(BaseModel):
    """One versioned prompt file."""

    version: str = Field(min_length=1)
    text: str = Field(min_length=1)
    sha256: str
    #: Placeholders the file actually uses (a subset of the whitelist).
    placeholders: tuple[str, ...] = ()
    #: Rules from :data:`REQUIRED_RULES` the file was found to carry.
    rules_present: tuple[str, ...] = ()

    @property
    def short_sha(self) -> str:
        return self.sha256[:12]


class SystemPrompt(BaseModel):
    """A rendered system prompt, ready to hand to a provider."""

    version: str
    language: str
    max_words: int
    text: str
    sha256: str


def prompt_text(version: str = PROMPT_VERSION) -> str:
    """Read the raw Markdown for ``version``. Raises :class:`PromptError`."""
    try:
        resource = files("app.content").joinpath(f"{PROMPTS_DIR}/{version}.md")
        raw = resource.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, TypeError) as exc:
        raise PromptError(f"no prompt file for version {version!r}") from exc
    return raw.decode("utf-8")


def available_versions() -> tuple[str, ...]:
    """Every prompt version shipped in the package, sorted."""
    directory = files("app.content").joinpath(PROMPTS_DIR)
    try:
        entries = list(directory.iterdir())
    except (FileNotFoundError, ModuleNotFoundError, TypeError):
        return ()
    names = sorted(
        entry.name[: -len(".md")]
        for entry in entries
        if entry.is_file() and entry.name.endswith(".md")
    )
    return tuple(names)


def _check_placeholders(version: str, text: str) -> tuple[str, ...]:
    found = set(_PLACEHOLDER_RE.findall(text))
    unknown = sorted(found - _ALLOWED_PLACEHOLDERS)
    if unknown:
        raise PromptError(
            f"prompt {version!r} uses unknown placeholders {unknown}; "
            f"allowed: {sorted(_ALLOWED_PLACEHOLDERS)}"
        )
    return tuple(sorted(found))


def _check_rules(version: str, text: str) -> tuple[str, ...]:
    # Match against a whitespace-flattened copy: the prompt files are wrapped
    # at 76 columns for reviewability, so a rule can be split across lines and
    # still be present.
    flattened = re.sub(r"\s+", " ", text)
    missing = [
        rule_id for rule_id, pattern in REQUIRED_RULES if re.search(pattern, flattened) is None
    ]
    if missing:
        raise PromptError(
            f"prompt {version!r} is missing required safety rules: {missing}. "
            "See app/content/prompts/CHANGELOG.md for what each rule is for."
        )
    return tuple(rule_id for rule_id, _ in REQUIRED_RULES)


def strip_header(text: str) -> str:
    """Drop the leading ``>`` blockquote block — the note for humans.

    Each prompt file opens with a Markdown blockquote explaining the versioning
    rules. That note is for a reviewer reading the repository, not for the
    model: sending it would spend tokens on meta-instructions and, worse, the
    note itself names the ``{placeholders}``, which would then be substituted
    into the prompt as literal values.
    """
    lines = text.splitlines()
    index = 0
    while index < len(lines) and lines[index].lstrip().startswith(">"):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "\n".join(lines[index:])


@lru_cache(maxsize=8)
def load_prompt(version: str = PROMPT_VERSION) -> PromptDocument:
    """Load and validate a prompt file. Cached; validation runs once per version."""
    # Validate the *whole* file (header included) so a required rule cannot
    # hide in a comment, then keep only the prompt for rendering.
    text = prompt_text(version)
    rules = _check_rules(version, text)
    text = strip_header(text)
    placeholders = _check_placeholders(version, text)
    return PromptDocument(
        version=version,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        placeholders=placeholders,
        rules_present=rules,
    )


def language_phrase(language: str | None) -> str:
    """What ``{language}`` becomes in the rendered prompt."""
    code = (language or "").strip().casefold()
    if not code or code in {"other", UNDETERMINED}:
        return UNKNOWN_LANGUAGE_PHRASE
    return LANGUAGE_NAMES.get(code.split("-", maxsplit=1)[0], UNKNOWN_LANGUAGE_PHRASE)


def render_system_prompt(
    *,
    version: str = PROMPT_VERSION,
    language: str | None = None,
    max_words: int = DEFAULT_MAX_WORDS,
) -> SystemPrompt:
    """Render the system prompt for one request.

    ``language`` is a detected code (``en`` / ``hi`` / ``bn`` / ``other``), not
    free text: an unknown or absent code renders as
    :data:`UNKNOWN_LANGUAGE_PHRASE` rather than being interpolated, so a caller
    can never smuggle instructions into the system prompt through it.
    """
    document = load_prompt(version)
    words = max(MIN_MAX_WORDS, min(MAX_MAX_WORDS, int(max_words)))
    code = (language or "").strip().casefold().split("-", maxsplit=1)[0] or UNDETERMINED
    rendered = document.text.format(max_words=words, language=language_phrase(code))
    return SystemPrompt(
        version=document.version,
        language=code,
        max_words=words,
        text=rendered,
        sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    )


def describe_prompt(version: str = PROMPT_VERSION) -> dict[str, object]:
    """Metadata for health/telemetry output. The prompt text itself is not metadata."""
    document = load_prompt(version)
    return {
        "version": document.version,
        "sha256": document.short_sha,
        "characters": len(document.text),
        "placeholders": list(document.placeholders),
        "rules_present": list(document.rules_present),
    }
