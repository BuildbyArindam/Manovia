"""Vector store unit tests: exact search, filters, persistence, validation."""

from pathlib import Path

import pytest

from app.services.rag.base import CHUNK_ID_SEP, ChunkMetadata
from app.services.rag.store import FaissVectorStore, VectorStoreError, doc_id_of_chunk

DIM = 4


def meta(doc: str, *, language: str = "en", index: int = 0, total: int = 1) -> ChunkMetadata:
    return ChunkMetadata(
        doc_id=doc,
        title=f"Title of {doc}",
        chunk_index=index,
        chunk_total=total,
        language=language,
        tags=("calm",),
        evidence_note="test note",
    )


def _unit(values: list[float]) -> list[float]:
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values]


def build_store() -> FaissVectorStore:
    store = FaissVectorStore(dim=DIM)
    store.add("doc-a#00", _unit([1, 0, 0, 0]), meta("doc-a", index=0, total=2), "alpha text")
    store.add("doc-a#01", _unit([0.2, 1, 0, 0]), meta("doc-a", index=1, total=2), "alpha more")
    store.add("doc-b#00", _unit([0, 0, 1, 0]), meta("doc-b", language="hi"), "beta text")
    return store


class TestQuerying:
    def test_best_first_ordering(self) -> None:
        store = build_store()
        hits = store.query(_unit([1, 0, 0, 0]), k=2)
        assert [h.id for h in hits] == ["doc-a#00", "doc-a#01"]
        assert hits[0].score > hits[1].score > 0.0

    def test_k_bounds_result_count(self) -> None:
        store = build_store()
        assert len(store.query(_unit([1, 0, 0, 0]), k=1)) == 1
        assert len(store.query(_unit([1, 0, 0, 0]), k=99)) == 3

    def test_language_filter(self) -> None:
        store = build_store()
        hits = store.query(_unit([0, 0, 1, 0]), k=3, where={"language": "hi"})
        assert [h.id for h in hits] == ["doc-b#00"]

    def test_doc_id_filter(self) -> None:
        store = build_store()
        hits = store.query(_unit([1, 0, 0, 0]), k=3, where={"doc_id": "doc-a"})
        assert {h.id for h in hits} == {"doc-a#00", "doc-a#01"}

    def test_tag_filter(self) -> None:
        store = build_store()
        assert len(store.query(_unit([1, 0, 0, 0]), k=3, where={"tag": "calm"})) == 3

    def test_empty_store_and_zero_k(self) -> None:
        empty = FaissVectorStore(dim=DIM)
        assert empty.query(_unit([1, 0, 0, 0]), k=3) == []
        assert empty.count() == 0
        assert build_store().query(_unit([1, 0, 0, 0]), k=0) == []

    def test_dim_mismatch_rejected(self) -> None:
        with pytest.raises(VectorStoreError, match="dim"):
            build_store().query([1.0, 0.0], k=1)


class TestBuilding:
    def test_duplicate_id_rejected(self) -> None:
        store = build_store()
        with pytest.raises(VectorStoreError, match="duplicate"):
            store.add("doc-a#00", _unit([1, 0, 0, 0]), meta("doc-a"), "again")

    def test_shape_mismatch_rejected(self) -> None:
        store = FaissVectorStore(dim=DIM)
        with pytest.raises(VectorStoreError, match="shape"):
            store.add("doc-a#00", [1.0, 0.0], meta("doc-a"), "text")

    def test_bad_dim_rejected(self) -> None:
        with pytest.raises(ValueError, match="dim"):
            FaissVectorStore(dim=0)

    def test_accessors(self) -> None:
        store = build_store()
        assert store.count() == 3
        assert store.chunk_ids() == {"doc-a#00", "doc-a#01", "doc-b#00"}
        assert store.text_of("doc-a#00") == "alpha text"
        with pytest.raises(KeyError):
            store.text_of("missing#00")


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        store = build_store()
        store.save(tmp_path)
        restored = FaissVectorStore.load(tmp_path)
        assert restored.count() == 3
        assert restored.chunk_ids() == store.chunk_ids()
        assert restored.text_of("doc-b#00") == "beta text"
        fresh = [h.id for h in restored.query(_unit([0, 0, 1, 0]), k=1)]
        assert fresh == ["doc-b#00"]
        assert fresh[0] and restored.query(_unit([0, 0, 1, 0]), k=1)[0].score > 0.99

    def test_load_rejects_version_mismatch(self, tmp_path: Path) -> None:
        build_store().save(tmp_path)
        import json

        sidecar = tmp_path / "store.json"
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["version"] = 999
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(VectorStoreError, match="version"):
            FaissVectorStore.load(tmp_path)

    def test_load_rejects_inconsistent_sidecar(self, tmp_path: Path) -> None:
        build_store().save(tmp_path)
        import json

        sidecar = tmp_path / "store.json"
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["rows"] = data["rows"][:-1]
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(VectorStoreError, match="inconsistent"):
            FaissVectorStore.load(tmp_path)


def test_chunk_id_format_and_doc_extraction() -> None:
    from app.services.rag.base import chunk_id

    assert chunk_id("grounding-five-senses", 2) == f"grounding-five-senses{CHUNK_ID_SEP}02"
    assert doc_id_of_chunk("grounding-five-senses#02") == "grounding-five-senses"
    # An id without a separator is treated as a single-chunk document id.
    assert doc_id_of_chunk("no-separator-here") == "no-separator-here"
