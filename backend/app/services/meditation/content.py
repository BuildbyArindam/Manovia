"""Day 45: the curated meditation catalogue.

Content is trusted, reviewable data, not model output: a broken or
incomplete file rejects the whole catalogue at load (the loader raises),
so a typo is a startup failure rather than a silent half-catalogue.

The three per-language files (``en.yaml``, ``hi.yaml``, ``bn.yaml``) must
be structurally identical — same themes, same list lengths, every entry
present in every language — so a translation gap is a load failure, not a
sentence served in the wrong language. ``hi`` and ``bn`` are AI-produced
drafts flagged for native review in ``translations/STATUS.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Final

import yaml

from app.services.meditation.types import Context, Language

CONTENT_DIR: Final = "meditation"
LANGUAGES: Final = ("en", "hi", "bn")
TIME_BUCKETS: Final = ("morning", "midday", "late-afternoon", "evening", "night", "any")
PREFACE_VARIANTS: Final = ("default", "unknown", "walking", "driving")


class ContentError(ValueError):
    """The meditation catalogue is missing, broken, or out of lockstep."""


@dataclass(frozen=True)
class ThemeCatalog:
    id: str
    label: dict[Language, str]
    hint: dict[Language, str]
    tags: tuple[str, ...]
    best_time: str


@dataclass(frozen=True)
class LanguageContent:
    """Every vetted string for one language, in the fixed section order."""

    language: Language
    preface: dict[str, str]
    eyes_open: str
    eyes_closed: str
    stop_cue: str
    stop_closing: str
    breath_cues: tuple[str, ...]
    openings: dict[str, tuple[str, ...]]
    passages: dict[str, tuple[str, ...]]
    closings: dict[str, tuple[str, ...]]


def _load_yaml(name: str) -> dict[str, object]:
    try:
        raw = files("app.content").joinpath(CONTENT_DIR, name).read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except FileNotFoundError as exc:  # pragma: no cover - importlib wraps it
        raise ContentError(f"meditation content file missing: {name}") from exc
    if not isinstance(data, dict):
        raise ContentError(f"meditation content file is not a mapping: {name}")
    return data


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContentError(f"meditation content: {where} must be a non-empty string")
    return value.strip()


def _texts(values: object, where: str, *, min_length: int) -> tuple[str, ...]:
    if not isinstance(values, list) or len(values) < min_length:
        raise ContentError(
            f"meditation content: {where} must be a list with at least {min_length} entries"
        )
    return tuple(_text(v, f"{where}[{i}]") for i, v in enumerate(values))


@lru_cache(maxsize=1)
def load_themes() -> tuple[ThemeCatalog, ...]:
    data = _load_yaml("themes.yaml")
    if data.get("version") != 1:
        raise ContentError("meditation themes: unsupported version")
    entries = data.get("themes")
    if not isinstance(entries, list) or not entries:
        raise ContentError("meditation themes: 'themes' must be a non-empty list")
    themes: list[ThemeCatalog] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ContentError(f"meditation themes[{i}] is not a mapping")
        tid = entry.get("id")
        if not isinstance(tid, str) or not tid or tid in seen:
            raise ContentError(f"meditation themes[{i}]: duplicate or missing id")
        seen.add(tid)
        best_time = entry.get("best_time")
        if best_time not in TIME_BUCKETS:
            raise ContentError(f"meditation theme {tid}: unknown best_time {best_time!r}")
        labels: dict[Language, str] = {}
        hints: dict[Language, str] = {}
        for lang in LANGUAGES:
            label = entry.get("label")
            hint = entry.get("hint")
            if not isinstance(label, dict) or not isinstance(hint, dict):
                raise ContentError(f"meditation theme {tid}: label/hint must be mappings")
            labels[lang] = _text(label.get(lang), f"theme {tid} label {lang}")
            hints[lang] = _text(hint.get(lang), f"theme {tid} hint {lang}")
        tags = entry.get("tags")
        if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
            raise ContentError(f"meditation theme {tid}: tags must be strings")
        themes.append(
            ThemeCatalog(
                id=tid,
                label=labels,
                hint=hints,
                tags=tuple(tags),
                best_time=str(best_time),
            )
        )
    return tuple(themes)


def _theme_blocks(data: dict[str, object]) -> dict[str, dict[str, object]]:
    """The per-theme section of one language file, type-checked once."""
    raw = data.get("themes")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for tid, block in raw.items():
        if isinstance(tid, str) and isinstance(block, dict):
            out[tid] = block
    return out


@lru_cache(maxsize=1)
def load_content() -> dict[Language, LanguageContent]:
    """Load and cross-check all three languages. Raises on any gap."""
    theme_ids = [t.id for t in load_themes()]
    contents: dict[Language, LanguageContent] = {}
    for lang in LANGUAGES:
        data = _load_yaml(f"{lang}.yaml")
        if data.get("version") != 1:
            raise ContentError(f"meditation {lang}: unsupported version")

        preface_raw = data.get("preface")
        if not isinstance(preface_raw, dict):
            raise ContentError(f"meditation {lang}: 'preface' must be a mapping")
        preface = {
            v: _text(preface_raw.get(v), f"meditation {lang} preface {v}") for v in PREFACE_VARIANTS
        }

        eyes_raw = data.get("eyes")
        stop_raw = data.get("stop")
        if not isinstance(eyes_raw, dict) or not isinstance(stop_raw, dict):
            raise ContentError(f"meditation {lang}: 'eyes'/'stop' must be mappings")

        content = LanguageContent(
            language=lang,
            preface=preface,
            eyes_open=_text(eyes_raw.get("open"), f"meditation {lang} eyes.open"),
            eyes_closed=_text(eyes_raw.get("closed"), f"meditation {lang} eyes.closed"),
            stop_cue=_text(stop_raw.get("cue"), f"meditation {lang} stop.cue"),
            stop_closing=_text(stop_raw.get("closing"), f"meditation {lang} stop.closing"),
            breath_cues=_texts(
                data.get("breath_cues"), f"meditation {lang} breath_cues", min_length=2
            ),
            openings={
                tid: _texts(
                    _theme_blocks(data)[tid].get("openings"),
                    f"meditation {lang} themes.{tid}.openings",
                    min_length=2,
                )
                for tid in theme_ids
            },
            passages={
                tid: _texts(
                    _theme_blocks(data)[tid].get("passages"),
                    f"meditation {lang} themes.{tid}.passages",
                    min_length=4,
                )
                for tid in theme_ids
            },
            closings={
                tid: _texts(
                    _theme_blocks(data)[tid].get("closings"),
                    f"meditation {lang} themes.{tid}.closings",
                    min_length=2,
                )
                for tid in theme_ids
            },
        )

        # Every curated preface carries the stop guarantee and the
        # not-treatment disclosure; the eyes-closed line is the only
        # closed-eye sentence in the whole catalogue (linted in tests).
        for variant, text in preface.items():
            if content.stop_cue.split(",")[0].lower() not in text.lower():
                raise ContentError(
                    f"meditation {lang} preface {variant}: missing the stop-at-any-time guarantee"
                )
            if "not treatment" not in text.lower() and lang == "en":
                raise ContentError(
                    f"meditation {lang} preface {variant}: missing the not-treatment disclosure"
                )
        contents[lang] = content

    # Structural lockstep: the same counts in every language, so a missing
    # translation is a startup failure rather than a shorter script.
    reference = contents["en"]
    non_english: tuple[Language, ...] = ("hi", "bn")
    for lang in non_english:
        content = contents[lang]
        for theme_id in theme_ids:
            for role in ("openings", "passages", "closings"):
                ref_len = len(getattr(reference, role)[theme_id])
                other_len = len(getattr(content, role)[theme_id])
                if ref_len != other_len:
                    raise ContentError(
                        f"meditation parity: themes.{theme_id}.{role} has {ref_len} entries in en "
                        f"but {other_len} in {lang}"
                    )
        if len(reference.breath_cues) != len(content.breath_cues):
            raise ContentError("meditation parity: breath_cues differ between languages")
    return contents


def theme_by_id(theme_id: str) -> ThemeCatalog:
    for theme in load_themes():
        if theme.id == theme_id:
            return theme
    raise ContentError(f"unknown meditation theme: {theme_id}")


def content_for(language: Language) -> LanguageContent:
    return load_content()[language]


def preface_for(language: Language, context: Context) -> str:
    """Pick the preface variant for the context. ``seated`` uses the short
    default; an unknown context carries the driving warning."""
    variant = "default" if context == "seated" else context
    return content_for(language).preface[variant]


__all__ = [
    "CONTENT_DIR",
    "LANGUAGES",
    "PREFACE_VARIANTS",
    "TIME_BUCKETS",
    "ContentError",
    "Context",
    "Language",
    "LanguageContent",
    "ThemeCatalog",
    "content_for",
    "load_content",
    "load_themes",
    "preface_for",
    "theme_by_id",
]
