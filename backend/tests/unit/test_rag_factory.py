"""Factory unit tests: settings → retriever, with safe degradation everywhere."""

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.chat.retrieval import NullRetriever
from app.services.rag.embedders import EMBEDDER_MARKER_FILE, embedder_marker
from app.services.rag.factory import build_knowledge_retriever
from app.services.rag.ingest import run_ingest
from app.services.rag.retriever import KnowledgeRetriever
from tests.unit.test_rag_retriever import DOCS
from tests.unit.test_rag_retriever import build as build_store_dir


def settings(tmp_path: Path, **overrides: object) -> Settings:
    import inspect

    base = {
        "rag_enabled": True,
        "rag_persist_dir": str(tmp_path),
        "rag_min_similarity": 0.0,  # defer to the per-embedder recommended floor
    }
    base.update(overrides)
    sig = inspect.signature(Settings)
    safe = {k: v for k, v in base.items() if k in sig.parameters}
    return Settings(_env_file=None, **safe)  # type: ignore[arg-type]


@pytest.fixture()
def ingested(tmp_path: Path) -> Path:
    run_ingest(
        DOCS,
        persist_dir=tmp_path,
        embedder_factory=lambda texts: (
            __import__("app.services.rag.embedders", fromlist=["TfidfEmbedder"])
            .TfidfEmbedder()
            .fit(texts)
        ),
        embedder_config=embedder_marker("tfidf", model_id="", dim=0),
        chunk_words=80,
        overlap_words=20,
    )
    return tmp_path


class TestBuildKnowledgeRetriever:
    def test_disabled_returns_null_retriever(self, tmp_path: Path) -> None:
        result = build_knowledge_retriever(settings(tmp_path, rag_enabled=False))
        assert isinstance(result, NullRetriever)

    def test_missing_dir_degrades_to_empty_retriever(self, tmp_path: Path) -> None:
        result = build_knowledge_retriever(settings(tmp_path / "never-ingested"))
        assert isinstance(result, KnowledgeRetriever)
        assert result.is_empty is True

    def test_ingested_store_builds_a_working_retriever(self, ingested: Path) -> None:
        import asyncio

        result = build_knowledge_retriever(settings(ingested))
        assert isinstance(result, KnowledgeRetriever)
        assert result.is_empty is False
        # The recommended tfidf floor applies when nothing is configured.
        assert result.min_similarity == pytest.approx(0.12)
        chunks = asyncio.run(result.retrieve("cannot sleep at night", limit=2))
        assert chunks and chunks[0].source_id.startswith("sleep-")

    def test_configured_floor_wins(self, ingested: Path) -> None:
        result = build_knowledge_retriever(settings(ingested, rag_min_similarity=0.42))
        assert result.min_similarity == pytest.approx(0.42)

    def test_store_marker_rule_tfidf(self, ingested: Path) -> None:
        # The marker file decides which embedder is rebuilt — flipping the
        # settings cannot silently query the index in the wrong space.
        import asyncio

        marker = json.loads((ingested / EMBEDDER_MARKER_FILE).read_text(encoding="utf-8"))
        assert marker["kind"] == "tfidf"
        result = build_knowledge_retriever(settings(ingested, rag_embedder="sentence_transformers"))
        chunks = asyncio.run(result.retrieve("cannot sleep at night", limit=1))
        # Still answers from the tfidf space the store was built with.
        assert isinstance(chunks, list)

    def test_corrupt_embedder_artifact_degrades_to_no_retrieval(self, ingested: Path) -> None:
        import asyncio

        (ingested / "embedder.joblib").write_bytes(b"not a joblib file")
        result = build_knowledge_retriever(settings(ingested))
        assert isinstance(result, KnowledgeRetriever)
        # Chat proceeds; it simply gets no reference material.
        assert asyncio.run(result.retrieve("cannot sleep at night", limit=3)) == []

    def test_missing_marker_degrades_to_no_retrieval(self, ingested: Path) -> None:
        import asyncio

        (ingested / EMBEDDER_MARKER_FILE).unlink()
        result = build_knowledge_retriever(settings(ingested))
        assert isinstance(result, KnowledgeRetriever)
        assert asyncio.run(result.retrieve("cannot sleep at night", limit=3)) == []


class TestFloorResolution:
    def test_unknown_kind_gets_the_default(self) -> None:
        from app.services.rag.factory import _resolve_floor

        assert _resolve_floor(0.0, "mystery") == 0.25
        assert _resolve_floor(0.0, "tfidf") == pytest.approx(0.12)
        assert _resolve_floor(0.0, "sentence_transformers") == pytest.approx(0.35)
        assert _resolve_floor(0.3, "tfidf") == pytest.approx(0.3)  # configured wins


def test_mini_corpus_fixture_still_builds(tmp_path: Path) -> None:
    """Guard the shared fixture used by the retriever tests."""
    build_store_dir(tmp_path, floor=0.12)
    assert (tmp_path / "index.faiss").exists()
