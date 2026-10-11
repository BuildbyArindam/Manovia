"""Idempotent ingestion: markdown corpus → chunks → embeddings → FAISS store.

Run with ``make ingest`` (``python -m app.services.rag.ingest`` from
``backend/``). The run is **idempotent** in the strong sense: ingesting the
same corpus twice with the same embedder writes nothing the second time, and
reports that plainly::

    knowledge docs: 40   chunks: 168
    embedder: hashing-v1:384   store: ./data/rag
    added: 0   updated: 0   removed: 0   duplicates: 0
    store already up to date

How idempotence is decided: the manifest (``manifest.json``) records, per
document, the SHA-256 of its content, plus the embedder identity, the vector
dimension and the chunker version. If the freshly computed facts match the
manifest and the store on disk is consistent with them, the run is a no-op.
Any difference — an edited document, a changed embedder, a changed chunker, a
missing store — rebuilds the whole store from scratch and atomically replaces
the files. Rebuild-over-patch is deliberate: with a corpus this small it
costs seconds, and it can never leave an index that mixes two embedding
spaces or forgets a deleted chunk.

Deletion works through the same rule: remove a markdown file, re-ingest, and
the store is rebuilt without it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from app.content.knowledge import KnowledgeDoc, load_knowledge_docs
from app.services.rag.base import ChunkMetadata, Embedder, EmbedderUnavailable, chunk_id
from app.services.rag.chunking import chunk_text
from app.services.rag.embedders import (
    EMBEDDER_MARKER_FILE,
    build_embedder_factory,
    embedder_marker,
)
from app.services.rag.store import FaissVectorStore

_log = structlog.get_logger()

MANIFEST_FILE = "manifest.json"
MANIFEST_VERSION = 1

#: Bump when chunk_text's behaviour changes, so an upgraded chunker forces a
#: rebuild instead of silently keeping chunks split the old way.
CHUNKER_VERSION = "words-v2"  # v2: embed_text anchors the title twice


@dataclass(frozen=True)
class PlannedChunk:
    """One chunk as ingest plans it, before anything is embedded."""

    id: str
    text: str
    #: What gets embedded: the title joined to the chunk text, so a query can
    #: hit a document through its title as well as its body.
    embed_text: str
    metadata: ChunkMetadata


@dataclass(frozen=True)
class IngestPlan:
    """The full set of chunks one ingest run would store, plus doc hashes."""

    chunks: tuple[PlannedChunk, ...]
    doc_hashes: dict[str, str]

    @property
    def chunk_ids(self) -> set[str]:
        return {c.id for c in self.chunks}


@dataclass(frozen=True)
class IngestReport:
    """What one ingest run did. ``added``/``updated``/``removed`` are chunk counts."""

    docs: int
    chunks: int
    added: int
    updated: int
    removed: int
    duplicates: int
    embedder: str
    dim: int
    persist_dir: str
    changed: bool

    def summary(self) -> str:
        state = "rebuilt" if self.changed else "already up to date"
        return (
            f"knowledge docs: {self.docs}   chunks: {self.chunks}\n"
            f"embedder: {self.embedder}   dim: {self.dim}   store: {self.persist_dir}\n"
            f"added: {self.added}   updated: {self.updated}   removed: {self.removed}   "
            f"duplicates: {self.duplicates}\n"
            f"store {state}"
        )


def plan_ingest(
    docs: tuple[KnowledgeDoc, ...], *, chunk_words: int, overlap_words: int
) -> IngestPlan:
    """Chunk every document and hash every document. Pure; the diff's input."""
    planned: list[PlannedChunk] = []
    hashes: dict[str, str] = {}
    for doc in docs:
        pieces = chunk_text(doc.body, target_words=chunk_words, overlap_words=overlap_words)
        if not pieces:  # the loader's length floor makes this near-impossible
            raise ValueError(f"{doc.id}: chunker produced no chunks")
        total = len(pieces)
        if total > 100:
            raise ValueError(f"{doc.id}: {total} chunks is suspiciously many; check chunk_words")
        hashes[doc.id] = _doc_hash(doc)
        for index, piece in enumerate(pieces):
            planned.append(
                PlannedChunk(
                    id=chunk_id(doc.id, index),
                    text=piece,
                    # The title carries the curated topical terms (e.g. the
                    # word "sleep" in a sleep doc), so it is embedded twice:
                    # once more than the body, it anchors the chunk's topic
                    # without changing the text users are shown.
                    embed_text=f"{doc.title}\n{doc.title}\n\n{piece}",
                    metadata=ChunkMetadata(
                        doc_id=doc.id,
                        title=doc.title,
                        chunk_index=index,
                        chunk_total=total,
                        language=doc.language,
                        tags=doc.tags,
                        evidence_note=doc.evidence_note,
                    ),
                )
            )
    ids = [c.id for c in planned]
    duplicates = len(ids) - len(set(ids))
    if duplicates:
        raise ValueError(f"{duplicates} duplicate chunk ids produced; refusing to ingest")
    return IngestPlan(chunks=tuple(planned), doc_hashes=hashes)


