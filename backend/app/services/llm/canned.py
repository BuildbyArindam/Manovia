"""The terminal link in the fallback chain: a canned, supportive reply.

When Anthropic is unreachable, the key is missing, Ollama is not running, and
the deadline is spent, the companion still has to say *something*. What it says
must be:

* **generic** — it never quotes, paraphrases or reacts to what the person
  wrote, because a template cannot know whether its reaction is appropriate and
  a wrong reaction at the wrong moment is worse than none;
* **honest** — it says the reply is a fallback rather than pretending to have
  understood;
* **safe** — it offers no advice, names no method, promises nothing, and points
  at human help;
* **reviewable** — it is one string in this file, which means the exact words a
  person sees when the system is broken are in the repository and in a diff.

It is deliberately *not* warm-and-chatty: a degraded system should not
manufacture intimacy it cannot follow up on.

This provider cannot fail, which is why the chain can promise never to raise.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from app.services.llm.base import LLMMessage, LLMProvider, LLMResult, LLMUsage

#: The reply. Plain, short, no greeting garnish, no fake understanding.
CANNED_REPLY: str = (
    "I'm having trouble gathering my thoughts right now, so I can't give you a "
    "proper reply — that's a limitation on my side, not a comment on what you "
    "said. I'm here and I'm listening. If you'd like, write to me again in a "
    "moment. If things feel heavy or unsafe, please reach out to a crisis "
    "helpline or someone you trust tonight; you shouldn't have to sit with it "
    "alone."
)

#: Roughly how many words to yield per streamed chunk. Small, so the client
#: starts rendering immediately even on the degraded path.
CANNED_CHUNK_WORDS: int = 6


class CannedProvider(LLMProvider):
    """A provider that always answers with :data:`CANNED_REPLY`."""

    name = "canned"

    @property
    def model_id(self) -> str | None:
        return None

    @property
    def is_configured(self) -> bool:
        return True

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> LLMResult:
        return LLMResult(
            text=CANNED_REPLY,
            provider=self.name,
            model=None,
            finish_reason="canned",
            usage=LLMUsage(input_tokens=None, output_tokens=None),
            latency_ms=0.0,
            degraded=True,
        )

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        system: str | None = None,
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        for chunk in split_for_stream(CANNED_REPLY):
            yield chunk
            # Yield control so the event loop actually interleaves; without it
            # a synchronous generator would deliver every chunk at once.
            await asyncio.sleep(0)

    def describe(self) -> dict[str, object]:
        return {"provider": self.name, "model": None, "configured": True, "terminal": True}


def split_for_stream(text: str, words_per_chunk: int = CANNED_CHUNK_WORDS) -> list[str]:
    """Split ``text`` into word-boundary chunks, keeping the trailing spaces.

    Splitting on spaces (not characters) means the concatenated chunks are byte
    identical to the original — a client that joins them gets exactly the same
    string it would have got from :meth:`CannedProvider.complete`.
    """
    pieces = text.split(" ")
    if not pieces:
        return []
    chunks: list[str] = []
    for index in range(0, len(pieces), words_per_chunk):
        group = pieces[index : index + words_per_chunk]
        chunk = " ".join(group)
        if index + words_per_chunk < len(pieces):
            chunk += " "
        chunks.append(chunk)
    return chunks
