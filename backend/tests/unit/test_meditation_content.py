"""Day 45: the curated catalogue loader.

The loader is the "content is trusted, reviewable data" gate: a broken or
incomplete file rejects the whole catalogue (raises), and the three
languages must stay in lockstep — a translation gap is a load failure, not
a sentence served in the wrong language.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.meditation.content import (
    LANGUAGES,
    ContentError,
    load_content,
    load_themes,
    preface_for,
)


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    load_themes.cache_clear()
    load_content.cache_clear()
    yield
    load_themes.cache_clear()
    load_content.cache_clear()


def test_themes_load_with_closed_fields() -> None:
    themes = load_themes()
    ids = [t.id for t in themes]
    assert ids == ["morning", "focus", "stress-release", "calm", "sleep", "gratitude"]
    for theme in themes:
        assert theme.best_time in {"morning", "midday", "late-afternoon", "evening", "night", "any"}
        assert set(theme.label) == set(LANGUAGES)
        assert set(theme.hint) == set(LANGUAGES)
        assert theme.tags


def test_content_loads_all_three_languages_in_lockstep() -> None:
    contents = load_content()
    assert set(contents) == set(LANGUAGES)
    theme_ids = {t.id for t in load_themes()}
    for content in contents.values():
        assert set(content.openings) == theme_ids
        assert set(content.passages) == theme_ids
        assert set(content.closings) == theme_ids
        for theme_id in theme_ids:
            assert len(content.openings[theme_id]) == 2
            assert len(content.passages[theme_id]) == 4
            assert len(content.closings[theme_id]) == 2
        assert len(content.breath_cues) >= 2
        assert set(content.preface) == {"default", "unknown", "walking", "driving"}
        assert content.eyes_open and content.eyes_closed
        assert content.stop_cue and content.stop_closing


def test_every_entry_is_nonempty_text() -> None:
    contents = load_content()
    for content in contents.values():
        for text in (
            [content.eyes_open, content.eyes_closed, content.stop_cue, content.stop_closing]
            + list(content.breath_cues)
            + [t for group in content.preface.values() for t in (group,)]
            + [t for group in content.openings.values() for t in group]
            + [t for group in content.passages.values() for t in group]
            + [t for group in content.closings.values() for t in group]
        ):
            assert text.strip()


def test_stop_cue_is_present_in_every_language() -> None:
    # The linter checks for the first comma-clause of the stop cue per
    # language; both parts must exist for that check to be meaningful.
    for content in load_content().values():
        first_clause = content.stop_cue.split(",")[0].strip()
        assert first_clause


def test_preface_for_maps_contexts() -> None:
    contents = load_content()
    for lang in LANGUAGES:
        content = contents[lang]
        assert preface_for(lang, "seated") == content.preface["default"]
        assert preface_for(lang, "walking") == content.preface["walking"]
        assert preface_for(lang, "driving") == content.preface["driving"]
        assert preface_for(lang, "unknown") == content.preface["unknown"]


def test_themes_wrong_version_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.meditation.content._load_yaml",
        lambda name: {"version": 2, "themes": []} if name == "themes.yaml" else {},
    )
    with pytest.raises(ContentError, match="unsupported version"):
        load_themes()


def test_themes_duplicate_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = {
        "id": "calm",
        "label": {lang: "l" for lang in LANGUAGES},
        "hint": {lang: "h" for lang in LANGUAGES},
        "tags": ["quiet"],
        "best_time": "evening",
    }
    monkeypatch.setattr(
        "app.services.meditation.content._load_yaml",
        lambda name: (
            {"version": 1, "themes": [entry, dict(entry)]} if name == "themes.yaml" else {}
        ),
    )
    with pytest.raises(ContentError, match="duplicate"):
        load_themes()


def test_themes_unknown_best_time_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = {
        "id": "calm",
        "label": {lang: "l" for lang in LANGUAGES},
        "hint": {lang: "h" for lang in LANGUAGES},
        "tags": ["quiet"],
        "best_time": "lunch",
    }
    monkeypatch.setattr(
        "app.services.meditation.content._load_yaml",
        lambda name: {"version": 1, "themes": [entry]} if name == "themes.yaml" else {},
    )
    with pytest.raises(ContentError, match="unknown best_time"):
        load_themes()


def test_content_too_few_passages_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.meditation.content as mod

    real_load = mod._load_yaml

    def fake(name: str) -> dict[str, Any]:
        data = real_load(name)
        if name == "hi.yaml":
            data = dict(data)
            themes = dict(data["themes"])  # type: ignore[index]
            calm = dict(themes["calm"])
            calm["passages"] = list(calm["passages"])[:3]  # one short
            themes["calm"] = calm
            data["themes"] = themes
        return data

    monkeypatch.setattr(mod, "_load_yaml", fake)
    with pytest.raises(ContentError, match="at least 4 entries"):
        load_content()


def test_content_lockstep_count_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual parity gate: bn with one fewer passage must fail the load."""
    import app.services.meditation.content as mod

    real_load = mod._load_yaml

    def fake(name: str) -> dict[str, Any]:
        data = real_load(name)
        if name == "bn.yaml":
            data = dict(data)
            themes = dict(data["themes"])  # type: ignore[index]
            sleep = dict(themes["sleep"])
            passages = list(sleep["passages"])
            sleep["passages"] = [*passages, passages[0]]  # one extra: 5 vs 4 in en
            themes["sleep"] = sleep
            data["themes"] = themes
        return data

    monkeypatch.setattr(mod, "_load_yaml", fake)
    with pytest.raises(ContentError, match="parity"):
        load_content()
