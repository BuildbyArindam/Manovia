"""Validated self-help screenings (PHQ-9, GAD-7).

Split in two on purpose:

* :mod:`app.services.assessments.instruments` — the official wording, the
  response options, the bands and their provenance, as validated content.
* :mod:`app.services.assessments.scoring` — pure functions that turn answers
  into a total, a band and the item-level safety flag.

Neither module knows about HTTP, the database, or a model. The API layer
(:mod:`app.api.v1.assessments`) wires them to storage and to the escalation
policy, which is what keeps "how is this scored?" answerable by reading one
small file.
"""

from app.services.assessments.instruments import (
    ASSESSMENTS_DIR,
    AssessmentContentError,
    Band,
    Cadence,
    Disclaimers,
    InstrumentDefinition,
    Item,
    Provenance,
    ResponseOption,
    Safety,
    Scoring,
    get_instruments,
    load_instruments,
)
from app.services.assessments.scoring import (
    AssessmentAnswerError,
    IncompleteAssessmentError,
    InvalidAnswerError,
    ScoreResult,
    UnknownItemError,
    band_for,
    bands,
    item_ids,
    score,
    score_from_definition,
)

__all__ = [
    "ASSESSMENTS_DIR",
    "AssessmentAnswerError",
    "AssessmentContentError",
    "Band",
    "Cadence",
    "Disclaimers",
    "IncompleteAssessmentError",
    "InstrumentDefinition",
    "InvalidAnswerError",
    "Item",
    "Provenance",
    "ResponseOption",
    "Safety",
    "ScoreResult",
    "Scoring",
    "UnknownItemError",
    "band_for",
    "bands",
    "get_instruments",
    "item_ids",
    "load_instruments",
    "score",
    "score_from_definition",
]
