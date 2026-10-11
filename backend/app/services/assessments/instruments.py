"""The two validated screening instruments, as reviewable content.

The item wording, the response options, the severity bands and the provenance of
both instruments live in ``app/content/assessments/{phq9,gad7}.json``. They are
data, not code, for the same reason the safety patterns are: the one thing that
must never drift silently is a sentence somebody is asked to answer about their
own life. A changed item is a visible diff in a reviewable file, validated
strictly at load time — a malformed band table rejects the instrument rather than
scoring it wrongly.

Two rules this module enforces so a reviewer does not have to:

* **Every answer option is 0-3 and every item is required.** Missing answers are
  rejected, never imputed (see :mod:`app.services.assessments.scoring`).
* **Bands tile the whole range exactly.** No gap, no overlap, starting at
  ``total_min`` and ending at ``total_max``. A hole in a band table is a total
  score that maps to nothing.

The safety flag on PHQ-9 item 9 is content too: ``creates_crisis_response`` marks
the one item whose *any* non-zero answer must trigger the crisis flow regardless
of the total (see :mod:`app.services.assessments.scoring`).
"""

from __future__ import annotations

import itertools
import json
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.models.enums import AssessmentBand, AssessmentInstrument

#: Content directory inside the ``app.content`` package.
ASSESSMENTS_DIR = "assessments"

#: A stable, URL-safe item id: ``phq9_1``. The instrument prefix is deliberate,
#: so a stored ``answers`` document is readable without knowing which row it
#: belongs to.
ItemId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*_[0-9]{1,2}$")]

#: Answer values are exactly the four options official scoring assigns.
AnswerValue = Literal[0, 1, 2, 3]


