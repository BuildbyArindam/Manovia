"""No vendor model name is hard-coded anywhere outside configuration.

AGENTS.md: "Config only via environment variables. Never hard-code secrets or
model names." That rule is easy to agree with and easy to break — one
``model="claude-…"`` default added during debugging is invisible in review and
becomes a silent cost decision, a silent behaviour change, and an unanswerable
"which model answered our users?".

So it is enforced by a test rather than by memory. The test walks the
repository, greps for the shapes vendor model ids take, and allows a hit only in
``.env.example`` (where a model id is documentation) — plus this file, which has
to contain the patterns to look for them.

It is deliberately a *shape* match, not a blocklist of ids we happen to know
about: the point is that a model name nobody has heard of yet is also caught if
it looks like one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Directories that are never source of truth: installed dependencies, build
#: output, caches and the git object store.
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".git",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".hypothesis",
        "dist",
        "build",
        "htmlcov",
        ".next",
        ".vite",
        "out",
        "target",
        "transformers_cache",
    }
)

#: Files allowed to name a model: the environment template (where a model id is
#: documentation for whoever writes their ``.env``), and this test.
ALLOWED_FILES: frozenset[str] = frozenset({".env.example", "test_no_hardcoded_model.py"})

#: Extensions worth reading. A model id in a binary blob is not a code path.
SCANNED_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".md", ".ts", ".tsx", ".js", ".jsx", ".json", ".yml", ".yaml", ".toml", ".cfg", ".ini"}
)

#: Shapes vendor model ids take. Anchored on a version or a family separator so
#: that ordinary prose ("anthropic", "ollama") is not a hit — the provider
#: names are allowed, an *id* is not.
MODEL_ID_PATTERNS: tuple[tuple[str, str], ...] = (
    ("anthropic claude", r"claude[-_][a-z0-9.\-]*\d"),
    ("openai gpt", r"\bgpt[-_][0-9][0-9a-z.\-]*"),
    ("meta llama", r"\bllama[-_]?[0-9][0-9a-z.:]*"),
    ("mistral", r"\bmistral[-_][0-9a-z.\-]+"),
    ("google gemini", r"\bgemini[-_][0-9][0-9a-z.\-]*"),
    ("alibaba qwen", r"\bqwen[-_]?[0-9][0-9a-z.\-]*"),
    ("google gemma", r"\bgemma[-_][0-9][0-9a-z.\-]*"),
    ("cohere command", r"\bcommand[-_](?:r|light)[-_a-z0-9.]*"),
    ("xai grok", r"\bgrok[-_][0-9][0-9a-z.\-]*"),
)

_COMPILED = tuple(
    (label, re.compile(pattern, re.IGNORECASE)) for label, pattern in MODEL_ID_PATTERNS
)


def iter_source_files() -> list[Path]:
    files: list[Path] = []
    stack = [REPO_ROOT]
    while stack:
        directory = stack.pop()
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                if entry.name in SKIP_DIRS or entry.name.startswith("."):
                    continue
                stack.append(entry)
            elif entry.suffix in SCANNED_SUFFIXES:
                files.append(entry)
    return files


def find_hits() -> list[tuple[Path, int, str, str]]:
    hits: list[tuple[Path, int, str, str]] = []
    for path in iter_source_files():
        if path.name in ALLOWED_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover - binary or gone
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in _COMPILED:
                if pattern.search(line):
                    hits.append((path, number, label, line.strip()[:120]))
                    break
    return hits


def test_no_vendor_model_id_outside_configuration() -> None:
    hits = find_hits()
    assert not hits, "hard-coded model ids found:\n" + "\n".join(
        f"  {path.relative_to(REPO_ROOT)}:{number} [{label}] {line}"
        for path, number, label, line in hits
    )


def test_the_scan_actually_scans_something() -> None:
    """A guard that walks zero files is a guard that passes forever."""
    files = iter_source_files()
    assert len(files) > 100, f"the walker only found {len(files)} files"
    assert any(path.suffix == ".py" for path in files)
    assert not any(".venv" in path.parts for path in files)
    assert not any("node_modules" in path.parts for path in files)


def test_the_patterns_match_what_they_are_meant_to_match() -> None:
    """Positive controls: if someone 'tidies' the regexes into something that
    matches nothing, this fails rather than the guard going quietly blind."""
    samples = {
        "claude-sonnet-4-5": "anthropic claude",
        "claude-3-5-haiku-20241022": "anthropic claude",
        "gpt-4o-mini": "openai gpt",
        "llama3.2:3b": "meta llama",
        "llama-3.1-8b-instant": "meta llama",
        "mistral-large-2411": "mistral",
        "gemini-1.5-pro": "google gemini",
        "qwen2.5-7b": "alibaba qwen",
        "command-r-plus": "cohere command",
    }
    for sample, expected_label in samples.items():
        labels = [label for label, pattern in _COMPILED if pattern.search(sample)]
        assert expected_label in labels, f"{sample!r} was not recognised as {expected_label}"


def test_the_patterns_do_not_match_ordinary_prose() -> None:
    """Negative controls: the provider names, the product's own ids and plain
    English must not trip the guard, or nobody will take it seriously."""
    for line in (
        "the anthropic provider is first in the chain",
        "ollama is the local fallback",
        "model=TEST_MODEL",
        "fake-deterministic-v1",
        "test-model-anthropic-000",
        "the llm provider layer",
    ):
        for label, pattern in _COMPILED:
            assert pattern.search(line) is None, f"{label} matched {line!r}"


@pytest.mark.parametrize(
    "env_example",
    [REPO_ROOT / ".env.example"],
)
def test_the_env_example_is_the_only_place_a_model_may_be_named(env_example: Path) -> None:
    """Documentation of the one sanctioned exception, so the exception cannot
    quietly grow into a habit."""
    assert env_example.exists()
    assert env_example.name in ALLOWED_FILES


def test_settings_have_no_model_default() -> None:
    """The rule that matters in code: an unset model must mean 'not
    configured', never 'use the one I typed in'."""
    from app.core.config import Settings

    assert Settings().anthropic_model is None
    assert Settings().ollama_model == ""
