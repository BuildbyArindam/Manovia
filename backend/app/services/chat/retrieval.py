"""Retrieval stub — Day 13 fills it.

Today it returns an empty list. The interface is intentionally tiny so that
Day 13 can swap the implementation without touching the orchestrator.

The orchestrator calls :func:`retrieve` with the redacted user text and the
session id; it gets back a list of context snippets. Each snippet is a plain
string today (later: source, score, etc.), and the prompt builder concatenates
them when present.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RetrievalResult:
    """One retrieved snippet. Stub today, richer tomorrow."""

    text: str
    source: str = "stub"
    score: float = 0.0


async def retrieve(
    query: str,
    *,
    session_id: UUID | None = None,
    user_id: UUID | None = None,
    limit: int = 3,
) -> list[RetrievalResult]:
    """Return relevant context for ``query``. Stub: always empty.

    The signature is future-proof: Day 13 will use ``user_id`` to scope
    retrieval to the user's own journal/mood entries when consent allows it.
    """
    _ = (query, session_id, user_id, limit)
    return []


__all__ = ["RetrievalResult", "retrieve"]
