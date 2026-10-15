"""Versioned locale copy for the opt-in, non-medical Mood Outlook."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib.resources import files
from typing import Final, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from app.content.insights import resolve_insight_locale

_OUTLOOK_COPY_FILE: Final = "outlook_copy.json"
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_REQUIRED: Final[dict[str, frozenset[str]]] = {
    "summary": frozenset({"mean", "low", "high"}),
    "uncertainty_note": frozenset(),
    "not_health_advice": frozenset(),
    "not_enough_data": frozenset({"minimum", "window"}),
    "stale_data": frozenset(),
    "factor_line": frozenset({"factor", "tagged_mean", "other_mean", "tagged_n", "untagged_n"}),
    "no_factors": frozenset(),
    "action": frozenset(),
    "support": frozenset(),
}


class OutlookLocaleCopy(BaseModel):
    """One locale's fixed, placeholder-checked Outlook sentences."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str
    uncertainty_note: str
    not_health_advice: str
    not_enough_data: str
    stale_data: str
    factor_line: str
    no_factors: str
    action: str
    support: str

    def render(self, key: str, **values: str) -> str:
        """Render an exact placeholder set; caller text cannot add new wording."""
        if key not in _REQUIRED:
            raise KeyError(f"unknown Outlook copy key {key!r}")
        template = getattr(self, key)
        used = frozenset(_PLACEHOLDER.findall(template))
        expected = _REQUIRED[key]
        if used != expected:
            raise ValueError(f"Outlook template {key!r} needs placeholders {sorted(expected)}")
        if frozenset(values) != expected:
            raise ValueError(
                f"Outlook template {key!r} expected values {sorted(expected)}, "
                f"received {sorted(values)}"
            )
        return cast(str, template.format_map(values))

    def all_text(self) -> tuple[str, ...]:
        """Every user-facing sentence in a stable, lintable order."""
        return tuple(getattr(self, key) for key in _REQUIRED)


class OutlookCopyFile(BaseModel):
    """Versioned locale map shipped with the backend package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int
    locales: dict[str, OutlookLocaleCopy]


@lru_cache(maxsize=1)
def _load_copy_file() -> OutlookCopyFile:
    path = files("app.content").joinpath(_OUTLOOK_COPY_FILE)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = OutlookCopyFile.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"invalid packaged Mood Outlook copy: {exc}") from exc
    for locale, content in result.locales.items():
        if len(locale) not in (2, 5) or not content.all_text():
            raise RuntimeError(f"invalid locale in Mood Outlook copy: {locale!r}")
        for key, expected in _REQUIRED.items():
            actual = frozenset(_PLACEHOLDER.findall(getattr(content, key)))
            if actual != expected:
                raise RuntimeError(
                    f"outlook_copy.json/{locale}/{key} needs {sorted(expected)}, "
                    f"found {sorted(actual)}"
                )
    return result


def load_outlook_copy(locale: str | None = None) -> tuple[OutlookLocaleCopy, str, bool]:
    """Return the shipped locale copy, resolved locale and fallback status."""
    resolved, fallback_used = resolve_insight_locale(locale)
    locales = _load_copy_file().locales
    if resolved not in locales:
        resolved = "en"
        fallback_used = True
    try:
        return locales[resolved], resolved, fallback_used
    except KeyError as exc:
        raise RuntimeError("Mood Outlook must ship English copy") from exc


__all__ = ["OutlookLocaleCopy", "load_outlook_copy"]
