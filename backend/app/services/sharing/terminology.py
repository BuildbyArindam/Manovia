"""Verified terminology for the FHIR export — and a refusal to guess.

Only two LOINC codes are claimed anywhere in this feature: the PHQ-9 total score
and the GAD-7 total score. Both were looked up on the official LOINC site on
2026-10-19 and copied into ``app/content/sharing/loinc.json`` with their source
URLs:

* ``44261-6`` — Patient Health Questionnaire 9 item (PHQ-9) total score
  [Reported] — https://loinc.org/44261-6
* ``70274-6`` — Generalized anxiety disorder 7 item (GAD-7) total score
  [Reported.PHQ] — https://loinc.org/70274-6

The panel codes (PHQ-9 ``44249-1``, GAD-7 ``69737-5``) identify the
*questionnaire*, not the score, so they are recorded here as ``panel_code`` and
never used as an Observation's ``code``. Everything else the bundle needs —
mood summaries, exercise helpfulness, thought records — is coded in Manovia's own
code system (``app/services/sharing/fhir.py``) rather than borrowing a LOINC term
nobody verified. A wrong code in a clinical document is worse than an honest
local one.

The file is content, not code, for the same reason the instruments and the
helplines are: it is reviewable, it carries its provenance, and the loader fails
loudly if it is malformed rather than exporting an empty bundle.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.models.enums import AssessmentInstrument

#: Where the file lives inside the ``app.content`` package.
SHARING_DIR = "sharing"
LOINC_FILE = "loinc.json"

#: The code system every code in the file belongs to.
LOINC_SYSTEM = "http://loinc.org"


class TerminologyError(ValueError):
    """The shipped terminology file is missing, malformed, or self-contradictory."""


class StrictContent(BaseModel):
    """Strict, frozen, extra-forbidding content model (the Day 18 convention)."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LoincCode(StrictContent):
    """One verified LOINC term, with the page it was read from."""

    key: Literal["phq9_total", "gad7_total"]
    instrument: AssessmentInstrument
    code: str = Field(pattern=r"^\d{1,8}-\d$")
    display: str = Field(min_length=1, max_length=200)
    fully_specified_name: str = Field(min_length=1, max_length=300)
    status: Literal["active"]
    scale: Literal["Qn"]
    method: Literal["Reported.PHQ"]
    #: LOINC's own example UCUM unit for both terms (their pages list ``{score}``).
    ucum_unit: Literal["{score}"]
    source_url: str = Field(min_length=1, max_length=200)
    #: The questionnaire's panel code, recorded so nobody "fixes" the total-score
    #: Observation into a panel code later.
    panel_code: str = Field(pattern=r"^\d{1,8}-\d$")
    panel_source_url: str = Field(min_length=1, max_length=200)

    @field_validator("instrument", mode="before")
    @classmethod
    def _read_instrument(cls, value: object) -> object:
        """JSON carries ``"phq9"``; the model stores the enum member."""

        return AssessmentInstrument(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def source_url_points_at_this_code(self) -> LoincCode:
        """The cited page must be the official page *for this code*."""

        for url, code in ((self.source_url, self.code), (self.panel_source_url, self.panel_code)):
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.netloc != "loinc.org":
                raise ValueError(f"{url} is not an https://loinc.org URL")
            if code not in parsed.path:
                raise ValueError(f"{url} does not cite code {code}")
        return self


class Terminology(StrictContent):
    """The whole file: one code system, one verification date, the codes."""

    schema_version: Literal[1]
    code_system: Literal["http://loinc.org"]
    verified_on: date
    verified_against: Literal["https://loinc.org"]
    note: str = Field(min_length=1)
    codes: list[LoincCode] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def one_code_per_instrument(self) -> Terminology:
        """A duplicate key or instrument would make ``for_instrument`` ambiguous."""

        keys = [code.key for code in self.codes]
        instruments = [code.instrument for code in self.codes]
        if len(set(keys)) != len(keys) or len(set(instruments)) != len(instruments):
            raise ValueError("codes must be unique by key and by instrument")
        return self

    def for_instrument(self, instrument: AssessmentInstrument) -> LoincCode:
        """The total-score term for ``instrument``. Raises if it is not there."""

        for code in self.codes:
            if code.instrument is instrument:
                return code
        raise TerminologyError(f"no verified LOINC total-score code for {instrument}")


def load_terminology(directory: Path | None = None) -> Terminology:
    """Read and validate the shipped file. Raises :class:`TerminologyError`."""

    root = directory if directory is not None else Path(str(files("app.content") / SHARING_DIR))
    path = root / LOINC_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TerminologyError(f"{path}: {exc}") from exc
    try:
        return Terminology.model_validate(raw)
    except ValidationError as exc:
        raise TerminologyError(f"{path}: {exc}") from exc


@lru_cache(maxsize=4)
def get_terminology(directory: Path | None = None) -> Terminology:
    """The shipped terminology, validated once per process (release content)."""

    return load_terminology(directory)


def loinc_for(instrument: AssessmentInstrument) -> LoincCode:
    """Shorthand for the one call the FHIR builder makes."""

    return get_terminology().for_instrument(instrument)


__all__ = [
    "LOINC_FILE",
    "LOINC_SYSTEM",
    "SHARING_DIR",
    "LoincCode",
    "Terminology",
    "TerminologyError",
    "get_terminology",
    "load_terminology",
    "loinc_for",
]
