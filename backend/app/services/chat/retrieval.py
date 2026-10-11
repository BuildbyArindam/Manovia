"""Step 5 — retrieval: reference material for the prompt, behind one seam.

The seam is a one-method protocol so the orchestrator never changes when the
vector store changes. Day 13's implementation lives in
:mod:`app.services.rag`; this module keeps the contract both sides share.

The query it is handed is the **redacted** text: the embedding side may be
external someday, and identifiers must not reach it any more than they reach
the LLM (AGENTS.md rule 5). ``language`` is the user's locale for this turn,
so the retriever can filter the corpus by document language; ``None`` means
"no preference".

Whatever a retriever returns is *reference material the application selected*,
not something the user said and not an instruction: the prompt builder frames it
that way (:mod:`app.services.chat.prompting`). Empty is a normal answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RetrievedChunk:
    """One passage of curated self-help content.

    ``source_id`` is the knowledge document's id — the stable handle the chat
    metadata shows the UI for its "Learn more" cards.
    """

    source_id: str
    title: str
    text: str


@runtime_checkable
class Retriever(Protocol):
    """Anything that can look up reference passages for a (redacted) query."""

    async def retrieve(
        self, query: str, *, limit: int = 3, language: str | None = None
    ) -> list[RetrievedChunk]:
        """Return up to ``limit`` passages, best first. Empty is a valid answer."""
        ...


class NullRetriever:
    """Returns nothing: retrieval disabled, or nothing ingested yet."""

    async def retrieve(
        self, query: str, *, limit: int = 3, language: str | None = None
    ) -> list[RetrievedChunk]:
        return []