def run_ingest(
    docs: tuple[KnowledgeDoc, ...],
    *,
    persist_dir: Path,
    embedder_factory: Callable[[Sequence[str]], Embedder],
    embedder_config: dict[str, str | int],
    chunk_words: int = 220,
    overlap_words: int = 50,
) -> IngestReport:
    """Ingest the corpus into ``persist_dir``, idempotently. See module doc.

    ``embedder_factory`` receives the planned chunk texts (the TF-IDF
    embedder fits on them; the other kinds ignore them). ``embedder_config``
    is the identity of the *configuration* — kind, model id, dim — and is
    what the no-op comparison uses, so the up-to-date path never pays for a
    fit or a model load.
    """
    plan = plan_ingest(docs, chunk_words=chunk_words, overlap_words=overlap_words)
    signature = {
        "version": MANIFEST_VERSION,
        "chunker": CHUNKER_VERSION,
        "embedder": embedder_config,
        "chunk_words": chunk_words,
        "overlap_words": overlap_words,
        "docs": plan.doc_hashes,
    }

    previous = _load_manifest(persist_dir)
    old_ids: set[str] = previous["chunk_ids"] if previous else set()
    up_to_date = (
        previous is not None
        and previous["signature"] == signature
        and previous["chunk_ids"] == plan.chunk_ids
        and _store_is_intact(persist_dir, expected_ids=plan.chunk_ids)
    )
    if up_to_date:
        assert previous is not None
        fitted: dict[str, object] = previous["fitted"]
        name = str(fitted.get("name")) if fitted.get("name") else str(embedder_config["kind"])
        raw_dim = fitted.get("dim", 0)
        return IngestReport(
            docs=len(docs),
            chunks=len(plan.chunks),
            added=0,
            updated=0,
            removed=0,
            duplicates=0,
            embedder=name,
            dim=raw_dim if isinstance(raw_dim, int) else 0,
            persist_dir=str(persist_dir),
            changed=False,
        )

    embedder = embedder_factory([c.embed_text for c in plan.chunks])
    dim = embedder.resolve_dim()
    if dim <= 0:
        raise ValueError(f"embedder {embedder.name!r} reports dim {dim}")
    vectors = embedder.embed([c.embed_text for c in plan.chunks])
    if len(vectors) != len(plan.chunks):
        raise ValueError(f"embedder returned {len(vectors)} vectors for {len(plan.chunks)} chunks")
    for vector in vectors:
        if len(vector) != dim:
            raise ValueError(f"embedder returned dim {len(vector)}, reported {dim}")

    store = FaissVectorStore(dim=dim)
    for chunk, vector in zip(plan.chunks, vectors, strict=True):
        store.add(chunk.id, vector, chunk.metadata, chunk.text)
    store.save(persist_dir)
    _save_embedder_state(persist_dir, embedder)
    _write_embedder_marker(persist_dir, embedder_config, fitted=embedder)
    _write_manifest(
        persist_dir,
        signature=signature,
        chunk_ids=sorted(plan.chunk_ids),
        fitted={"name": embedder.name, "dim": dim},
    )

    new_ids = plan.chunk_ids
    return IngestReport(
        docs=len(docs),
        chunks=len(plan.chunks),
        added=len(new_ids - old_ids),
        updated=len(new_ids & old_ids),
        removed=len(old_ids - new_ids),
        duplicates=0,
        embedder=embedder.name,
        dim=dim,
        persist_dir=str(persist_dir),
        changed=True,
    )


