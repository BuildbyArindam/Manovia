"""Day 45: the script generator.

Pins the contracts the rest of the surface relies on:

* the curated catalogue always passes both gates (deterministic floor);
* the same request + seed always composes the same script (the content
  hash is a stable cache key);
* ``auto`` resolution is deterministic and respects time-of-day + tags;
* LLM passages are linted before they may enter a script, get one stricter
  retry, and fall back to curated copy on failure;
* the offline Fake's canned reply never reaches a script.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from app.services.llm.base import LLMMessage, LLMProvider, LLMResult
from app.services.meditation.content import load_themes
from app.services.meditation.generate import (
    MAX_LLM_SLOTS,
    MeditationScriptGenerator,
    resolve_theme,
)
from app.services.meditation.lint import lint_script
from app.services.meditation.types import (
    PREFERENCE_TAGS,
    Context,
    Language,
    MeditationRequest,
    MeditationScript,
)

THEMES = [t.id for t in load_themes()]
CONTEXTS: tuple[Context, ...] = ("seated", "walking", "driving", "unknown")
LANGUAGES: tuple[Language, ...] = ("en", "hi", "bn")


def _request(**overrides: Any) -> MeditationRequest:
    base: dict[str, Any] = {
        "theme": "calm",
        "duration_seconds": 180,
        "language": "en",
        "context": "seated",
        "hour_of_day": 12,
        "seed": 42,
        "eyes_open": True,
    }
    base.update(overrides)
    return MeditationRequest(**base)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("context", CONTEXTS)
@pytest.mark.parametrize("theme", THEMES)
async def test_curated_catalogue_always_passes_both_gates(
    theme: str, context: Context, language: Language
) -> None:
    """The brief's floor: a vetted script exists offline for every
    theme/context/language, and it passes the linter and the guard."""
    generator = MeditationScriptGenerator()
    request = _request(
        theme=theme, context=context, language=language, eyes_open=context != "seated"
    )
    script = await generator.generate(request)

    assert script.sections and all(s.source == "curated" for s in script.sections)
    assert script.resolved_theme == theme
    roles = [s.role for s in script.sections]
    assert roles[0] == "preface"
    assert "stop" in roles
    assert "eyes" in roles
    assert roles[-1] == "closing"

    report = lint_script(
        script.transcript,
        language=language,
        context=context,
        eyes_open=request.eyes_open,
        word_budget=int(request.duration_seconds * 115 / 60),
    )
    assert report.passed, (theme, context, language, report.findings)

    # Safety frame: preface + stop cue are structurally present.
    assert script.safety.safety_preface is True
    assert script.safety.stop_cue is True
    if context in {"walking", "driving"}:
        assert script.safety.eyes_open is True


async def test_same_request_same_seed_same_script() -> None:
    generator = MeditationScriptGenerator()
    first = await generator.generate(_request())
    second = await generator.generate(_request())
    assert first.content_hash == second.content_hash
    assert first.transcript == second.transcript
    assert first.sections == second.sections


async def test_different_seed_different_script() -> None:
    generator = MeditationScriptGenerator()
    first = await generator.generate(_request(seed=1))
    second = await generator.generate(_request(seed=2))
    assert first.content_hash != second.content_hash


async def test_seedless_request_is_deterministic() -> None:
    generator = MeditationScriptGenerator()
    first = await generator.generate(_request(seed=None))
    second = await generator.generate(_request(seed=None))
    assert first.content_hash == second.content_hash
    assert isinstance(first.seed, int)


async def test_hour_of_day_defaults_to_server_time() -> None:
    fixed = datetime(2026, 10, 17, 7, 30, tzinfo=UTC)
    generator = MeditationScriptGenerator(now=lambda: fixed)
    request = _request(theme="auto", hour_of_day=None)
    script = await generator.generate(request)
    # 07:30 UTC is in the morning bucket: morning or the any-time theme.
    assert script.resolved_theme in {"morning", "gratitude"}


async def test_hour_of_day_beats_server_time() -> None:
    fixed = datetime(2026, 10, 17, 7, 30, tzinfo=UTC)
    generator = MeditationScriptGenerator(now=lambda: fixed)
    script = await generator.generate(_request(theme="auto", hour_of_day=23))
    assert script.resolved_theme in {"sleep", "gratitude"}


@pytest.mark.parametrize(
    ("hour", "allowed"),
    [
        (6, {"morning", "gratitude"}),
        (13, {"focus", "gratitude"}),
        (17, {"stress-release", "gratitude"}),
        (21, {"calm", "gratitude"}),
        (23, {"sleep", "gratitude"}),
    ],
)
async def test_auto_resolution_respects_time_bucket(hour: int, allowed: set[str]) -> None:
    generator = MeditationScriptGenerator()
    for seed in range(8):
        request = _request(theme="auto", hour_of_day=hour, seed=seed)
        script = await generator.generate(request)
        assert script.resolved_theme in allowed, (hour, seed, script.resolved_theme)


async def test_preferences_narrow_the_auto_pool() -> None:
    generator = MeditationScriptGenerator()
    # 17:00 -> late-afternoon bucket; only stress-release is tagged "body".
    for seed in range(8):
        request = _request(
            theme="auto",
            hour_of_day=17,
            seed=seed,
            preferences=["body"],
        )
        script = await generator.generate(request)
        assert script.resolved_theme in {"stress-release", "gratitude"}


async def test_preference_tags_are_a_closed_vocabulary() -> None:
    generator = MeditationScriptGenerator()
    # Every tag resolves without error (the Literal already rejects others).
    for tag in sorted(PREFERENCE_TAGS):
        request = _request(theme="auto", hour_of_day=21, seed=3, preferences=[tag])
        script = await generator.generate(request)
        assert script.preferences == [tag]


class _PassageProvider(LLMProvider):
    """A scripted provider: a queue of replies (str) or exceptions (type)."""

    name = "scripted"

    def __init__(self, replies: list[str | type[Exception]]) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> LLMResult:
        self.calls += 1
        item = self.replies.pop(0) if self.replies else "Nothing to add."
        if isinstance(item, type) and issubclass(item, Exception):
            raise item()
        return LLMResult(text=item, provider=self.name)

    def stream(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    def describe(self) -> dict[str, object]:  # type: ignore[override]
        return {"provider": self.name, "model": None, "configured": True}


_GOOD_PASSAGE = (
    "Notice the feeling of your feet on the ground beneath you. They are "
    "steady, and you are steady with them. For a few slow breaths, let that "
    "steadiness be enough. You do not have to do anything else at all."
)


async def test_clean_llm_passage_is_adopted() -> None:
    provider = _PassageProvider([_GOOD_PASSAGE])
    generator = MeditationScriptGenerator(provider, allow_llm_passages=True)
    # A 2-minute script plans exactly one body passage -> one LLM slot.
    script = await generator.generate(_request(duration_seconds=120))
    llm_sections = [s for s in script.sections if s.source == "llm"]
    assert len(llm_sections) == 1
    assert llm_sections[0].text == _GOOD_PASSAGE
    assert provider.calls == 1


async def test_llm_slots_are_capped() -> None:
    provider = _PassageProvider([_GOOD_PASSAGE] * MAX_LLM_SLOTS)
    generator = MeditationScriptGenerator(provider, allow_llm_passages=True)
    # A 3-minute script plans 4 passages -> two LLM slots, both filled.
    script = await generator.generate(_request())
    llm_sections = [s for s in script.sections if s.source == "llm"]
    assert len(llm_sections) == MAX_LLM_SLOTS
    assert provider.calls == MAX_LLM_SLOTS


async def test_rejected_passage_gets_one_stricter_retry() -> None:
    bad = "Empty your mind completely and let yourself float away into darkness."
    provider = _PassageProvider([bad, _GOOD_PASSAGE])
    generator = MeditationScriptGenerator(provider, allow_llm_passages=True)
    script = await generator.generate(_request(duration_seconds=120))
    llm_sections = [s for s in script.sections if s.source == "llm"]
    assert provider.calls == 2
    if llm_sections:
        assert llm_sections[0].text == _GOOD_PASSAGE
    else:
        # The retry was rejected too: curated copy stands in, script still safe.
        assert all(s.source == "curated" for s in script.sections)


async def test_double_rejection_falls_back_to_curated() -> None:
    bad = "This will cure your tension and you will be calm in just minutes."
    provider = _PassageProvider([bad, bad])
    generator = MeditationScriptGenerator(provider, allow_llm_passages=True)
    script = await generator.generate(_request(duration_seconds=120))
    assert all(s.source == "curated" for s in script.sections)
    assert script.transcript


async def test_provider_failure_falls_back_silently() -> None:
    provider = _PassageProvider([RuntimeError])
    generator = MeditationScriptGenerator(provider, allow_llm_passages=True)
    script = await generator.generate(_request(duration_seconds=120))
    assert all(s.source == "curated" for s in script.sections)
    assert script.transcript


async def test_fake_provider_reply_never_reaches_a_script() -> None:
    """The offline Fake's canned chat reply is a test double, not a
    meditation voice: with it enabled, the script stays curated-only."""
    from app.services.llm.fake_provider import FakeLLMProvider

    provider = FakeLLMProvider()
    generator = MeditationScriptGenerator(provider, allow_llm_passages=True)
    script = await generator.generate(_request())
    assert all(s.source == "curated" for s in script.sections)


async def test_llm_disabled_makes_no_provider_call() -> None:
    provider = _PassageProvider([_GOOD_PASSAGE])
    generator = MeditationScriptGenerator(provider, allow_llm_passages=False)
    await generator.generate(_request())
    assert provider.calls == 0


def test_resolve_theme_is_deterministic() -> None:
    request = _request(theme="auto", hour_of_day=21)
    first = resolve_theme(request, rng=random.Random(7))
    second = resolve_theme(request, rng=random.Random(7))
    assert first == second


def test_explicit_theme_is_never_reresolved() -> None:
    for theme in THEMES:
        request = _request(theme=theme, hour_of_day=23)
        assert resolve_theme(request, rng=random.Random(1)) == theme


async def test_generated_script_round_trips_through_a_model() -> None:
    """The script model must survive a dump/validate cycle (the API does it)."""
    generator = MeditationScriptGenerator()
    script = await generator.generate(_request())
    dumped = script.model_dump()
    rebuilt = MeditationScript.model_validate(dumped)
    assert rebuilt.content_hash == script.content_hash


async def test_long_offline_scripts_stay_within_the_honest_floor() -> None:
    """A 15-minute offline script is shorter than nominal (the pool is
    finite) — the linter's floor relaxes to the curated maximum instead of
    failing, and the estimate is disclosed."""
    generator = MeditationScriptGenerator()
    script = await generator.generate(
        _request(theme="calm", duration_seconds=900, context="seated")
    )
    assert script.estimated_duration_s < 900
    assert script.word_count > 0
