"""Ingest unit tests: planning, idempotence, markers, and the rebuild diff."""

from collections.abc import Sequence
from pathlib import Path

from app.content.knowledge import KnowledgeDoc
from app.services.rag.base import Embedder, EmbedderUnavailable, chunk_id
from app.services.rag.embedders import (
    EMBEDDER_FIT_VERSION,
    EMBEDDER_MARKER_FILE,
    HashingEmbedder,
    TfidfEmbedder,
    embedder_marker,
)
from app.services.rag.ingest import IngestReport, plan_ingest, run_ingest

BODY = (
    "Sleep improves when the day has a shape to it. "
    "Wake at the same time, get morning light, and keep the evening dim. "
    "If the mind races at night, write the thoughts down and let the page hold them. "
) * 6


def doc(doc_id: str = "sleep-rhythm", *, language: str = "en", body: str = BODY) -> KnowledgeDoc:
    return KnowledgeDoc(
        id=doc_id,
        title="A steady sleep rhythm",
        tags=("sleep", "rhythm"),
        language=language,
        evidence_note="test",
        reviewed=False,
        body=body,
    )


def docs(*items: KnowledgeDoc) -> tuple[KnowledgeDoc, ...]:
    return items if items else (doc(), doc("breathing-slow", body="Longer body. " * 80))


def factory(texts: Sequence[str]) -> Embedder:
    return TfidfEmbedder().fit(texts)


def config() -> dict[str, str | int]:
    return embedder_marker("tfidf", model_id="", dim=0)


class TestPlanning:
    def test_plan_produces_sequential_ids_and_metadata(self) -> None:
        plan = plan_ingest(docs(), chunk_words=60, overlap_words=15)
        assert len(plan.chunks) >= 2
        assert plan.chunks[0].id == chunk_id("sleep-rhythm", 0)
        assert [
            c.metadata.chunk_index for c in plan.chunks if c.metadata.doc_id == "sleep-rhythm"
        ] == list(range(len([c for c in plan.chunks if c.metadata.doc_id == "sleep-rhythm"])))
        first = plan.chunks[0].metadata
        assert (first.title, first.language, first.tags) == (
            "A steady sleep rhythm",
            "en",
            ("sleep", "rhythm"),
        )
        # The embed text anchors the title twice; the shown text never does.
        assert plan.chunks[0].embed_text.count("A steady sleep rhythm") == 2
        assert plan.chunks[0].text.count("A steady sleep rhythm") == 0

    def test_plan_hashes_documents(self) -> None:
        plan = plan_ingest(docs(), chunk_words=60, overlap_words=15)
        assert set(plan.doc_hashes) == {"sleep-rhythm", "breathing-slow"}
        assert plan.doc_hashes["sleep-rhythm"] != plan.doc_hashes["breathing-slow"]


class TestIdempotence:
    def test_first_run_adds_second_run_is_a_noop(self, tmp_path: Path) -> None:
        first = run_ingest(
            docs(), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config()
        )
        assert (first.added, first.changed) == (first.chunks, True)
        assert first.duplicates == 0

        second = run_ingest(
            docs(), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config()
        )
        assert (second.added, second.updated, second.removed) == (0, 0, 0)
        assert second.changed is False
        assert second.chunks == first.chunks
        assert second.embedder == first.embedder  # the *fitted* identity, restored

    def test_editing_a_document_updates_only_that_diff(self, tmp_path: Path) -> None:
        run_ingest(docs(), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config())
        edited = (doc(), doc("breathing-slow", body="Completely new words. " * 40))
        report = run_ingest(
            edited, persist_dir=tmp_path, embedder_factory=factory, embedder_config=config()
        )
        assert report.changed is True
        assert report.updated > 0 or report.added > 0

    def test_removing_a_document_removes_its_chunks(self, tmp_path: Path) -> None:
        run_ingest(docs(), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config())
        report = run_ingest(
            (doc(),), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config()
        )
        assert report.removed > 0
        assert report.changed is True

    def test_embedder_config_change_triggers_rebuild(self, tmp_path: Path) -> None:
        run_ingest(docs(), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config())
        other = embedder_marker("hashing", model_id="", dim=128)
        report = run_ingest(
            docs(),
            persist_dir=tmp_path,
            embedder_factory=lambda _texts: HashingEmbedder(dim=128),
            embedder_config=other,
        )
        assert report.changed is True

    def test_fit_version_is_part_of_the_marker(self) -> None:
        marker = config()
        assert marker["fit_version"] == EMBEDDER_FIT_VERSION

    def test_corrupt_store_is_rebuilt(self, tmp_path: Path) -> None:
        run_ingest(docs(), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config())
        (tmp_path / "index.faiss").unlink()  # an index lost is a store not intact
        report = run_ingest(
            docs(), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config()
        )
        # Every chunk is materialised again (ids known from the manifest, so
        # they count as updated rather than added); nothing is lost.
        assert report.changed is True
        assert report.added + report.updated == report.chunks
        assert report.removed == 0

    def test_markers_written(self, tmp_path: Path) -> None:
        run_ingest(docs(), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config())
        import json

        marker = json.loads((tmp_path / EMBEDDER_MARKER_FILE).read_text(encoding="utf-8"))
        assert marker["kind"] == "tfidf"
        assert marker["fitted_name"].startswith("tfidf-v1:")
        assert marker["fitted_dim"] == TfidfEmbedder.load(tmp_path).resolve_dim()
        manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["signature"]["embedder"] == config()


class TestUnavailableEmbedder:
    def test_unavailable_embedder_surfaces_as_unavailable(self, tmp_path: Path) -> None:
        def broken(_texts: Sequence[str]) -> Embedder:
            raise EmbedderUnavailable("model download failed")

        import pytest

        with pytest.raises(EmbedderUnavailable):
            run_ingest(
                docs(), persist_dir=tmp_path, embedder_factory=broken, embedder_config=config()
            )


def test_report_summary_is_log_safe(tmp_path: Path) -> None:
    report: IngestReport = run_ingest(
        docs(), persist_dir=tmp_path, embedder_factory=factory, embedder_config=config()
    )
    line = report.summary()
    assert "chunks" in line and "added" in line
    # No chunk text may ever leak into the summary.
    assert "Sleep improves" not in line