def _doc_hash(doc: KnowledgeDoc) -> str:
    payload = "\x1f".join(
        (
            doc.id,
            doc.title,
            doc.language,
            doc.evidence_note,
            str(doc.reviewed),
            ",".join(doc.tags),
            doc.body,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _manifest_path(persist_dir: Path) -> Path:
    return persist_dir / MANIFEST_FILE


def _load_manifest(persist_dir: Path) -> dict[str, Any] | None:
    """The stored signature, chunk-id set and fitted-embedder info.

    A corrupt manifest reads as "no manifest": the rebuild is the safe answer.
    The shape is ``{"signature": dict, "chunk_ids": set[str], "fitted": dict}``.
    """
    path = _manifest_path(persist_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result: dict[str, Any] = {
            "signature": data["signature"],
            "chunk_ids": set(data["chunk_ids"]),
            "fitted": dict(data.get("fitted", {})),
        }
        return result
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _write_manifest(
    persist_dir: Path,
    *,
    signature: dict[str, object],
    chunk_ids: list[str],
    fitted: dict[str, object],
) -> None:
    """The manifest is written last and atomically: it is the commit mark."""
    persist_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"signature": signature, "chunk_ids": chunk_ids, "fitted": fitted},
        ensure_ascii=False,
    )
    tmp = _manifest_path(persist_dir).with_name(f"{MANIFEST_FILE}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, _manifest_path(persist_dir))


def _save_embedder_state(persist_dir: Path, embedder: Embedder) -> None:
    """Persist fitted state (a corpus-fitted embedder persists its vectorizer)."""
    save = getattr(embedder, "save", None)
    if callable(save):
        save(persist_dir)


def _write_embedder_marker(
    persist_dir: Path, config: dict[str, str | int], *, fitted: Embedder
) -> None:
    """Record which embedder built this store; the retriever side loads it."""
    persist_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({**config, "fitted_name": fitted.name, "fitted_dim": fitted.resolve_dim()})
    tmp = persist_dir / f"{EMBEDDER_MARKER_FILE}.tmp"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, persist_dir / EMBEDDER_MARKER_FILE)


def _store_is_intact(persist_dir: Path, *, expected_ids: set[str]) -> bool:
    """Consistency check: the persisted store exists and describes this corpus."""
    try:
        store = FaissVectorStore.load(persist_dir)
    except Exception:
        return False
    return store.count() == len(expected_ids) and store.chunk_ids() == expected_ids


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``python -m app.services.rag.ingest`` (make ingest)."""
    parser = argparse.ArgumentParser(
        description="Ingest the knowledge corpus into the vector store"
    )
    parser.parse_args(argv)

    from app.core.config import get_settings

    settings = get_settings()
    docs = load_knowledge_docs()
    config = embedder_marker(
        settings.rag_embedder,
        model_id=settings.rag_embedding_model_id,
        dim=settings.rag_embedding_dim,
    )
    factory = build_embedder_factory(
        settings.rag_embedder,
        model_id=settings.rag_embedding_model_id,
        dim=settings.rag_embedding_dim,
    )
    persist_dir = Path(settings.rag_persist_dir)
    try:
        report = run_ingest(
            docs,
            persist_dir=persist_dir,
            embedder_factory=factory,
            embedder_config=config,
            chunk_words=settings.rag_chunk_words,
            overlap_words=settings.rag_chunk_overlap_words,
        )
    except EmbedderUnavailable as exc:
        print(f"INGEST FAILED: {exc}", file=sys.stderr)
        return 2
    print(report.summary())
    _log.info(
        "rag_ingest_complete",
        docs=report.docs,
        chunks=report.chunks,
        changed=report.changed,
        embedder=report.embedder,
    )
    return 0


__all__ = [
    "CHUNKER_VERSION",
    "IngestPlan",
    "IngestReport",
    "PlannedChunk",
    "main",
    "plan_ingest",
    "run_ingest",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
