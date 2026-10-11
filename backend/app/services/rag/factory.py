"""Build the retriever a deployment should use, from its settings.

The rule this module exists to enforce: **the store's own marker decides**
which embedder reads it. ``embedder.json`` is written by the ingest and names
the kind, model id and dimension the index was built with; rebuilding the
embedder from today's settings instead would let a config change put a query
into a different vector space than the chunks — every score would be quietly
meaningless. So: marker found → the recorded embedder is reconstructed
(loading the fitted TF-IDF state where there is one); marker missing → an
empty retriever and a warning that names ``make ingest``.

Construction never fails the caller: retrieval is optional context for a
chat turn, and the orchestrator already degrades to "no reference material".
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import structlog

from app.core.config import Settings
from app.services.chat.retrieval import NullRetriever, Retriever
from app.services.rag.base import Embedder, EmbedderUnavailable
from app.services.rag.embedders import (
    EMBEDDER_MARKER_FILE,
    RECOMMENDED_MIN_SIMILARITY,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    TfidfEmbedder,
)
from app.services.rag.retriever import KnowledgeRetriever
from app.services.rag.store import FaissVectorStore, VectorStoreError

_log = structlog.get_logger()


def build_knowledge_retriever(settings: Settings) -> Retriever:
    """The chat pipeline's retriever: knowledge-backed, or a null one."""
    if not settings.rag_enabled:
        return NullRetriever()
    persist_dir = Path(settings.rag_persist_dir)
    store = _load_store(persist_dir)
    if store.count() == 0:
        # Nothing ingested (yet). An empty store is a valid retriever that
        # always answers [] — but say why, once, at build time.
        _log.warning("rag_store_empty", persist_dir=str(persist_dir), hint="run: make ingest")
        return KnowledgeRetriever(
            store,
            HashingEmbedder(dim=64),
            min_similarity=settings.rag_min_similarity if settings.rag_min_similarity > 0 else 0.5,
            fallback_language=None,
        )
    embedder, kind = _load_embedder(persist_dir)
    return KnowledgeRetriever(
        store,
        embedder,
        min_similarity=_resolve_floor(settings.rag_min_similarity, kind),
        fallback_language="en",
    )


def _load_store(persist_dir: Path) -> FaissVectorStore:
    try:
        return FaissVectorStore.load(persist_dir)
    except VectorStoreError as exc:
        _log.error("rag_store_unreadable", persist_dir=str(persist_dir), error=str(exc))
        return FaissVectorStore(dim=1)  # empty; answers []


def _load_embedder(persist_dir: Path) -> tuple[Embedder, str]:
    """Reconstruct the embedder the store was built with, from its marker."""
    marker_path = persist_dir / EMBEDDER_MARKER_FILE
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        kind = str(marker["kind"])
        model_id = str(marker.get("model_id", ""))
        dim = int(marker.get("dim", 0))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        _log.error(
            "rag_embedder_marker_missing", persist_dir=str(persist_dir), hint="re-run: make ingest"
        )
        return _empty_embedder(), "none"

    try:
        if kind == "tfidf":
            return TfidfEmbedder.load(persist_dir), kind
        if kind == "sentence_transformers":
            return SentenceTransformerEmbedder(model_id), kind
        if kind == "hashing":
            return HashingEmbedder(dim=dim), kind
    except EmbedderUnavailable as exc:
        _log.error("rag_embedder_unavailable", kind=kind, error=str(exc))
        return _empty_embedder(), "none"
    except Exception as exc:  # a corrupt joblib should not take chat down
        _log.error("rag_embedder_load_failed", kind=kind, error_type=type(exc).__name__)
        return _empty_embedder(), "none"
    _log.error("rag_embedder_marker_unknown", kind=kind, hint="re-run: make ingest")
    return _empty_embedder(), "none"


class _ZeroEmbedder:
    """Answers nothing; used only over an unusable store (which is empty)."""

    name = "unavailable"
    dim = 1

    def resolve_dim(self) -> int:
        return 1

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return []


def _empty_embedder() -> Embedder:
    result: Embedder = _ZeroEmbedder()
    return result


def _resolve_floor(configured: float, kind: str) -> float:
    """The configured floor, or the embedding space's recommended one."""
    if configured > 0:
        return configured
    return RECOMMENDED_MIN_SIMILARITY.get(kind, 0.25)


__all__ = ["build_knowledge_retriever"]
