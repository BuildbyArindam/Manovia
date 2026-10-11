"""The vector store: FAISS over normalised vectors, with JSON sidecar metadata.

The brief allows Chroma or FAISS. FAISS was chosen (see
``docs/adr/0013-knowledge-retrieval-rag.md``): one small CPU wheel, no
telemetry, no server process, and the collection sizes here are tiny (40
documents → a few hundred chunks), where an exact flat index is both simpler
and *more* correct than an approximate one.

Design:

* Vectors are L2-normalised before insertion and the index is ``IndexFlatIP``,
  so a search score **is** the cosine similarity — the same number the
  retriever's threshold compares against.
* Metadata lives in a JSON sidecar (``store.json``), keyed by chunk id. FAISS
  rows map to ids by position; the sidecar also holds the manifest facts
  (embedder identity, dimension) that decide whether a stored index can be
  reused at all.
* Writes are build-elsewhere-then-swap: the ingest builds a complete new
  index and replaces the files, so an interrupted write can never leave a
  half-populated store that would silently answer queries badly.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import faiss
import numpy as np
import structlog

from app.services.rag.base import CHUNK_ID_SEP, ChunkMetadata, StoredChunk

_log = structlog.get_logger()

INDEX_FILE = "index.faiss"
SIDECAR_FILE = "store.json"

SIDECAR_VERSION = 1


@dataclass(frozen=True)
class Hit:
    """One query result: the chunk id, its cosine similarity, its metadata."""

    id: str
    score: float
    metadata: ChunkMetadata


class VectorStoreError(RuntimeError):
    """The store is unusable (unreadable index, mismatched dim, corrupt JSON)."""


class FaissVectorStore:
    """An exact-nearest-neighbour store over cosine similarity, with metadata.

    Used by the ingest (build + save) and the retriever (load + query). A
    store loaded from an empty or missing directory is a valid empty store:
    the retriever answers ``[]`` and the chat proceeds without reference
    material, which is the correct degradation for an un-ingested deployment.
    """

    def __init__(self, dim: int) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim
        self._index = faiss.IndexFlatIP(dim)
        self._chunks: dict[str, StoredChunk] = {}
        self._rows: list[str] = []

    # ------------------------------------------------------------- build #

    def add(
        self, chunk_id: str, vector: Sequence[float], metadata: ChunkMetadata, text: str
    ) -> None:
        """Append one chunk. Ids must be unique; vector length must match dim."""
        if chunk_id in self._chunks:
            raise VectorStoreError(f"duplicate chunk id {chunk_id!r}")
        vec = np.asarray(vector, dtype="float32")
        if vec.shape != (self.dim,):
            raise VectorStoreError(
                f"vector for {chunk_id!r} has shape {vec.shape}, want ({self.dim},)"
            )
        self._index.add(vec.reshape(1, -1))
        row = len(self._rows)
        self._rows.append(chunk_id)
        self._chunks[chunk_id] = StoredChunk(id=chunk_id, text=text, metadata=metadata, row=row)

    # ------------------------------------------------------------- query #

    def query(
        self,
        vector: Sequence[float],
        *,
        k: int,
        where: dict[str, str] | None = None,
    ) -> list[Hit]:
        """The ``k`` best chunks whose metadata matches ``where``, best first.

        The search is exact: every row is scored (cheap at corpus scale), then
        metadata-filtered, so a filter can never hide a hit behind an
        approximate-index artefact.
        """
        if self.count() == 0:
            return []
        if k < 1:
            return []
        vec = np.asarray(vector, dtype="float32").reshape(1, -1)
        if vec.shape[1] != self.dim:
            raise VectorStoreError(f"query vector has dim {vec.shape[1]}, want {self.dim}")
        scores, rows = self._index.search(vec, self.count())
        hits: list[Hit] = []
        for score, row in zip(scores[0], rows[0], strict=False):
            if row < 0:  # faiss pads short result lists with -1
                break
            chunk_id = self._rows[int(row)]
            chunk = self._chunks[chunk_id]
            if where is not None and not _matches(chunk.metadata, where):
                continue
            hits.append(Hit(id=chunk_id, score=float(score), metadata=chunk.metadata))
            if len(hits) == k:
                break
        return hits

    def count(self) -> int:
        return len(self._rows)

    def chunk_ids(self) -> set[str]:
        return set(self._chunks)

    def text_of(self, chunk_id: str) -> str:
        return self._chunks[chunk_id].text

    # --------------------------------------------------------- persist #

    def save(self, directory: Path) -> None:
        """Write index + sidecar atomically: both to temp files, then swap.

        The sidecar is written first and the index last: a crash between the
        two leaves a sidecar that describes chunks the index does not have,
        and :meth:`load` refuses that mismatch rather than trusting it.
        """
        directory.mkdir(parents=True, exist_ok=True)
        sidecar = {
            "version": SIDECAR_VERSION,
            "dim": self.dim,
            "rows": [
                {
                    "id": chunk.id,
                    "text": chunk.text,
                    "row": chunk.row,
                    **chunk.metadata.as_dict(),
                }
                for chunk in (self._chunks[cid] for cid in self._rows)
            ],
        }
        sidecar_tmp = directory / f"{SIDECAR_FILE}.tmp"
        sidecar_tmp.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
        index_tmp = directory / f"{INDEX_FILE}.tmp"
        faiss.write_index(self._index, str(index_tmp))
        os.replace(sidecar_tmp, directory / SIDECAR_FILE)
        os.replace(index_tmp, directory / INDEX_FILE)

    @classmethod
    def load(cls, directory: Path) -> FaissVectorStore:
        """Load a persisted store, or an empty store if none exists.

        Raises :class:`VectorStoreError` for a store that exists but cannot be
        trusted (missing sidecar, dim mismatch, short row list) — a silently
        wrong reference store is worse than a loudly missing one.
        """
        index_path = directory / INDEX_FILE
        sidecar_path = directory / SIDECAR_FILE
        if not index_path.exists() and not sidecar_path.exists():
            return cls(dim=1)  # placeholder; an empty store's dim is meaningless

        if not index_path.exists() or not sidecar_path.exists():
            raise VectorStoreError(
                f"store in {directory} is incomplete: index and sidecar must exist together"
            )
        index = faiss.read_index(str(index_path))
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        if int(data.get("version", 0)) != SIDECAR_VERSION:
            raise VectorStoreError(
                f"store in {directory} has sidecar version {data.get('version')!r}, "
                f"want {SIDECAR_VERSION}; re-run the ingest"
            )
        store = cls(dim=int(index.d))
        # Keep the loaded vectors (faiss.read_index returns the general Index
        # type; the flat IP index we wrote is what actually comes back).
        store._index = cast("faiss.IndexFlatIP", index)
        rows: list[dict[str, object]] = data.get("rows", [])
        if index.ntotal != len(rows):
            raise VectorStoreError(
                f"store in {directory} is inconsistent: index holds {index.ntotal} "
                f"vectors but the sidecar describes {len(rows)}"
            )
        for position, row in enumerate(rows):
            meta = ChunkMetadata.from_dict({k: str(v) for k, v in row.items()})
            chunk = StoredChunk(
                id=str(row["id"]),
                text=str(row["text"]),
                metadata=meta,
                row=position,
            )
            store._chunks[chunk.id] = chunk
            store._rows.append(chunk.id)
        if store.dim == 1 and index.ntotal > 0:  # pragma: no cover - defensive
            raise VectorStoreError("store has vectors but dim 1")
        return store


def _matches(metadata: ChunkMetadata, where: dict[str, str]) -> bool:
    for key, want in where.items():
        have: str | None = None
        if key == "language":
            have = metadata.language
        elif key == "doc_id":
            have = metadata.doc_id
        elif key == "tag":
            have = want if want in metadata.tags else None
        else:
            have = None
        if have != want:
            return False
    return True


def doc_id_of_chunk(chunk_id: str) -> str:
    """The document id from a ``<doc>#<n>`` chunk id."""
    return chunk_id.split(CHUNK_ID_SEP, 1)[0]


__all__ = ["FaissVectorStore", "Hit", "VectorStoreError", "doc_id_of_chunk"]
