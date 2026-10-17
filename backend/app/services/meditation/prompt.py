"""Day 45: the versioned meditation passage prompt.

The file name is the version string (``meditation_passage_v1``). The loader
SHA-256 hashes it at load time so a prompt change is visible in logs and
pinned by a test, following the ``prompts/CHANGELOG.md`` convention: a new
version needs a changelog entry and a test that states the rule the change
serves.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Final

PROMPT_VERSION: Final = "meditation_passage_v1"
_REQUIRED_PLACEHOLDERS: Final = ("{language}", "{theme}", "{context}", "{word_target}")


class PromptError(ValueError):
    """The passage prompt is missing, unversioned, or lost a placeholder."""


@dataclass(frozen=True)
class PassagePrompt:
    version: str
    sha256: str
    text: str

    def render(self, *, language: str, theme: str, context: str, word_target: int) -> str:
        rendered = self.text
        for placeholder, value in (
            ("{language}", language),
            ("{theme}", theme),
            ("{context}", context),
            ("{word_target}", str(word_target)),
        ):
            rendered = rendered.replace(placeholder, value)
        if "{" in rendered and "}" in rendered:
            # A placeholder that survived the render is a content bug, not a
            # model input: the linter and the hash would both lie.
            raise PromptError(f"prompt {self.version}: unrendered placeholder remains")
        return rendered


@lru_cache(maxsize=1)
def load_passage_prompt() -> PassagePrompt:
    try:
        raw = (
            files("app.content")
            .joinpath("prompts", f"{PROMPT_VERSION}.md")
            .read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise PromptError(f"meditation passage prompt missing: {PROMPT_VERSION}.md") from exc
    for placeholder in _REQUIRED_PLACEHOLDERS:
        if placeholder not in raw:
            raise PromptError(f"meditation passage prompt lost placeholder {placeholder}")
    # The safety rules are the point of the prompt; a rewording that drops a
    # rule must fail at load, not at lint time on the first generated script.
    for rule in ("No medical claims", "No promises", "No trance", "No closed-eye"):
        if rule not in raw:
            raise PromptError(f"meditation passage prompt lost rule: {rule}")
    return PassagePrompt(
        version=PROMPT_VERSION,
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        text=raw,
    )


__all__ = ["PROMPT_VERSION", "PassagePrompt", "PromptError", "load_passage_prompt"]
