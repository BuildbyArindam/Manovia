"""Day 45: the meditation script generator.

Composition = vetted structure + (optionally) model-written passages.

* The skeleton — safety preface, stop cue, eyes line, opening, breath
  cues, closing — always comes from the curated catalogue
  (``app/content/meditation``). The LLM can only ever replace individual
  body passages, never the safety frame.
* Every candidate LLM passage is linted before assembly
  (``lint_passage``); a rejected passage gets one stricter retry, then
  reviewed copy stands in for it.
* The assembled script is then run through the deterministic script lint
  AND the full Day 14 output guard (``RuleBasedOutputGuard``). A failed
  gate with LLM text present falls back to a curated-only composition,
  which the test suite pins to always pass both gates.

Offline default: with the Fake provider (or no provider) no LLM call is
made at all — the script is a deterministic function of the request plus
the seed, which is what makes the content hash a stable cache key.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from app.services.llm.base import LLMMessage, LLMProvider
from app.services.meditation.content import content_for, load_themes, preface_for
from app.services.meditation.lint import LintReport, lint_passage, lint_script
from app.services.meditation.prompt import load_passage_prompt
from app.services.meditation.types import (
    WORDS_PER_MINUTE_BASE,
    Context,
    Language,
    MeditationRequest,
    MeditationScript,
    ScriptRole,
    ScriptSafety,
    ScriptSection,
    ScriptSource,
)
from app.services.safety.base import RiskLevel
from app.services.safety.output_guard import GuardVerdict, RuleBasedOutputGuard

#: Target word count for one body passage (curated passages are 40-70).
PASSAGE_TARGET_WORDS: Final = 55
PASSAGE_MIN_WORDS: Final = 30
PASSAGE_MAX_WORDS: Final = 90
MAX_PASSAGES: Final = 12
MAX_LLM_SLOTS: Final = 2

#: Pause markers (ms of silence inserted by the renderer before the section
#: starts speaking) — the "SSML-like pauses" of the script structure.
_PAUSE_MS: Final = {
    "preface": 0,
    "stop": 1200,
    "eyes": 800,
    "opening": 1200,
    "breath": 1000,
    "passage": 1500,
    "closing": 1800,
}


class MeditationCompositionError(RuntimeError):
    """A script that cannot be composed safely. Carries no script text."""


def _words(text: str) -> int:
    return len(text.split())


def _bucket_for_hour(hour: int) -> str:
    if 5 <= hour <= 10:
        return "morning"
    if 11 <= hour <= 15:
        return "midday"
    if 16 <= hour <= 19:
        return "late-afternoon"
    if 20 <= hour <= 22:
        return "evening"
    return "night"  # 23:00-04:59


def resolve_theme(request: MeditationRequest, *, rng: random.Random) -> str:
    """Deterministic ``auto`` resolution: time of day first, a matching
    preference tag narrows the pool, the seed breaks ties."""
    if request.theme != "auto":
        return request.theme
    themes = load_themes()
    hour = request.hour_of_day if request.hour_of_day is not None else datetime.now(UTC).hour
    bucket = _bucket_for_hour(hour)
    pool = [t for t in themes if t.best_time in (bucket, "any")]
    if not pool:
        pool = list(themes)
    tagged = [t for t in pool if set(t.tags) & set(request.preferences)]
    candidates = tagged or pool
    return candidates[rng.randrange(len(candidates))].id


@dataclass(frozen=True)
class _Plan:
    """The word budget plan for one script."""

    budget: int
    n_passages: int
    n_breaths: int


def _plan(request: MeditationRequest) -> _Plan:
    budget = int(request.duration_seconds * (WORDS_PER_MINUTE_BASE * request.voice_speed) / 60.0)
    body = max(budget - _ESTIMATED_FIXED_WORDS, PASSAGE_TARGET_WORDS)
    n_passages = max(1, min(MAX_PASSAGES, round(body / PASSAGE_TARGET_WORDS)))
    n_breaths = max(1, min(3, 1 + n_passages // 3))
    return _Plan(budget=budget, n_passages=n_passages, n_breaths=n_breaths)


#: Rough size of the fixed skeleton (preface + stop + eyes + closing + one
#: breath cue) in the longest of the three languages. Used only to derive
#: how many passages a budget should hold; the linter uses the exact,
#: per-language numbers.
_ESTIMATED_FIXED_WORDS: Final = 150


def _curated_max_words(language: Language, theme: str, context: Context, eyes_open: bool) -> int:
    """The most words the curated pool can honestly fill, for this request.

    This is the honest relaxation of the 0.75*budget floor: a script can
    never be longer than its whole curated pool (plus repeated passages for
    long durations, which the top-up below does not need). When a theme's
    pool is thin in a language, the floor drops to what the pool can
    actually deliver instead of rejecting every offline script for it.
    """
    content = content_for(language)
    eyes_words = _words(content.eyes_open if eyes_open else content.eyes_closed)
    top_breaths = sorted((_words(b) for b in content.breath_cues), reverse=True)[:3]
    return (
        _words(preface_for(language, context))
        + _words(content.stop_cue)
        + eyes_words
        + max(_words(o) for o in content.openings[theme])
        + sum(_words(p) for p in content.passages[theme])
        + sum(top_breaths)
        + max(_words(c) for c in content.closings[theme])
    )


class _Composition:
    """One assembly of the script sections, before linting and guarding."""

    def __init__(self, sections: list[tuple[ScriptRole, str, ScriptSource]] | None = None) -> None:
        # (role, text, source)
        self.sections: list[tuple[ScriptRole, str, ScriptSource]] = sections or []

    def add(self, role: ScriptRole, text: str, source: ScriptSource = "curated") -> None:
        self.sections.append((role, text, source))

    @property
    def llm_slot_count(self) -> int:
        return sum(1 for _, _, source in self.sections if source == "llm")

    def transcript(self) -> str:
        return "\n\n".join(text for _, text, _ in self.sections)

    def replace_slot(
        self, role: ScriptRole, position: int, text: str, source: ScriptSource
    ) -> None:
        """Replace the ``position``-th section of ``role`` (0-based among
        that role's sections)."""
        seen = -1
        for i, (r, _, _) in enumerate(self.sections):
            if r == role:
                seen += 1
                if seen == position:
                    self.sections[i] = (role, text, source)
                    return
        raise MeditationCompositionError("slot not found")


class MeditationScriptGenerator:
    """Composes, lints and guard-checks one meditation script."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        *,
        allow_llm_passages: bool = False,
        output_guard: RuleBasedOutputGuard | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._allow_llm = allow_llm_passages and provider is not None
        self._output_guard = output_guard
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def llm_enabled(self) -> bool:
        return self._allow_llm

    async def generate(self, request: MeditationRequest) -> MeditationScript:
        seed = request.seed if request.seed is not None else _seed_for(request)
        rng = random.Random(seed)
        resolved_theme = resolve_theme(request, rng=rng)
        plan = _plan(request)
        floor_words = min(
            int(plan.budget * 0.75),
            _curated_max_words(
                request.language, resolved_theme, request.context, request.eyes_open
            ),
        )

        composition = self._compose_curated(request, resolved_theme, plan, rng)
        llm_texts = await self._upgrade_with_llm(request, resolved_theme, plan, rng, composition)

        lint_report, guard_verdict = await self._check(request, plan, composition, floor_words)
        if not (lint_report.passed and guard_verdict.passed) and llm_texts:
            # One stricter regeneration already happened inside
            # ``_upgrade_with_llm``; the last resort is curated-only, drawn
            # with a fresh seed so the suspect material is gone.
            composition = self._compose_curated(
                request, resolved_theme, plan, random.Random(seed + 1)
            )
            lint_report, guard_verdict = await self._check(request, plan, composition, floor_words)
        if not lint_report.passed or not guard_verdict.passed:
            codes = ",".join(f.code for f in lint_report.findings)
            reason = guard_verdict.reason or "unknown"
            raise MeditationCompositionError(
                f"script failed safety gates after fallback (lint: {codes}; guard: {reason})"
            )

        return self._assemble(request, resolved_theme, composition, seed)

    def _compose_curated(
        self,
        request: MeditationRequest,
        theme: str,
        plan: _Plan,
        rng: random.Random,
    ) -> _Composition:
        """The guaranteed-safe floor: every slot from the curated catalogue.

        The same skeleton is used as the base for the LLM upgrade path, so
        curated-only is always reachable in one step."""
        content = content_for(request.language)
        eyes_open = request.eyes_open
        composition = _Composition()
        composition.add("preface", preface_for(request.language, request.context))
        composition.add("stop", content.stop_cue)
        composition.add("eyes", content.eyes_open if eyes_open else content.eyes_closed)
        openings = content.openings[theme]
        composition.add("opening", openings[rng.randrange(len(openings))])

        pool = list(content.passages[theme])
        rng.shuffle(pool)
        breaths = list(content.breath_cues)
        rng.shuffle(breaths)
        breaths_used: list[int] = []  # indexes into ``breaths``, in order

        def next_breath() -> str:
            # Round-robin over the cues, so three cues from a two-item pool
            # never repeat one twice in a row.
            index = len(breaths_used) % len(breaths)
            breaths_used.append(index)
            return breaths[index]

        breath_positions = [1, 3, 5][: plan.n_breaths]  # after passages 2, 4, 6 (1-based)
        for i in range(plan.n_passages):
            composition.add("passage", pool[i % len(pool)])
            if i in breath_positions:
                composition.add("breath", next_breath())
        if plan.n_passages == 1:
            # A short script still gets one breath cue, right after the opening.
            composition.add("breath", next_breath())

        closings = content.closings[theme]
        composition.add("closing", closings[rng.randrange(len(closings))])

        # If the drawn curated passages overshoot the budget (long sentences
        # in a short script), trim from the last curated passage inward.
        while _words(composition.transcript()) > plan.budget * 1.20:
            passage_indexes = [i for i, s in enumerate(composition.sections) if s[0] == "passage"]
            if len(passage_indexes) <= 1:
                break
            composition.sections.pop(passage_indexes[-1])

        # A thin pool (shortest theme in a language) can undershoot 75% of a
        # short budget; a breath cue is the only vetted material that fills
        # it without padding. Up to three total, each before the closing.
        while _words(composition.transcript()) < plan.budget * 0.75 and len(breaths_used) < 3:
            insert_at = max(
                i for i, s in enumerate(composition.sections) if s[0] == "passage"
            ) + 1
            composition.sections.insert(insert_at, ("breath", next_breath(), "curated"))
        return composition

    async def _upgrade_with_llm(
        self,
        request: MeditationRequest,
        theme: str,
        plan: _Plan,
        rng: random.Random,
        composition: _Composition,
    ) -> list[str]:
        """Replace up to ``MAX_LLM_SLOTS`` body passages with model-written
        ones, each linted before it may enter the script. Returns the list of
        LLM texts that made it in (empty = curated-only)."""
        if not self._allow_llm or plan.n_passages == 0 or self._provider is None:
            return []
        slots = sorted(rng.sample(range(plan.n_passages), min(MAX_LLM_SLOTS, plan.n_passages)))
        adopted: list[str] = []
        for slot in slots:
            text = await self._llm_passage(request, theme, stricter=False)
            if text is not None:
                composition.replace_slot("passage", slot, text, "llm")
                adopted.append(text)
        return adopted

    async def _llm_passage(
        self, request: MeditationRequest, theme: str, *, stricter: bool
    ) -> str | None:
        """One model-written passage, linted before it may be used.

        A rejected passage gets exactly one stricter retry; a provider
        failure or a second rejection yields None and the curated text
        stands in. No passage text is ever logged.
        """
        provider = self._provider
        if provider is None:
            return None
        prompt = load_passage_prompt()
        note = (
            "Your previous passage was rejected. Write a shorter, plainer one."
            if stricter
            else "Keep the person's attention gentle and present."
        )
        messages = [
            LLMMessage(
                role="user",
                content=(
                    f"Write a meditation passage, about {PASSAGE_TARGET_WORDS} words, for the "
                    f"theme '{theme}', for someone whose context is '{request.context}', "
                    f"in language '{request.language}'. {note}"
                ),
            )
        ]
        try:
            result = await provider.complete(
                messages,
                system=prompt.render(
                    language=request.language,
                    theme=theme,
                    context=request.context,
                    word_target=PASSAGE_TARGET_WORDS,
                ),
                max_tokens=220,
                temperature=0.7,
            )
        except Exception:
            # Provider trouble is not a content failure: fall back silently
            # to curated copy. The exception class (never the text) is what
            # a caller may log.
            return None
        text = result.text.strip().strip("\"'").strip()
        if not text:
            return None
        report = lint_passage(
            text,
            language=request.language,
            min_words=PASSAGE_MIN_WORDS,
            max_words=PASSAGE_MAX_WORDS,
        )
        if not report.passed and not stricter:
            return await self._llm_passage(request, theme, stricter=True)
        return text if report.passed else None

    async def _check(
        self,
        request: MeditationRequest,
        plan: _Plan,
        composition: _Composition,
        floor_words: int,
    ) -> tuple[LintReport, GuardVerdict]:
        text = composition.transcript()
        lint_report = lint_script(
            text,
            language=request.language,
            context=request.context,
            eyes_open=request.eyes_open,
            word_budget=plan.budget,
            min_achievable_words=floor_words,
        )
        guard = self._output_guard or RuleBasedOutputGuard(max_words=int(plan.budget * 1.25) + 100)
        guard_verdict = await guard.check(
            text, risk=RiskLevel.NONE, expected_language=request.language
        )
        return lint_report, guard_verdict

    def _assemble(
        self,
        request: MeditationRequest,
        theme: str,
        composition: _Composition,
        seed: int,
    ) -> MeditationScript:
        wps = request.voice_speed * WORDS_PER_MINUTE_BASE / 60.0
        sections: list[ScriptSection] = []
        t = 0.0
        for i, (role, text, source) in enumerate(composition.sections):
            pause_ms = _PAUSE_MS[role]
            t += pause_ms / 1000.0
            start = t
            duration = max(_words(text) / wps, 0.5)
            end = start + duration
            t = end
            sections.append(
                ScriptSection(
                    id=f"{role}-{i + 1:02d}",
                    role=role,
                    text=text,
                    source=source,
                    pause_before_ms=pause_ms,
                    start_s=round(start, 2),
                    end_s=round(end, 2),
                )
            )
        transcript = composition.transcript()
        return MeditationScript(
            id=uuid.uuid4().hex[:24],
            content_hash=_content_hash(
                language=request.language,
                theme=theme,
                duration_seconds=request.duration_seconds,
                voice_speed=request.voice_speed,
                context=request.context,
                eyes_open=request.eyes_open,
                preferences=request.preferences,
                seed=seed,
                sections=sections,
            ),
            theme=request.theme,
            resolved_theme=theme,
            language=request.language,
            duration_seconds=request.duration_seconds,
            voice_speed=request.voice_speed,
            context=request.context,
            eyes_open=request.eyes_open,
            preferences=list(request.preferences),
            seed=seed,
            word_count=_words(transcript),
            estimated_duration_s=round(t, 1),
            sections=sections,
            transcript=transcript,
            safety=ScriptSafety(
                safety_preface=True,
                stop_cue=True,
                eyes_open=request.eyes_open,
                driving_warning=request.context in {"driving", "unknown"},
                eyes_closed_allowed=not request.eyes_open,
            ),
        )


def _seed_for(request: MeditationRequest) -> int:
    """A deterministic seed for requests that do not carry one, so the same
    request always composes the same script (the cache key must be stable)."""
    basis = "|".join(
        [
            request.theme,
            str(request.duration_seconds),
            f"{request.voice_speed:.2f}",
            request.language,
            request.context,
            str(request.eyes_open),
            ",".join(sorted(request.preferences)),
        ]
    )
    return int.from_bytes(hashlib.sha256(basis.encode("utf-8")).digest()[:4], "big")


def _content_hash(
    *,
    language: Language,
    theme: str,
    duration_seconds: int,
    voice_speed: float,
    context: Context,
    eyes_open: bool,
    preferences: Sequence[str],
    seed: int,
    sections: Sequence[ScriptSection],
) -> str:
    """The content hash: a function of everything that gets spoken.

    The renderer reads ``sections`` (text, roles, pauses, sources), so the
    hash must cover them — the top-level ``transcript`` is a display-only
    convenience field, and a hash that ignored the sections would let a
    client get tampered section text rendered (Day 45 verification found
    exactly that; the ``meditation-v2`` marker separates this basis from
    the earlier transcript-only one)."""
    section_parts = [
        "|".join([section.role, section.source, section.text, str(section.pause_before_ms)])
        for section in sections
    ]
    basis = "|".join(
        [
            "meditation-v2",
            language,
            theme,
            str(duration_seconds),
            f"{voice_speed:.2f}",
            context,
            str(eyes_open).lower(),
            ",".join(sorted(preferences)),
            str(seed),
            "\u00a7".join(section_parts),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


__all__ = [
    "MAX_LLM_SLOTS",
    "MAX_PASSAGES",
    "PASSAGE_MAX_WORDS",
    "PASSAGE_MIN_WORDS",
    "PASSAGE_TARGET_WORDS",
    "MeditationCompositionError",
    "MeditationScriptGenerator",
    "resolve_theme",
]
