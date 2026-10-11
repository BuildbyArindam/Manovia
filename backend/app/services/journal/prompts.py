"""Curated, localised journal writing prompts and templates.

Loaded from ``backend/app/content/journal_prompts.json`` and validated on import.
Prompts rotate deterministically by UTC date ordinal so ``GET /api/v1/journal/prompts``
surfaces a fresh curated set each day while keeping the full catalogue and
templates (``free``, ``gratitude``, ``worry_dump``) available.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from importlib.resources import files

from app.content.i18n import resolve_locale

_PROMPTS_FILE = "journal_prompts.json"

VALID_TEMPLATES: tuple[str, ...] = ("free", "gratitude", "worry_dump")


@dataclass(frozen=True)
class LocalisedJournalTemplate:
    """One journal template localized for a specific language."""

    id: str
    label: str
    description: str
    scaffold_title: str
    scaffold_body: str


@dataclass(frozen=True)
class LocalisedJournalPrompt:
    """One curated writing prompt localized for a specific language."""

    id: str
    category: str
    template: str
    text: str
    hint: str


@dataclass(frozen=True)
class JournalPromptsCatalogue:
    """Parsed raw catalogue from ``journal_prompts.json``."""

    version: str
    supported_locales: tuple[str, ...]
    templates: tuple[dict[str, object], ...]
    prompts: tuple[dict[str, object], ...]


def _pick_localised(mapping: object, locale: str, *, field: str) -> str:
    if not isinstance(mapping, dict):
        raise ValueError(f"journal_prompts.json field {field!r} must be a locale mapping")
    resolved, _ = resolve_locale(locale)
    val = mapping.get(resolved) or mapping.get("en")
    if not isinstance(val, str):
        raise ValueError(f"journal_prompts.json field {field!r} missing string for {resolved!r}")
    return val


@lru_cache(maxsize=1)
def load_journal_prompts_catalogue() -> JournalPromptsCatalogue:
    """Load and validate ``journal_prompts.json`` from ``app.content``."""
    raw = files("app.content").joinpath(_PROMPTS_FILE).read_text(encoding="utf-8")
    payload = json.loads(raw)

    version = str(payload.get("version", ""))
    if not version:
        raise ValueError("journal_prompts.json must declare a non-empty version")

    locales_raw = payload.get("supported_locales", [])
    if not isinstance(locales_raw, list) or not locales_raw:
        raise ValueError("journal_prompts.json must declare supported_locales")
    supported_locales = tuple(str(loc) for loc in locales_raw)

    templates_raw = payload.get("templates", [])
    if not isinstance(templates_raw, list) or not templates_raw:
        raise ValueError("journal_prompts.json must declare at least one template")

    seen_template_ids: set[str] = set()
    for tpl in templates_raw:
        if not isinstance(tpl, dict):
            raise ValueError("Each template in journal_prompts.json must be an object")
        tid = str(tpl.get("id", ""))
        if tid not in VALID_TEMPLATES:
            raise ValueError(f"Unknown template id {tid!r} in journal_prompts.json")
        if tid in seen_template_ids:
            raise ValueError(f"Duplicate template id {tid!r} in journal_prompts.json")
        seen_template_ids.add(tid)
        for loc in supported_locales:
            _pick_localised(tpl.get("label"), loc, field=f"templates[{tid}].label")
            _pick_localised(tpl.get("description"), loc, field=f"templates[{tid}].description")
            _pick_localised(
                tpl.get("scaffold_title"), loc, field=f"templates[{tid}].scaffold_title"
            )
            _pick_localised(tpl.get("scaffold_body"), loc, field=f"templates[{tid}].scaffold_body")

    prompts_raw = payload.get("prompts", [])
    if not isinstance(prompts_raw, list) or len(prompts_raw) < 4:
        raise ValueError("journal_prompts.json must contain at least 4 curated prompts")

    seen_prompt_ids: set[str] = set()
    for item in prompts_raw:
        if not isinstance(item, dict):
            raise ValueError("Each prompt in journal_prompts.json must be an object")
        pid = str(item.get("id", "")).strip()
        if not pid or pid in seen_prompt_ids:
            raise ValueError(f"Invalid or duplicate prompt id {pid!r} in journal_prompts.json")
        seen_prompt_ids.add(pid)
        tpl_id = str(item.get("template", "free"))
        if tpl_id not in VALID_TEMPLATES:
            raise ValueError(f"Prompt {pid!r} references unknown template {tpl_id!r}")
        for loc in supported_locales:
            text_val = _pick_localised(item.get("text"), loc, field=f"prompts[{pid}].text")
            if not text_val.strip():
                raise ValueError(f"Prompt {pid!r} has empty text for locale {loc!r}")
            _pick_localised(item.get("hint"), loc, field=f"prompts[{pid}].hint")

    return JournalPromptsCatalogue(
        version=version,
        supported_locales=supported_locales,
        templates=tuple(templates_raw),
        prompts=tuple(prompts_raw),
    )


def get_localised_templates(locale: str = "en") -> list[LocalisedJournalTemplate]:
    """Return all journal templates (`free`, `gratitude`, `worry_dump`) in `locale`."""
    catalogue = load_journal_prompts_catalogue()
    resolved, _ = resolve_locale(locale)
    result: list[LocalisedJournalTemplate] = []
    for tpl in catalogue.templates:
        tid = str(tpl["id"])
        result.append(
            LocalisedJournalTemplate(
                id=tid,
                label=_pick_localised(tpl["label"], resolved, field="label"),
                description=_pick_localised(tpl["description"], resolved, field="description"),
                scaffold_title=_pick_localised(
                    tpl["scaffold_title"], resolved, field="scaffold_title"
                ),
                scaffold_body=_pick_localised(
                    tpl["scaffold_body"], resolved, field="scaffold_body"
                ),
            )
        )
    return result


def get_rotating_prompts(
    *,
    locale: str = "en",
    for_date: date | None = None,
    template: str | None = None,
    count: int = 4,
) -> tuple[LocalisedJournalPrompt, list[LocalisedJournalPrompt]]:
    """Return ``(featured_prompt, rotating_prompts)`` for ``for_date`` and ``locale``."""
    catalogue = load_journal_prompts_catalogue()
    resolved, _ = resolve_locale(locale)
    active_date = for_date or datetime.now(UTC).date()

    all_localised: list[LocalisedJournalPrompt] = []
    for item in catalogue.prompts:
        tpl_id = str(item["template"])
        if template is not None and tpl_id != template:
            continue
        all_localised.append(
            LocalisedJournalPrompt(
                id=str(item["id"]),
                category=str(item["category"]),
                template=tpl_id,
                text=_pick_localised(item["text"], resolved, field="text"),
                hint=_pick_localised(item["hint"], resolved, field="hint"),
            )
        )

    if not all_localised:
        # Fallback if a template filter matched nothing (should never happen for valid templates)
        return get_rotating_prompts(
            locale=resolved, for_date=active_date, template=None, count=count
        )

    total = len(all_localised)
    window = max(1, min(count, total))
    offset = active_date.toordinal() % total
    rotated = [all_localised[(offset + idx) % total] for idx in range(window)]
    return rotated[0], rotated


__all__ = [
    "VALID_TEMPLATES",
    "JournalPromptsCatalogue",
    "LocalisedJournalPrompt",
    "LocalisedJournalTemplate",
    "get_localised_templates",
    "get_rotating_prompts",
    "load_journal_prompts_catalogue",
]
