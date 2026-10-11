"""Embedder unit tests: hashing, corpus-fitted tfidf, and the factory.

All offline: the sentence-transformers path is tested through its *lazy*
contract (no import, no model load) plus the factory's fallback decision,
because the sandbox has no network and the suite must never download.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from app.services.rag.base import Embedder, EmbedderUnavailable
from app.services.rag.embedders import (
    EMBEDDER_FIT_VERSION,
    EMBEDDER_KINDS,
    RECOMMENDED_MIN_SIMILARITY,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    TfidfEmbedder,
    build_embedder_factory,
    embedder_marker,
)

# Deliberately larger than three texts: min_df=2 drops words that appear in
# only one document, so a toy corpus needs repeated vocabulary for the tfidf
# embedder to have anything to work with.
CORPUS = [
    "sleep and rest: the body loves a rhythm. wake at the same time, dim the lights",
    "grounding: name five things you can see, four you can hear, three you can touch",
    "worry time: give worries one named slot of fifteen minutes, then close the notebook",
    "sleep again: waking at the same time anchors the rhythm and rest follows",
    "grounding again: five things you can see grounds a racing mind in the room",
    "worry again: worries wait in their slot of fifteen minutes, then the notebook closes",
]
ON_TOPIC_QUERY = "sleep rhythm at night"
SLEEP_DOC = CORPUS[0]
GROUND_DOC = CORPUS[1]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return num / (na * nb) if na and nb else 0.0


class TestHashingEmbedder:
    def test_deterministic_and_unit_norm(self) -> None:
        emb = HashingEmbedder(dim=64)
        v1, v2 = emb.embed(["the same words"]), emb.embed(["the same words"])
        assert v1 == v2
        norm = sum(x * x for x in v1[0]) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-5)

    def test_dim_matches_resolve_dim(self) -> None:
        emb = HashingEmbedder(dim=128)
        assert emb.resolve_dim() == 128
        assert len(emb.embed(["x"])[0]) == 128

    def test_empty_input(self) -> None:
        assert HashingEmbedder(dim=64).embed([]) == []

    def test_satisfies_protocol(self) -> None:
        emb: Embedder = HashingEmbedder(dim=64)
        assert emb.name.startswith("hashing")


class TestTfidfEmbedder:
    def test_unfit_embedder_raises_unavailable(self) -> None:
        emb = TfidfEmbedder()
        with pytest.raises(EmbedderUnavailable):
            emb.embed(["anything"])
        with pytest.raises(EmbedderUnavailable):
            emb.resolve_dim()
        assert emb.fitted is False

    def test_fit_then_embed(self) -> None:
        emb = TfidfEmbedder().fit(CORPUS)
        assert emb.fitted is True
        vecs = emb.embed(["wake at the same time", "completely unrelated zebra words"])
        assert len(vecs) == 2
        assert all(len(v) == emb.resolve_dim() for v in vecs)

    def test_relevant_outranks_irrelevant(self) -> None:
        emb = TfidfEmbedder().fit(CORPUS)
        query = emb.embed([ON_TOPIC_QUERY])[0]
        on_topic = _cosine(query, emb.embed([SLEEP_DOC])[0])
        off_topic = _cosine(query, emb.embed([GROUND_DOC])[0])  # shares no terms
        assert on_topic > 0.1
        assert off_topic == 0.0
        assert on_topic > off_topic

    def test_feature_cap_dampens_single_term_collisions(self) -> None:
        # Capping exists so one shared *rare* word cannot manufacture
        # relevance (ADR 0013): adding matched terms must raise the cosine
        # sharply, i.e. several shared words outweigh one. (After
        # re-normalisation individual features can exceed the cap; the cap
        # bounds their *contribution to a cosine*, which is what these
        # assertions -- and the floor calibration on the real corpus --
        # actually check.)
        emb = TfidfEmbedder().fit(CORPUS)
        worry_doc = emb.embed([CORPUS[2]])[0]
        single = _cosine(emb.embed(["notebook"])[0], worry_doc)
        multi = _cosine(emb.embed(["worries slot fifteen minutes notebook"])[0], worry_doc)
        assert multi > single * 2
        assert multi > 0.12

    def test_fit_is_stable_across_instances(self) -> None:
        name1 = TfidfEmbedder().fit(CORPUS).name
        name2 = TfidfEmbedder().fit(list(CORPUS)).name
        assert name1 == name2
        assert name1.startswith("tfidf-v1:")

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        original = TfidfEmbedder().fit(CORPUS)
        original.save(tmp_path)
        restored = TfidfEmbedder.load(tmp_path)
        assert restored.name == original.name
        assert restored.resolve_dim() == original.resolve_dim()
        query = ["worry slot of fifteen minutes"]
        assert restored.embed(query) == original.embed(query)

    def test_load_missing_raises_unavailable(self, tmp_path: Path) -> None:
        with pytest.raises(EmbedderUnavailable):
            TfidfEmbedder.load(tmp_path)


class TestMarkersAndFactory:
    def test_marker_includes_fit_version(self) -> None:
        marker = embedder_marker("tfidf", model_id="", dim=0)
        assert marker == {
            "kind": "tfidf",
            "model_id": "",
            "dim": 0,
            "fit_version": EMBEDDER_FIT_VERSION,
        }

    def test_recommended_floors_cover_every_real_kind(self) -> None:
        assert set(RECOMMENDED_MIN_SIMILARITY) == {
            "sentence_transformers",
            "tfidf",
            "hashing",
        }
        assert all(0.0 < v <= 1.0 for v in RECOMMENDED_MIN_SIMILARITY.values())

    def test_factory_tfidf_kind_fits(self) -> None:
        factory = build_embedder_factory("tfidf", model_id="", dim=0)
        emb = factory(CORPUS)
        assert isinstance(emb, TfidfEmbedder)
        assert emb.fitted is True

    def test_factory_hashing_kind_needs_no_fit(self) -> None:
        factory = build_embedder_factory("hashing", model_id="", dim=128)
        emb = factory(CORPUS)
        assert isinstance(emb, HashingEmbedder)
        assert emb.resolve_dim() == 128

    def test_factory_unknown_kind_rejected(self) -> None:
        with pytest.raises(ValueError, match="kind"):
            build_embedder_factory("brainwaves", model_id="", dim=0)

    def test_factory_auto_falls_back_without_sentence_transformers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A ``None`` entry in sys.modules makes ``import`` raise ImportError,
        # which is how a machine without the optional package looks.
        import sys

        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        emb = build_embedder_factory("auto", model_id="some/model", dim=384)(CORPUS)
        assert isinstance(emb, TfidfEmbedder)

    def test_factory_st_kind_defers_the_model_load(self) -> None:
        # Building the factory must not import torch or touch the network;
        # only calling it would resolve the model, and we never call it here.
        factory = build_embedder_factory("sentence_transformers", model_id="some/model", dim=384)
        assert callable(factory)

    def test_st_embedder_is_lazy_about_the_model(self) -> None:
        # Constructing never loads the model: the vector size is only known
        # after a load, so it starts at 0 and the name is pure configuration.
        emb = SentenceTransformerEmbedder("some/model")
        assert emb._model is None
        assert emb.dim == 0
        assert emb.name == "sentence-transformers:some/model"

    def test_embedder_kinds_match_settings_choices(self) -> None:
        from app.core.config import RAG_EMBEDDER_CHOICES

        assert set(RAG_EMBEDDER_CHOICES) == set(EMBEDDER_KINDS)
