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


def no_artifacts(tmp_path: Path) -> Path:
    """An existing, empty artefact directory: "nobody has tuned this yet".

    Pointing ``rag_reranker_dir`` at a path that does not exist is *not* the same
    thing — :func:`retrieval_artifact_dir` falls back to the packaged directory,
    which since Day 35 contains the committed calibration. Tests that assert on
    documented defaults have to say so by handing the factory an empty directory
    it can actually find.
    """

    directory = tmp_path / "artifacts-none"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


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
        assert isinstance(result, KnowledgeRetriever)  # narrows the Retriever protocol
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


# --------------------------------------------------------------------------- #
# Day 35: RETRIEVER=baseline|hybrid|hybrid_rerank, and the tuning.json it reads #
# --------------------------------------------------------------------------- #

from structlog.testing import capture_logs  # noqa: E402  (kept next to the tests that use it)

from app.services.rag.factory import (  # noqa: E402
    TUNING_FILE,
    corpus_idf,
    load_tuning,
    retrieval_artifact_dir,
)
from app.services.rag.hybrid import HybridKnowledgeRetriever  # noqa: E402
from app.services.rag.rerank import (  # noqa: E402
    DEFAULT_WEIGHTS,
    FEATURE_NAMES,
    RERANKER_METADATA_FILE,
    RERANKER_WEIGHTS_FILE,
)


def write_tuning(directory: Path, **overrides: object) -> Path:
    """A tuning.json in the shape ``ml/.../retrieval/tuning.py`` writes."""

    payload: dict[str, object] = {
        "_note": "written by a test",
        "embedder_kind": "tfidf",
        "dev": {
            "dense_weight": 0.3,
            "fusion_k": 40,
            "candidates": 7,
            "rerank_candidates": 7,
            "bm25_k1": 1.4,
            "bm25_b": 0.6,
            "title_boost": 3.0,
        },
        "abstain": {"hybrid": 0.22, "hybrid_rerank": 0.44},
        "splits": {"dev": "b9dc4cbb", "calibration": "1ce6ecf9", "test": "091a469c"},
    }
    payload.update(overrides)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / TUNING_FILE).write_text(json.dumps(payload), encoding="utf-8")
    return directory


def write_reranker(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / RERANKER_WEIGHTS_FILE).write_text(
        json.dumps({"features": list(FEATURE_NAMES), "weights": list(DEFAULT_WEIGHTS)}),
        encoding="utf-8",
    )
    (directory / RERANKER_METADATA_FILE).write_text(
        json.dumps({"feature_version": 1, "trained_on": "day35-train"}), encoding="utf-8"
    )
    return directory


class TestRetrieverSelection:
    def test_baseline_is_the_default_and_stays_the_day13_retriever(self, ingested: Path) -> None:
        assert isinstance(build_knowledge_retriever(settings(ingested)), KnowledgeRetriever)

    def test_hybrid_builds_the_hybrid_retriever(self, ingested: Path) -> None:
        result = build_knowledge_retriever(settings(ingested, retriever="hybrid"))
        assert isinstance(result, HybridKnowledgeRetriever)
        assert result.setting == "hybrid"

    def test_hybrid_answers_a_query_end_to_end(self, ingested: Path, tmp_path: Path) -> None:
        import asyncio

        # No artefact dir: this asserts the *mechanism* over the three-document
        # fixture, so it must not inherit the committed calibration, whose floor
        # was fitted on the real 59-passage corpus and is far above anything this
        # fixture can score. `TestTheShippedCalibration` pins that file separately.
        result = build_knowledge_retriever(
            settings(ingested, retriever="hybrid", rag_reranker_dir=str(no_artifacts(tmp_path)))
        )
        assert isinstance(result, HybridKnowledgeRetriever)
        chunks = asyncio.run(result.retrieve("cannot sleep at night", limit=2))
        assert chunks and chunks[0].source_id.startswith("sleep-")

    def test_hybrid_rerank_without_coefficients_downgrades_to_hybrid(
        self, ingested: Path, tmp_path: Path
    ) -> None:
        # Not "serve hand-set weights under a fitted threshold": the setting
        # downgrades, and the log says so, because a deployment that asked for a
        # reranker and silently got none is a deployment nobody can debug.
        with capture_logs() as logs:
            result = build_knowledge_retriever(
                settings(
                    ingested,
                    retriever="hybrid_rerank",
                    rag_reranker_dir=str(no_artifacts(tmp_path)),
                )
            )
        assert isinstance(result, HybridKnowledgeRetriever)
        assert result.setting == "hybrid"
        events = [entry["event"] for entry in logs]
        assert "rag_reranker_unavailable" in events
        assert "rag_retriever_downgraded" in events

    def test_hybrid_rerank_with_coefficients_serves_the_reranker(
        self, ingested: Path, tmp_path: Path
    ) -> None:
        artifacts = write_reranker(tmp_path / "artifacts")
        result = build_knowledge_retriever(
            settings(ingested, retriever="hybrid_rerank", rag_reranker_dir=str(artifacts))
        )
        assert isinstance(result, HybridKnowledgeRetriever)
        assert result.setting.startswith("hybrid_rerank:")
        assert not result.setting.endswith(":untrained")

    def test_an_empty_store_degrades_the_same_way_for_every_setting(self, tmp_path: Path) -> None:
        import asyncio

        result = build_knowledge_retriever(
            settings(tmp_path / "never-ingested", retriever="hybrid_rerank")
        )
        assert isinstance(result, HybridKnowledgeRetriever)
        assert result.is_empty
        assert asyncio.run(result.retrieve("anything", limit=3)) == []

    def test_the_choice_is_case_insensitive_like_every_other_choice_here(
        self, ingested: Path
    ) -> None:
        result = build_knowledge_retriever(settings(ingested, retriever="Hybrid"))
        assert isinstance(result, HybridKnowledgeRetriever)


