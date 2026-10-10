"""Load and validate the localised crisis templates in ``content/i18n/*.json``.

AGENTS.md safety rule 2: at high risk the reply is **pre-written**. It is not
generated, not assembled from the user's own words, and not varied per request —
which means the exact text a person sees in a crisis is reviewable in this repo,
and reviewable in the language they asked for.

The rules engine decides *that* somebody needs this message. This module decides
*which* words they get, and it fails loudly:

* every locale file must carry every required template id — a missing
  translation is a startup error, not a blank card shown to someone in distress;
* every ``{placeholder}``` in every string must be on that file's whitelist, so
  a typo cannot leak into the rendered message and so nobody can smuggle user
  text into a template;
* the fallback locale (English) must exist.

Adding a language means dropping a JSON file next to the others. Nothing in this
module enumerates locales by hand.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from typing import Any, Final

from pydantic import BaseModel, Field, field_validator

#: Directory inside ``app/content`` holding one JSON file per locale.
I18N_DIR: Final = "i18n"

#: The locale served when the requested one is unknown or untranslated.
FALLBACK_LOCALE: Final = "en"

#: Template ids every locale must carry. ``crisis.*`` are the deterministic
#: high-risk replies; ``check_in.*`` are the warm nudges at LOW/MEDIUM, where the
#: LLM is still allowed to answer but the person should see a human option.
REQUIRED_TEMPLATES: Final = (
    "crisis.high",
    "crisis.imminent",
    #: Used instead of the two above when the message is about somebody else
    #: ("my friend says she wants to die"). Same urgency, different reader: the
    #: copy tells a supporter what to do, not a person in crisis.
    "crisis.about_someone_else",
    "check_in.medium",
    "check_in.low",
)

#: ``{name}`` — the only substitution syntax templates may use.
_PLACEHOLDER_RE: Final = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class CrisisTemplate(BaseModel):
    """One pre-written message, in one language.

    Field-by-field rather than one blob of prose, so the frontend can render the
    emergency instruction and the safety steps as visually distinct blocks (and
    so a test can assert the method-free wording of each part separately).
    """

    title: str = Field(min_length=3, max_length=200)
    #: Short paragraphs, in reading order. Warm, plain, non-judgemental.
    body: list[str] = Field(min_length=1, max_length=8)
    #: Lead-in shown directly above the helpline list.
    helpline_intro: str | None = Field(default=None, max_length=200)
    #: What to do *right now* if in danger. Contains ``{emergency_number}``.
    emergency_instruction: str | None = Field(default=None, max_length=400)
    #: Encouragement to involve one trusted person.
    trusted_person: str | None = Field(default=None, max_length=400)
    #: Concrete, method-free steps. Never describes how anything is done.
    safety_steps: list[str] = Field(default_factory=list, max_length=10)
    closing: str | None = Field(default=None, max_length=400)
    #: The "not therapy, not a crisis service" line, in the same language.
    disclaimer: str | None = Field(default=None, max_length=400)

    @field_validator("body", "safety_steps")
    @classmethod
    def _no_blank_lines(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("template paragraphs must not be blank")
        return value

    def strings(self) -> list[str]:
        """Every human-readable string in this template, in a stable order."""
        collected = [self.title, *self.body]
        for value in (
            self.helpline_intro,
            self.emergency_instruction,
            self.trusted_person,
            self.closing,
            self.disclaimer,
        ):
            if value:
                collected.append(value)
        collected.extend(self.safety_steps)
        return collected


class LocalisedContent(BaseModel):
    """One locale file, validated."""

    version: int = Field(ge=1)
    locale: str = Field(min_length=2, max_length=16)
    #: Native-script name, for a language picker.
    display_name: str = Field(min_length=1, max_length=80)
    #: Placeholders this file is allowed to use. Only ``emergency_number`` today.
    placeholder_whitelist: list[str] = Field(min_length=1)
    templates: dict[str, CrisisTemplate] = Field(min_length=1)

    @field_validator("locale")
    @classmethod
    def _lower_locale(cls, value: str) -> str:
        return value.strip().lower()

    def template(self, template_id: str) -> CrisisTemplate:
        """Look one template up; raises ``KeyError`` if the locale lacks it.

        Callers use :func:`resolve_locale` first, so a missing id is a content bug
        caught at load time rather than a runtime surprise.
        """
        return self.templates[template_id]

    def placeholders(self, template_id: str) -> set[str]:
        """The placeholders one template actually uses.

        Callers ask this before rendering: ``check_in.low`` uses none, and
        :meth:`render` rejects values a template does not ask for.
        """
        used: set[str] = set()
        for text in self.template(template_id).strings():
            used.update(_PLACEHOLDER_RE.findall(text))
        return used

    def render(self, template_id: str, values: Mapping[str, str]) -> RenderedTemplate:
        """Substitute the whitelisted placeholders and return display-ready text.

        ``values`` must cover every placeholder the template actually uses, and
        may not contain anything else: extra keys would mean a template and a
        caller disagreeing about what is allowed to appear in a crisis message.
        """
        template = self.template(template_id)
        allowed = set(self.placeholder_whitelist)
        used = self.placeholders(template_id)

        unknown = sorted(used - allowed)
        if unknown:
            raise ValueError(f"{self.locale}/{template_id} uses unlisted placeholders {unknown}")
        missing = sorted(used - {key for key, value in values.items() if value})
        if missing:
            raise ValueError(f"{self.locale}/{template_id} needs values for {missing}")
        extra = sorted(set(values) - used)
        if extra:
            raise ValueError(f"{self.locale}/{template_id} was given unused values {extra}")

        return RenderedTemplate(
            template_id=template_id,
            locale=self.locale,
            title=_fill(template.title, values),
            body=[_fill(item, values) for item in template.body],
            helpline_intro=_fill_optional(template.helpline_intro, values),
            emergency_instruction=_fill_optional(template.emergency_instruction, values),
            trusted_person=_fill_optional(template.trusted_person, values),
            safety_steps=[_fill(item, values) for item in template.safety_steps],
            closing=_fill_optional(template.closing, values),
            disclaimer=_fill_optional(template.disclaimer, values),
        )


class RenderedTemplate(BaseModel):
    """A template with its placeholders already substituted — safe to display."""

    template_id: str
    locale: str
    title: str
    body: list[str]
    helpline_intro: str | None = None
    emergency_instruction: str | None = None
    trusted_person: str | None = None
    safety_steps: list[str] = Field(default_factory=list)
    closing: str | None = None
    disclaimer: str | None = None

    @field_validator("title", "body", "safety_steps", "helpline_intro", "emergency_instruction")
    @classmethod
    def _no_placeholders_left(cls, value: Any) -> Any:
        """Fail rather than show ``{emergency_number}`` to a person in crisis."""
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str) and _PLACEHOLDER_RE.search(item):
                raise ValueError("rendered template still contains a placeholder")
        return value


def _fill(text: str, values: Mapping[str, str]) -> str:
    """Substitute ``{name}`` placeholders, leaving unknown ones to the validator."""

    class _Safe(dict[str, str]):
        def __missing__(self, key: str) -> str:
            return f"{{{key}}}"

    return text.format_map(_Safe(values))


def _fill_optional(text: str | None, values: Mapping[str, str]) -> str | None:
    return None if text is None else _fill(text, values)


def _locale_files() -> tuple[str, ...]:
    """Every ``*.json`` in the i18n directory, sorted, without the extension.

    Uses ``name.endswith`` rather than ``Path.suffix``: ``importlib.resources``
    hands back a ``Traversable``, which does not promise one.
    """
    directory = files("app.content") / I18N_DIR
    return tuple(
        sorted(
            path.name[: -len(".json")]
            for path in directory.iterdir()
            if path.name.endswith(".json")
        )
    )


def available_locales() -> tuple[str, ...]:
    """Locales that ship with the backend (derived from the files present)."""
    return _locale_files()


def parse_localisation(payload: dict[str, Any]) -> LocalisedContent:
    """Validate one locale payload and check the invariants a model cannot."""
    content = LocalisedContent.model_validate(payload)

    missing = [
        template_id for template_id in REQUIRED_TEMPLATES if template_id not in content.templates
    ]
    if missing:
        raise ValueError(f"locale {content.locale} is missing templates {missing}")

    declared = set(content.placeholder_whitelist)
    for template_id, template in content.templates.items():
        for text in template.strings():
            for name in _PLACEHOLDER_RE.findall(text):
                if name not in declared:
                    raise ValueError(
                        f"locale {content.locale}/{template_id} uses {name!r}, "
                        f"which is not in placeholder_whitelist"
                    )
    return content


@lru_cache(maxsize=1)
def load_all_localisations() -> Mapping[str, LocalisedContent]:
    """Load every locale file once, at first use.

    Cached for the process lifetime: these are shipped files, so there is
    nothing to reload and no reason to touch disk per request.
    """
    directory = files("app.content") / I18N_DIR
    loaded: dict[str, LocalisedContent] = {}
    for name in _locale_files():
        payload = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
        content = parse_localisation(payload)
        if content.locale != name:
            raise ValueError(f"i18n/{name}.json declares locale {content.locale!r}")
        loaded[name] = content

    if FALLBACK_LOCALE not in loaded:
        raise ValueError(f"the fallback locale {FALLBACK_LOCALE!r} must ship with the backend")
    return loaded


def resolve_locale(locale: str | None) -> tuple[str, bool]:
    """Map a requested locale onto a shipped one.

    Accepts BCP-47 tags and strips the region (``hi-IN`` → ``hi``), because the
    copy is the same across regions — the *helplines* are what vary by region,
    and they come from ``helplines.json``. Returns ``(locale, fallback_used)``.
    """
    if locale:
        candidate = locale.strip().lower().split("-")[0]
        if candidate in load_all_localisations():
            return candidate, False
    return FALLBACK_LOCALE, True


def load_localisation(locale: str | None = None) -> LocalisedContent:
    """The content for ``locale``, or English when it is unknown."""
    resolved, _ = resolve_locale(locale)
    return load_all_localisations()[resolved]


__all__ = [
    "FALLBACK_LOCALE",
    "I18N_DIR",
    "REQUIRED_TEMPLATES",
    "CrisisTemplate",
    "LocalisedContent",
    "RenderedTemplate",
    "available_locales",
    "load_all_localisations",
    "load_localisation",
    "parse_localisation",
    "resolve_locale",
]