class ContentModel(BaseModel):
    """Strict, immutable, extra-forbidding content model (the Day 18 convention).

    ``strict=True`` is what keeps ``"3"`` from being read as the answer ``3``, and
    what makes a truthy value fail loudly instead of scoring a point. The two
    enum-typed fields accept the enum's *string value* through an explicit
    before-validator, because JSON has no enum type and the alternative — turning
    strict mode off globally — would cost the guarantee that matters here.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ResponseOption(ContentModel):
    """One labelled answer option: ``Not at all`` is option 0, and so on."""

    value: AnswerValue
    label: str = Field(min_length=1, max_length=120)
    detail: str | None = Field(default=None, max_length=200)


class Item(ContentModel):
    """One question. ``text`` is the official wording, unaltered."""

    id: ItemId
    position: int = Field(ge=1, le=99)
    text: str = Field(min_length=1, max_length=400)
    #: True on PHQ-9 item 9: any answer above 0 must open the crisis flow.
    creates_crisis_response: bool = False


class Band(ContentModel):
    """A severity band plus the plain-language copy the result page shows."""

    id: AssessmentBand
    min: int = Field(ge=0)
    max: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=60)
    explanation: str = Field(min_length=1, max_length=1200)
    what_helps: list[str] = Field(min_length=1, max_length=8)
    when_to_seek_help: str = Field(min_length=1, max_length=1200)

    @field_validator("id", mode="before")
    @classmethod
    def _read_band_id(cls, value: object) -> object:
        """JSON carries ``"minimal"``; the model stores the enum member."""
        return AssessmentBand(value) if isinstance(value, str) else value


class Scoring(ContentModel):
    """How the item answers become a total, and what the total means."""

    method: Literal["sum"]
    total_min: int = Field(ge=0)
    total_max: int = Field(gt=0)
    bands: list[Band] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def bands_tile_the_range(self) -> Self:
        """Refuse a band table with a gap, an overlap, or a missing edge."""
        ordered = sorted(self.bands, key=lambda band: band.min)
        if ordered[0].min != self.total_min:
            raise ValueError("bands must start at total_min")
        if ordered[-1].max != self.total_max:
            raise ValueError("bands must end at total_max")
        for previous, current in itertools.pairwise(ordered):
            if current.min != previous.max + 1:
                raise ValueError("bands must be contiguous and non-overlapping")
        if len({band.id for band in self.bands}) != len(self.bands):
            raise ValueError("band ids must be unique")
        return self


class Safety(ContentModel):
    """The non-negotiable item-level rule, and the copy that goes with it.

    PHQ-9 item 9 asks about thoughts of being better off dead or of hurting
    oneself. A response of ``1`` or more is treated as a crisis signal on its
    own, whatever the total says — an average hides exactly the answer that
    matters most, and the official instruction manual says the same thing about
    following a positive item 9 up rather than reading the total alone.
    """

    item_id: ItemId
    rule: Literal["answer_greater_than_zero"]
    level: Literal["high"]
    rationale_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z0-9_]+$")]
    explanation: str = Field(min_length=1, max_length=1200)
    encouragement: str = Field(min_length=1, max_length=1200)
    paired_with_score: str = Field(min_length=1, max_length=600)


class Cadence(ContentModel):
    """The gentle re-screening suggestion. Never a block, only a sentence."""

    recommended_interval_days: int = Field(ge=1, le=365)
    first_notice: str = Field(min_length=1, max_length=600)
    due_notice: str = Field(min_length=1, max_length=600)
    recent_notice: str = Field(min_length=1, max_length=1200)


class Disclaimers(ContentModel):
    """The two sentences every result carries, in every band."""

    screening_not_diagnosis: str = Field(min_length=1, max_length=600)
    how_to_read: str = Field(min_length=1, max_length=600)


class Provenance(ContentModel):
    """Where the instrument came from and what its terms allow.

    This is the field a reviewer reads when they ask "are we allowed to show this
    wording, and is it the official one?" — so every claim here names its URL and
    the date it was checked.
    """

    instrument_id: AssessmentInstrument
    developers: str = Field(min_length=1, max_length=400)
    rights_holder: str = Field(min_length=1, max_length=400)
    licence_statement: str = Field(min_length=1, max_length=400)
    licence_source_url: str = Field(min_length=1, max_length=400)
    availability_note: str = Field(min_length=1, max_length=1200)
    item_wording_source_url: str = Field(min_length=1, max_length=400)
    item_wording_verified_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    item_wording_verification: str = Field(min_length=1, max_length=1200)
    scoring_source_url: str = Field(min_length=1, max_length=400)
    scoring_source_quote: str = Field(min_length=1, max_length=400)
    band_verification_note: str = Field(min_length=1, max_length=1200)
    citation: str = Field(min_length=1, max_length=400)
    loinc: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    loinc_verified_on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    disclaimer: str = Field(min_length=1, max_length=600)

    @field_validator("instrument_id", mode="before")
    @classmethod
    def _read_instrument_id(cls, value: object) -> object:
        """JSON carries ``"phq9"``; the model stores the enum member."""
        return AssessmentInstrument(value) if isinstance(value, str) else value


class InstrumentDefinition(ContentModel):
    """One complete screening instrument."""

    schema_version: Literal[1]
    instrument: AssessmentInstrument
    name: str = Field(min_length=1, max_length=120)
    short_name: str = Field(min_length=1, max_length=40)
    measures: str = Field(min_length=1, max_length=200)
    intro: str = Field(min_length=1, max_length=400)
    timeframe_days: int = Field(ge=1, le=365)
    timeframe_label: str = Field(min_length=1, max_length=80)
    response_options: list[ResponseOption] = Field(min_length=4, max_length=4)
    items: list[Item] = Field(min_length=2, max_length=40)
    scoring: Scoring
    safety: Safety | None = None
    cadence: Cadence
    disclaimers: Disclaimers
    provenance: Provenance

    @field_validator("instrument", mode="before")
    @classmethod
    def _read_instrument(cls, value: object) -> object:
        """JSON carries ``"phq9"``; the model stores the enum member."""
        return AssessmentInstrument(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def instrument_is_coherent(self) -> Self:
        """The cross-checks a reviewer would otherwise have to do by eye."""
        if self.provenance.instrument_id != self.instrument:
            raise ValueError("provenance.instrument_id must match instrument")
        positions = [item.position for item in self.items]
        if positions != list(range(1, len(self.items) + 1)):
            raise ValueError("item positions must be 1..n in order")
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("item ids must be unique")
        prefix = f"{self.instrument}_"
        if any(not item.id.startswith(prefix) for item in self.items):
            raise ValueError(f"every item id must start with {prefix!r}")
        if sorted(option.value for option in self.response_options) != [0, 1, 2, 3]:
            raise ValueError("response options must be exactly 0, 1, 2 and 3")
        if len({option.label for option in self.response_options}) != 4:
            raise ValueError("response option labels must be distinct")
        expected_total = 3 * len(self.items)
        if self.scoring.total_min != 0 or self.scoring.total_max != expected_total:
            raise ValueError(f"total range must be 0..{expected_total} for {len(self.items)} items")
        if self.safety is not None:
            flagged = [item.id for item in self.items if item.creates_crisis_response]
            if flagged != [self.safety.item_id]:
                raise ValueError("exactly the safety item must carry creates_crisis_response")
        elif any(item.creates_crisis_response for item in self.items):
            raise ValueError("an item is flagged without a safety block to explain it")
        return self

    def item(self, item_id: str) -> Item | None:
        """One item by id, or ``None``."""
        return next((item for item in self.items if item.id == item_id), None)

    def band_for(self, total: int) -> Band:
        """The band a total falls in. Raises ``ValueError`` outside the range."""
        for band in self.scoring.bands:
            if band.min <= total <= band.max:
                return band
        raise ValueError(f"total {total} is outside 0..{self.scoring.total_max}")


class AssessmentContentError(ValueError):
    """Missing, ambiguous, or invalid instrument content."""


def load_instruments(
    directory: Path | None = None,
) -> dict[AssessmentInstrument, InstrumentDefinition]:
    """Load and validate every instrument file. Raises on the first bad file.

    Nothing is skipped silently: an instrument that fails validation is a
    startup failure, because a screening tool that quietly disappears is worse
    than one that refuses to load.
    """
    root = directory if directory is not None else Path(str(files("app.content") / ASSESSMENTS_DIR))
    paths = sorted(root.glob("*.json"))
    if not paths:
        raise AssessmentContentError(f"no assessment content in {root}")
    loaded: dict[AssessmentInstrument, InstrumentDefinition] = {}
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            definition = InstrumentDefinition.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise AssessmentContentError(f"{path.name}: {exc}") from exc
        if definition.instrument in loaded:
            raise AssessmentContentError(f"duplicate instrument {definition.instrument}")
        loaded[definition.instrument] = definition
    missing = set(AssessmentInstrument) - set(loaded)
    if missing:
        raise AssessmentContentError(f"missing instruments: {sorted(missing)}")
    return loaded


@lru_cache(maxsize=4)
def get_instruments(
    directory: Path | None = None,
) -> dict[AssessmentInstrument, InstrumentDefinition]:
    """The shipped instruments, validated once per process.

    Cached because this is release content validated by strict models and never
    edited at runtime — the same reasoning as the exercise catalogue (Day 18).
    ``directory`` is a keyword-only parameter of a hashable type, so no cache key
    can ever be a message or a user id.
    """
    return load_instruments(directory)