class TestTuningLoading:
    def test_the_dev_block_and_the_matching_abstain_floor_are_used(
        self, ingested: Path, tmp_path: Path
    ) -> None:
        artifacts = write_tuning(tmp_path / "artifacts")
        result = build_knowledge_retriever(
            settings(ingested, retriever="hybrid", rag_reranker_dir=str(artifacts))
        )
        assert isinstance(result, HybridKnowledgeRetriever)
        tuning = result.tuning
        assert tuning.dense_weight == pytest.approx(0.3)
        assert tuning.fusion_k == 40
        assert tuning.candidates == 7
        assert tuning.bm25_k1 == pytest.approx(1.4)
        assert tuning.abstain_score == pytest.approx(0.22)

    def test_the_rerank_setting_gets_its_own_threshold(
        self, ingested: Path, tmp_path: Path
    ) -> None:
        artifacts = write_reranker(write_tuning(tmp_path / "artifacts"))
        result = build_knowledge_retriever(
            settings(ingested, retriever="hybrid_rerank", rag_reranker_dir=str(artifacts))
        )
        assert isinstance(result, HybridKnowledgeRetriever)
        assert result.abstain_score == pytest.approx(0.44)

    def test_an_env_override_beats_the_fitted_value(self, ingested: Path, tmp_path: Path) -> None:
        artifacts = write_tuning(tmp_path / "artifacts")
        with capture_logs() as logs:
            result = build_knowledge_retriever(
                settings(
                    ingested,
                    retriever="hybrid",
                    rag_reranker_dir=str(artifacts),
                    rag_hybrid_dense_weight=0.9,
                    rag_abstain_score=0.05,
                )
            )
        assert isinstance(result, HybridKnowledgeRetriever)
        assert result.tuning.dense_weight == pytest.approx(0.9)
        assert result.tuning.abstain_score == pytest.approx(0.05)
        assert any(entry["event"] == "rag_tuning_env_override" for entry in logs)

    def test_a_missing_file_boots_on_documented_defaults(
        self, ingested: Path, tmp_path: Path
    ) -> None:
        # `RETRIEVER=hybrid` on a fresh clone, before anybody has run the tuning
        # stage: a warning and a working retriever, never a crash.
        with capture_logs() as logs:
            result = build_knowledge_retriever(
                settings(ingested, retriever="hybrid", rag_reranker_dir=str(no_artifacts(tmp_path)))
            )
        assert isinstance(result, HybridKnowledgeRetriever)
        assert result.tuning.dense_weight == pytest.approx(0.5)
        assert any(entry["event"] == "rag_tuning_missing" for entry in logs)

    def test_the_dense_scale_is_derived_from_the_embedders_own_floor(
        self, ingested: Path, tmp_path: Path
    ) -> None:
        artifacts = write_tuning(tmp_path / "artifacts")
        result = build_knowledge_retriever(
            settings(ingested, retriever="hybrid", rag_reranker_dir=str(artifacts))
        )
        assert isinstance(result, HybridKnowledgeRetriever)
        # The tfidf floor is 0.12, and a cosine at twice the floor is "clearly a
        # match" — that is the scale the evidence mix divides by.
        assert result.tuning.dense_scale == pytest.approx(0.24)

    def test_unknown_keys_are_ignored_loudly(self, tmp_path: Path) -> None:
        artifacts = write_tuning(tmp_path / "artifacts", dev={"dense_weight": 0.4, "telepathy": 1})
        with capture_logs() as logs:
            tuning = load_tuning(artifacts, settings(tmp_path), embedder_kind="tfidf")
        assert tuning.dense_weight == pytest.approx(0.4)
        assert any(entry["event"] == "rag_tuning_unknown_keys" for entry in logs)

    def test_a_file_fitted_for_another_embedder_says_so(self, tmp_path: Path) -> None:
        artifacts = write_tuning(tmp_path / "artifacts", embedder_kind="sentence_transformers")
        with capture_logs() as logs:
            load_tuning(artifacts, settings(tmp_path), embedder_kind="tfidf")
        assert any(entry["event"] == "rag_tuning_embedder_mismatch" for entry in logs)

    def test_an_unparsable_value_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        artifacts = write_tuning(tmp_path / "artifacts", dev={"fusion_k": "sixty"})
        with capture_logs() as logs:
            tuning = load_tuning(artifacts, settings(tmp_path), embedder_kind="tfidf")
        assert tuning.fusion_k == 60  # the default survives a bad file
        assert any(entry["event"] == "rag_tuning_bad_value" for entry in logs)

    def test_the_artifact_dir_prefers_a_configured_path_that_exists(self, tmp_path: Path) -> None:
        configured = write_tuning(tmp_path / "elsewhere")
        assert retrieval_artifact_dir(settings(tmp_path, rag_reranker_dir=str(configured))) == (
            configured
        )

    def test_the_artifact_dir_falls_back_to_the_packaged_one(self, tmp_path: Path) -> None:
        resolved = retrieval_artifact_dir(settings(tmp_path, rag_reranker_dir="does/not/exist"))
        assert resolved.name == "retrieval_v1"
        assert (resolved.parent / "crisis_v1").is_dir()

    def test_the_idf_map_covers_the_corpus_vocabulary(self, ingested: Path) -> None:
        from app.services.rag.store import FaissVectorStore

        idf = corpus_idf(FaissVectorStore.load(ingested))
        assert "sleep" in idf
        assert idf["sleep"] > 0.0
        assert "qqqneverseen" not in idf


