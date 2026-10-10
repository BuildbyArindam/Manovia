"""Step 5 — retrieval. A stub today; Day 13 fills it.

The seam is a one-method protocol so the orchestrator never changes when the
vector store arrives. The query it is handed is the **redacted** text: a future
embedding service may be external, and identifiers must not reach it any more
than they reach the LLM (AGENTS.md rule 5).

Whatever a retriever returns is *reference material the application selected*,
not something the user said and not an instruction: the prompt builder frames it
that way (:mod:`app.services.chat.prompting`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RetrievedChunk:
    """One passage of curated self-help content."""

    source_id: str
    title: str
    text: str


@runtime_checkable
class Retriever(Protocol):
    """Anything that can look up reference passages for a (redacted) query."""

    async def retrieve(self, query: str, *, limit: int = 3) -> list[RetrievedChunk]:
        """Return up to ``limit`` passages, best first. Empty is a valid answer."""
        ...


class NullRetriever:
    """Returns nothing. Day 13 replaces it."""

    async def retrieve(self, query: str, *, limit: int = 3) -> list[RetrievedChunk]:
        return []
