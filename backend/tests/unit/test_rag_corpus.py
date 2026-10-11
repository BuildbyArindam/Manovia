"""Corpus-level behaviour: the whole knowledge pipeline on the real 40 docs.

This is the in-suite form of the Day 13 offline probe: ingest the actual
curated corpus with the offline (tfidf) embedder and pin the behavioural
contract -- an on-topic question retrieves the right document, an
out-of-domain question retrieves nothing, and ingest is idempotent.
"""

import asyncio
from pathlib import Path

import pytest

from app.content.knowledge import load_knowledge_docs
from app.services.rag.embedders import TfidfEmbedder, embedder_marker
from app.services.rag.ingest import run_ingest
from app.services.rag.retriever import KnowledgeRetriever
from app.services.rag.store import FaissVectorStore

QUERY_FLOOR = 0.12  # RECOMMENDED_MIN_SIMILARITY["tfidf"]


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Ingest the real corpus once for this module (tfidf, offline)."""
    docs = load_knowledge_docs()
    persist = tmp_path_factory.mktemp("rag-corpus")
    report = run_ingest(
        docs,
        persist_dir=persist,
        embedder_factory=lambda texts: TfidfEmbedder().fit(texts),
        embedder_config=embedder_marker("tfidf", model_id="", dim=0),
    )
    assert report.chunks > 0 and report.changed is True
    return persist


@pytest.fixture(scope="module")
def retriever(corpus_dir: Path) -> KnowledgeRetriever:
    return KnowledgeRetriever(
        FaissVectorStore.load(corpus_dir),
        TfidfEmbedder.load(corpus_dir),
        min_similarity=QUERY_FLOOR,
        fallback_language="en",
    )


def test_corpus_ingests_and_is_idempotent(corpus_dir: Path) -> None:
    docs = load_knowledge_docs()
    report = run_ingest(
        docs,
        persist_dir=corpus_dir,
        embedder_factory=lambda texts: TfidfEmbedder().fit(texts),
        embedder_config=embedder_marker("tfidf", model_id="", dim=0),
    )
    assert report.changed is False
    assert (report.added, report.updated, report.removed) == (0, 0, 0)


def test_sleep_question_retrieves_the_sleep_documents(retriever: KnowledgeRetriever) -> None:
    chunks = asyncio.run(retriever.retrieve("I can't sleep before exams", limit=3))
    assert chunks, "the flagship sleep query must retrieve"
    assert chunks[0].source_id.startswith("sleep-")


def test_exam_stress_question_retrieves_exam_help(retriever: KnowledgeRetriever) -> None:
    chunks = asyncio.run(retriever.retrieve("exams are close and I feel like failing", limit=3))
    assert chunks and any(c.source_id.startswith("exams-") for c in chunks)


def test_irrelevant_question_returns_nothing(retriever: KnowledgeRetriever) -> None:
    chunks = asyncio.run(retriever.retrieve("what is the capital of France", limit=3))
    assert chunks == []


def test_language_filter_returns_only_that_language(retriever: KnowledgeRetriever) -> None:
    chunks = asyncio.run(retriever.retrieve("रात को नींद नहीं आती", limit=3, language="hi"))
    assert chunks, "the Hindi sleep query must retrieve from the Hindi docs"
    docs = load_knowledge_docs()
    languages = {d.language for d in docs if d.id in {c.source_id for c in chunks}}
    assert languages == {"hi"}


def test_retrieved_text_is_the_document_body_not_user_data(retriever: KnowledgeRetriever) -> None:
    chunks = asyncio.run(retriever.retrieve("how do I tell someone I trust", limit=1))
    assert chunks
    # Curated prose only: the retrieval path never sees raw user messages.
    assert all("[{source_id}]" not in c.text for c in chunks)
    assert all(c.title and c.text for c in chunks)
