"""Shared vocabulary for the RAG service: the embedder seam and chunk records.

The knowledge base serves *reference material the application selected* — the
same trust boundary the chat retriever protocol established on Day 11. Nothing
in this package ever sees the user's raw words: the orchestrator hands the
retriever the redacted message (AGENTS.md rule 5), and the only text stored
here is curated content written by us.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable


class EmbedderError(RuntimeError):
    """An embedding could not be produced."""


class EmbedderUnavailable(EmbedderError):
    """The requested embedder is not usable here (extra missing, model absent)."""


@dataclass(frozen=True)
class EmbeddingVector:
    """One embedding with the signature of the model that produced it."""

    values: list[float]
    #: Recorded in the store manifest, so vectors from two different embedders
    #: can never be mixed in one index (a change forces a full rebuild).
    embedder: str
    dim: int


@runtime_checkable
class Embedder(Protocol):
    """Anything that can turn text into fixed-length vectors, in order."""

    #: Short identity string stored in the ingest manifest ("hashing-v1",
    #: "sentence-transformers:<model id>"). Two embedders must never share one.
    name: str

    #: Vector length. The store is built for one dim; vectors are checked
    #: before insertion. 0 means "known only after loading".
    dim: int

    def resolve_dim(self) -> int:
        """The vector length, loading the model if that is what it takes.

        Ingest calls this before embedding anything (the idempotence
        signature records the dim), so a lazy embedder may pay its load here.
        """
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts``, returning one L2-normalised vector per input."""
        ...


#: The metadata every chunk carries in the vector store. Keys are deliberately
#: short and flat — they are stored per chunk and filtered per query.
@dataclass(frozen=True)
class ChunkMetadata:
    doc_id: str
    title: str
    chunk_index: int
    chunk_total: int
    language: str
    tags: tuple[str, ...] = ()
    evidence_note: str = ""

    def as_dict(self) -> dict[str, str]:
        """The flat form stored alongside each vector. Tags join on commas."""
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "chunk_index": str(self.chunk_index),
            "chunk_total": str(self.chunk_total),
            "language": self.language,
            "tags": ",".join(self.tags),
            "evidence_note": self.evidence_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> ChunkMetadata:
        return cls(
            doc_id=data["doc_id"],
            title=data["title"],
            chunk_index=int(data["chunk_index"]),
            chunk_total=int(data["chunk_total"]),
            language=data["language"],
            tags=tuple(t for t in data.get("tags", "").split(",") if t),
            evidence_note=data.get("evidence_note", ""),
        )


#: A chunk id is ``<doc id>#<chunk index>``, so a chunk is traceable to its
#: document from the id alone — the id the chat metadata shows the UI.
CHUNK_ID_SEP: Final = "#"


def chunk_id(doc_id: str, chunk_index: int) -> str:
    return f"{doc_id}{CHUNK_ID_SEP}{chunk_index:02d}"


@dataclass
class StoredChunk:
    """One chunk as the store keeps it: id, text, metadata, row position."""

    id: str
    text: str
    metadata: ChunkMetadata
    #: Row number inside the FAISS index; rebuilt on every ingest.
    row: int = field(default=-1)


__all__ = [
    "CHUNK_ID_SEP",
    "ChunkMetadata",
    "Embedder",
    "EmbedderError",
    "EmbedderUnavailable",
    "EmbeddingVector",
    "StoredChunk",
    "chunk_id",
]
