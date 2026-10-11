"""The shapes the chat pipeline hands around: metadata, replies, stream events.

Everything in here is safe to log and safe to put on the wire. None of it holds
the person's words except :attr:`ChatReply.reply`, which is the assistant's text
(returned to the client, and stored encrypted — never logged).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from app.content.crisis import CrisisResource
from app.content.i18n import RenderedTemplate


class ResponseType(StrEnum):
    """What kind of answer the person got. A closed vocabulary the UI switches on.

    Precedence when more than one applies: ``crisis`` > ``fallback`` >
    ``check_in`` > ``normal``.
    """

    #: The model answered; nothing special applies.
    NORMAL = "normal"
    #: The model answered *and* the deterministic MEDIUM check-in was appended
    #: with the region's helplines.
    CHECK_IN = "check_in"
    #: The pre-written crisis response. The model was **not** called.
    CRISIS = "crisis"
    #: The model could not answer (outage, or the output guard replaced its
    #: text), so a pre-written template stands in for it.
    FALLBACK = "fallback"


class KnowledgeDocRef(BaseModel):
    """A retrieved knowledge document, as the UI's "Learn more" card needs it."""

    #: The document id (``grounding-five-senses``) — the stable handle for
    #: ``GET /api/v1/knowledge/{id}``.
    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=120)


class ChatMetadata(BaseModel):
    """Everything the UI needs besides the words: it renders the CrisisCard from this."""

    #: ``none`` | ``low`` | ``medium`` | ``high`` | ``imminent``.
    risk_level: str
    #: Primary emotion label of the *original* text (``sadness``, ``neutral``, …).
    #: ``None`` on a crisis turn: the pipeline stops before emotion analysis.
    emotion: str | None = None
    response_type: ResponseType
    #: Helplines for the person's region, in display order. Empty below MEDIUM.
    resources: list[CrisisResource] = Field(default_factory=list)
    #: The region's emergency entry, shown above everything else on a crisis card.
    emergency: CrisisResource | None = None
    #: The pre-written crisis copy, verbatim from ``content/i18n``. Only on
    #: ``crisis`` turns. The CrisisCard renders this and never rewords it.
    crisis: RenderedTemplate | None = None
    #: The pre-written MEDIUM check-in copy, only on ``check_in`` turns.
    check_in: RenderedTemplate | None = None
    #: Knowledge documents retrieved for this turn (Day 13), best first —
    #: the UI renders one "Learn more" card per entry. Empty when retrieval
    #: found nothing above the similarity floor (or is disabled).
    retrieved_docs: list[KnowledgeDocRef] = Field(default_factory=list)
    #: The ids of :attr:`retrieved_docs`, kept as a flat list for simple
    #: clients and log-safe telemetry. Kept in lockstep by the validator below.
    retrieved_ids: list[str] = Field(default_factory=list)
    #: True when the reply is a stand-in for a model answer that could not be given.
    degraded: bool = False
    #: True when this turn was written to the database (history saved). Always
    #: ``False`` for an ephemeral session.
    persisted: bool = False
    #: True when the crisis copy addresses a supporter ("my friend says…").
    about_someone_else: bool = False
    locale: str = "en"
    region: str = "DEFAULT"

    @model_validator(mode="after")
    def _retrieved_lists_agree(self) -> ChatMetadata:
        if self.retrieved_ids != [doc.id for doc in self.retrieved_docs]:
            raise ValueError("retrieved_ids must be exactly the ids of retrieved_docs, in order")
        return self


class ChatReply(BaseModel):
    """The orchestrator's answer to one user message."""

    session_id: uuid.UUID
    #: Id of the stored assistant message (``None`` when nothing was stored).
    message_id: uuid.UUID | None = None
    reply: str
    metadata: ChatMetadata


@dataclass(frozen=True)
class TokenEvent:
    """A chunk of the reply, to be appended in order."""

    text: str


@dataclass(frozen=True)
class FinalEvent:
    """The last event of a stream: authoritative text plus metadata.

    ``replaced`` is True when ``reply.reply`` is **not** the concatenation of the
    token events — the output guard rewrote the text, or the model failed
    part-way. A client must then replace what it has rendered with ``reply``.
    """

    reply: ChatReply
    replaced: bool = False


StreamEvent = TokenEvent | FinalEvent