class TestTheShippedCalibration:
    """The committed ``tuning.json`` is served as-is, so it is asserted as-is.

    It is calibration data rather than a model — a fusion weight, two abstain
    floors and the fingerprints of the splits they were fitted on — which is why
    it is in Git while the fitted cross-encoder coefficients beside it are not
    (they are weights, ``make ml-retrieval-rerank`` regenerates them, and the
    factory downgrades to plain ``hybrid`` without them). Nothing here re-derives
    the numbers from the eval set; the point is that a hand edit, a smoke run's
    output or a file fitted for another embedder is caught at build time instead
    of in somebody's chat turn.
    """

    @pytest.fixture()
    def packaged(self, tmp_path: Path) -> tuple[Path, dict[str, object]]:
        directory = retrieval_artifact_dir(settings(tmp_path))
        assert directory.name == "retrieval_v1"
        path = directory / "tuning.json"
        assert path.is_file(), "run: make ml-retrieval-tune ml-retrieval-calibrate"
        return directory, json.loads(path.read_text(encoding="utf-8"))

    def test_it_names_its_schema_and_the_splits_it_was_fitted_on(
        self, packaged: tuple[Path, dict[str, object]]
    ) -> None:
        _, payload = packaged
        assert payload["_schema"] == "manovia.retrieval.tuning/v1"
        splits = payload["splits"]
        assert isinstance(splits, dict)
        # The whole point of shipping the fingerprints: a reader can confirm the
        # reported numbers came from the test split and the fitted numbers did not.
        for name in ("train", "dev", "calibration", "test"):
            digest = splits[name]
            assert isinstance(digest, str) and len(digest) == 64, name
        assert splits["dev"] != splits["test"]
        assert splits["calibration"] != splits["test"]

    def test_no_number_in_it_came_from_a_smoke_run(
        self, packaged: tuple[Path, dict[str, object]]
    ) -> None:
        _, payload = packaged
        dev = payload["dev"]
        abstain = payload["abstain"]
        assert isinstance(dev, dict) and isinstance(abstain, dict)
        assert dev["_smoke"] is False
        assert abstain["_smoke"] is False
        assert dev["_split"] == "dev"
        assert abstain["_split"] == "calibration"

    def test_every_knob_is_one_the_retriever_actually_has(
        self, packaged: tuple[Path, dict[str, object]]
    ) -> None:
        # `load_tuning` skips `_`-prefixed provenance and warns about anything
        # else it does not recognise, so a typo here is a served default plus a
        # warning line nobody reads.
        from app.services.rag.hybrid import HybridTuning

        _, payload = packaged
        dev = payload["dev"]
        assert isinstance(dev, dict)
        fields = set(HybridTuning.__dataclass_fields__)
        knobs = [key for key in dev if not key.startswith("_")]
        assert knobs and all(key in fields for key in knobs), knobs
        # `dense_scale` is derived from the served embedder's floor at build time;
        # persisting it would let a stale number override the live one.
        assert "dense_scale" not in dev
        assert "abstain_score" not in dev

    def test_both_served_settings_get_their_own_calibrated_floor(
        self, packaged: tuple[Path, dict[str, object]], tmp_path: Path
    ) -> None:
        directory, payload = packaged
        abstain = payload["abstain"]
        assert isinstance(abstain, dict)
        thresholds = {key: value for key, value in abstain.items() if not key.startswith("_")}
        assert set(thresholds) == {"hybrid", "hybrid_rerank"}
        for setting, expected in thresholds.items():
            assert isinstance(expected, float) and 0.0 < expected < 1.0
            tuning = load_tuning(
                directory,
                settings(tmp_path),
                embedder_kind=str(payload["embedder_kind"]),
                setting=setting,
                dense_scale=0.24,
            )
            assert tuning.abstain_score == pytest.approx(expected)
            assert tuning.dense_weight == pytest.approx(dev_weight(payload))

    def test_a_deployment_that_overrides_nothing_serves_this_file(self, ingested: Path) -> None:
        # The coupling worth pinning: with no `RAG_RERANKER_DIR`, the retriever a
        # default deployment builds reads *this* committed file. Three fixture
        # tests used to pass because the packaged directory was empty; now that it
        # is not, the expectation lives here instead of being discovered by an
        # unrelated assertion about a three-document corpus.
        result = build_knowledge_retriever(settings(ingested, retriever="hybrid"))
        assert isinstance(result, HybridKnowledgeRetriever)
        assert result.tuning.dense_weight == pytest.approx(0.2)
        assert result.tuning.abstain_score == pytest.approx(0.657762)
        assert result.tuning.dense_scale == pytest.approx(0.24)  # 2 x the tfidf floor

    def test_it_is_fitted_for_the_embedder_the_deployment_serves(
        self, packaged: tuple[Path, dict[str, object]]
    ) -> None:
        _, payload = packaged
        assert payload["embedder_kind"] == "tfidf"

    def test_the_packaged_directory_holds_no_weights(
        self, packaged: tuple[Path, dict[str, object]]
    ) -> None:
        # AGENTS.md ML ground rule 3. `crisis_v1` next door is the one committed
        # exception and it is not being extended; this directory stays JSON-only
        # even though `make ml-retrieval-rerank` installs coefficients into it
        # (they are gitignored, and a wheel built from Git cannot contain them).
        directory, _ = packaged
        forbidden = ("*.joblib", "*.npz", "*.bin", "*.pt", "*.safetensors", "*.onnx", "*.pkl")
        found = [path.name for pattern in forbidden for path in directory.glob(pattern)]
        assert not found, found
        assert {path.name for path in directory.iterdir() if path.suffix == ".py"} == {
            "__init__.py"
        }


def dev_weight(payload: dict[str, object]) -> float:
    dev = payload["dev"]
    assert isinstance(dev, dict)
    weight = dev["dense_weight"]
    assert isinstance(weight, int | float)
    return float(weight)
