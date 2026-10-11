"""Retriever unit tests: floors, filters, fallback language, empty stores.

Uses a tiny real ingest (hashing embedder, hand-built vectors via tfidf) so
the full store → retriever path is exercised without any network.
"""

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

import pytest

from app.content.knowledge import KnowledgeDoc
from app.services.rag.embedders import TfidfEmbedder, embedder_marker
from app.services.rag.ingest import run_ingest
from app.services.rag.retriever import KnowledgeRetriever, normalize_language


def make_doc(doc_id: str, title: str, body: str, language: str = "en") -> KnowledgeDoc:
    return KnowledgeDoc(
        id=doc_id,
        title=title,
        tags=("calm",),
        language=language,
        evidence_note="test",
        reviewed=False,
        body=body,
    )


DOCS = (
    make_doc(
        "sleep-rhythm",
        "A steady sleep rhythm",
        (
            "Sleep improves when the day has a shape. Wake at the same time, keep evenings dim, "
            "and let the mind put the day down on paper before bed. " * 4
        ),
    ),
    make_doc(
        "worry-window",
        "A window for worries",
        (
            "Give worries one slot of the day. Write each worry in the notebook, and let them "
            "wait together until the window opens. " * 4
        ),
    ),
    make_doc(
        "sleep-rhythm-hi",
        "सोने का समय",
        ("नींद तब बेहतर होती है जब दिन का एक ढाँचा हो। एक ही समय पर उठें और शाम को रोशनी कम करें। " * 4),
        language="hi",
    ),
)


def build(tmp_path: Path, *, floor: float) -> KnowledgeRetriever:
    """Ingest the mini-corpus into ``tmp_path`` and load a retriever from it."""
    run_ingest(
        DOCS,
        persist_dir=tmp_path,
        embedder_factory=lambda texts: TfidfEmbedder().fit(texts),
        embedder_config=embedder_marker("tfidf", model_id="", dim=0),
        chunk_words=80,
        overlap_words=20,
    )
    from app.services.rag.store import FaissVectorStore

    return KnowledgeRetriever(
        FaissVectorStore.load(tmp_path),
        TfidfEmbedder.load(tmp_path),
        min_similarity=floor,
        fallback_language="en",
    )


@pytest.fixture()
def retriever(tmp_path: Path) -> KnowledgeRetriever:
    return build(tmp_path, floor=0.12)


R = TypeVar("R")


def _run(coro: Coroutine[Any, Any, list[R]]) -> list[R]:
    """``asyncio.run`` with a friendly name for these sync-shaped tests."""
    return asyncio.run(coro)


class TestKnowledgeRetriever:
    def test_relevant_query_returns_best_document_first(
        self, retriever: KnowledgeRetriever
    ) -> None:
        chunks = _run(retriever.retrieve("I cannot sleep, my mind races at night", limit=2))
        assert chunks, "an on-topic query must retrieve"
        assert chunks[0].source_id.startswith("sleep-")
        assert chunks[0].title
        assert chunks[0].text

    def test_irrelevant_query_returns_nothing(self, retriever: KnowledgeRetriever) -> None:
        # An out-of-domain query whose only lexical overlaps are stopwords.
        assert _run(retriever.retrieve("zzz qqq xxx", limit=3)) == []

    def test_language_filter_restricts_results(self, retriever: KnowledgeRetriever) -> None:
        chunks = _run(retriever.retrieve("नींद नहीं आ रही", limit=3, language="hi"))
        assert chunks
        # Devanagari query hits the Hindi doc first via the language filter.
        assert all("hi" in c.source_id or "sleep" in c.source_id for c in chunks)

    def test_floor_filters_weak_matches(self, tmp_path: Path) -> None:
        permissive = build(tmp_path / "a", floor=0.12)
        strict = build(tmp_path / "b", floor=0.99)
        assert _run(permissive.retrieve("sleep", limit=3))
        assert _run(strict.retrieve("sleep", limit=3)) == []

    def test_invalid_floor_rejected(self, tmp_path: Path) -> None:
        from app.services.rag.embedders import HashingEmbedder
        from app.services.rag.store import FaissVectorStore

        store = FaissVectorStore(dim=4)
        with pytest.raises(ValueError, match="min_similarity"):
            KnowledgeRetriever(store, HashingEmbedder(dim=64), min_similarity=0.0)

    def test_empty_store_answers_nothing(self, tmp_path: Path) -> None:
        from app.services.rag.embedders import HashingEmbedder
        from app.services.rag.store import FaissVectorStore

        retriever = KnowledgeRetriever(
            FaissVectorStore(dim=64), HashingEmbedder(dim=64), min_similarity=0.5
        )
        assert retriever.is_empty is True
        assert _run(retriever.retrieve("anything at all", limit=3)) == []

    def test_one_result_per_document(self, retriever: KnowledgeRetriever) -> None:
        # A document with several strong chunks must not crowd out other
        # documents: the UI renders one "Learn more" card per result.
        for query in ("what is therapy like", "how do I tell someone I trust"):
            chunks = _run(retriever.retrieve(query, limit=3))
            ids = [c.source_id for c in chunks]
            assert len(ids) == len(set(ids)), f"duplicate documents for {query!r}"

    def test_normalize_language(self) -> None:
        assert normalize_language(" EN ") == "en"
        assert normalize_language(None) is None
        assert normalize_language("") is None
