"""Day 13 — the knowledge base and retrieval (RAG) service.

Authoring-facing half: ``app/content/knowledge/`` + ``app/content/knowledge.py``
(markdown corpus, front-matter, validation).

This package: chunking, embedding, the FAISS store, idempotent ingestion
(``make ingest``), the retriever that plugs into the chat orchestrator behind
the Day 11 ``Retriever`` protocol, and the factory that builds that retriever
from settings. Ingestion is run directly (``python -m app.services.rag.ingest``,
wired to ``make ingest``) rather than re-exported here, so the module can be
executed without a double-import warning. Decisions in
``docs/adr/0013-knowledge-retrieval-rag.md``; the
safety-review checklist for new documents in
``docs/knowledge-authoring-guide.md``.
"""

from app.services.rag.base import (
    CHUNK_ID_SEP,
    ChunkMetadata,
    Embedder,
    EmbedderError,
    EmbedderUnavailable,
    StoredChunk,
    chunk_id,
)
from app.services.rag.chunking import chunk_text
from app.services.rag.embedders import (
    HashingEmbedder,
    SentenceTransformerEmbedder,
    TfidfEmbedder,
    build_embedder_factory,
    embedder_marker,
)
from app.services.rag.factory import build_knowledge_retriever
from app.services.rag.retriever import KnowledgeRetriever, normalize_language
from app.services.rag.store import FaissVectorStore, Hit, VectorStoreError

__all__ = [
    "CHUNK_ID_SEP",
    "ChunkMetadata",
    "Embedder",
    "EmbedderError",
    "EmbedderUnavailable",
    "FaissVectorStore",
    "HashingEmbedder",
    "Hit",
    "KnowledgeRetriever",
    "SentenceTransformerEmbedder",
    "StoredChunk",
    "TfidfEmbedder",
    "VectorStoreError",
    "build_embedder_factory",
    "build_knowledge_retriever",
    "chunk_id",
    "chunk_text",
    "embedder_marker",
    "normalize_language",
]
