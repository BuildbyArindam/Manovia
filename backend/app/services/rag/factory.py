"""Build the retriever a deployment should use, from its settings.

Day 35 made this the place where ``RETRIEVER`` is honoured: ``baseline`` (the
Day-13 dense-only retriever, still the default and still the rollback),
``hybrid`` (BM25 + dense fused by rank) and ``hybrid_rerank`` (hybrid, then a
cross-encoder over the top candidates). All three satisfy the same
:class:`~app.services.chat.retrieval.Retriever` protocol, so switching between
them is a configuration change and never a code change — which is the whole
point of shipping a retrieval improvement behind a flag.

The calibrated numbers (fusion weight, candidate counts, the abstain floor) come
from the committed ``app/ml_artifacts/retrieval_v1/tuning.json`` that
``ml/src/manovia_ml/retrieval/tuning.py`` fitted on the dev + calibration splits;
env vars override them one at a time, for rollback. A missing or mismatched
artefact degrades to documented defaults with a warning, never to a crash and
never to a silently different retrieval.

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
from collections.abc import Mapping, Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

import structlog

from app.core.config import Settings
from app.services.chat.retrieval import NullRetriever, Retriever
from app.services.rag.base import Embedder, EmbedderUnavailable
from app.services.rag.bm25 import Bm25Index
from app.services.rag.embedders import (
    EMBEDDER_MARKER_FILE,
    RECOMMENDED_MIN_SIMILARITY,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    TfidfEmbedder,
)
from app.services.rag.hybrid import HybridKnowledgeRetriever, HybridTuning
from app.services.rag.rerank import CrossEncoder, CrossEncoderUnavailable, load_cross_encoder
from app.services.rag.retriever import KnowledgeRetriever
from app.services.rag.store import FaissVectorStore, VectorStoreError

_log = structlog.get_logger()

#: The committed artefact directory: tuning.json (+ the reranker coefficients
#: once a fit has been run). Packaged data, no weights.
RETRIEVAL_ARTIFACT_DIR = "ml_artifacts/retrieval_v1"
#: The file inside it that carries the calibrated numbers.
TUNING_FILE = "tuning.json"


def build_knowledge_retriever(settings: Settings) -> Retriever:
    """The chat pipeline's retriever: knowledge-backed, or a null one."""
    if not settings.rag_enabled:
        return NullRetriever()
    persist_dir = Path(settings.rag_persist_dir)
    store = _load_store(persist_dir)
    setting = settings.retriever.strip().casefold()
    if store.count() == 0:
        # Nothing ingested (yet). An empty store is a valid retriever that
        # always answers [] — but say why, once, at build time. Every setting
        # degrades the same way, so a hybrid deployment with no ingest is empty
        # rather than half-working.
        _log.warning(
            "rag_store_empty",
            persist_dir=str(persist_dir),
            retriever=setting,
            hint="run: make ingest",
        )
        return _assemble(
            setting,
            store,
            HashingEmbedder(dim=64),
            settings,
            kind="none",
            floor=settings.rag_min_similarity if settings.rag_min_similarity > 0 else 0.5,
        )
    embedder, kind = _load_embedder(persist_dir)
    return _assemble(
        setting,
        store,
        embedder,
        settings,
        kind=kind,
        floor=_resolve_floor(settings.rag_min_similarity, kind),
    )


def _assemble(
    setting: str,
    store: FaissVectorStore,
    embedder: Embedder,
    settings: Settings,
    *,
    kind: str,
    floor: float,
) -> Retriever:
    """The retriever for ``setting``, over a store and the embedder that fits it."""
    if setting not in {"hybrid", "hybrid_rerank"}:
        return KnowledgeRetriever(store, embedder, min_similarity=floor, fallback_language="en")

    artifact_dir = retrieval_artifact_dir(settings)
    reranker: CrossEncoder | None = None
    if setting == "hybrid_rerank":
        reranker = _load_reranker(artifact_dir, store)
        if reranker is None:
            # Downgrade the *setting*, not the threshold. Serving hand-set
            # coefficients under a threshold that was calibrated on fitted ones
            # would put an uncalibrated number where a calibrated one is
            # promised; plain hybrid is the honest fallback, and it says so in
            # the log line and in `retriever.setting`.
            _log.warning(
                "rag_retriever_downgraded",
                requested="hybrid_rerank",
                served="hybrid",
                reason="no loadable reranker coefficients",
                hint="run: make ml-retrieval-rerank",
            )
            setting = "hybrid"

    tuning = load_tuning(
        artifact_dir, settings, embedder_kind=kind, setting=setting, dense_scale=2.0 * floor
    )
    _log.info(
        "rag_retriever_built",
        retriever=setting,
        embedder=kind,
        dense_weight=tuning.dense_weight,
        fusion_k=tuning.fusion_k,
        candidates=tuning.candidates,
        dense_scale=tuning.dense_scale,
        abstain_score=tuning.abstain_score,
        reranker=getattr(reranker, "name", "none"),
        artifact_dir=str(artifact_dir),
    )
    return HybridKnowledgeRetriever(
        store,
        embedder,
        reranker=reranker,
        tuning=tuning,
        min_similarity=floor,
        fallback_language="en",
    )


def retrieval_artifact_dir(settings: Settings) -> Path:
    """Where tuning.json and the reranker coefficients live.

    Resolved the way the safety artefacts are: an explicit configured path wins
    (so a deployment can point at a refit), otherwise the packaged directory
    inside ``app/``. Never a hard-coded absolute path (AGENTS.md).
    """
    configured = settings.rag_reranker_dir.strip()
    if configured:
        path = Path(configured).expanduser()
        candidate = path if path.is_absolute() else Path.cwd() / path
        if candidate.is_dir():
            return candidate
        # `make dev` runs from backend/, tests from the repo root, a container
        # from /app: the packaged location is the one that is always right.
    return Path(str(files("app").joinpath(RETRIEVAL_ARTIFACT_DIR)))


def load_tuning(
    artifact_dir: Path,
    settings: Settings,
    *,
    embedder_kind: str = "",
    setting: str = "hybrid",
    dense_scale: float = 0.24,
) -> HybridTuning:
    """The calibrated knobs: ``tuning.json`` first, then any explicit env override.

    The file is written by ``ml/src/manovia_ml/retrieval/tuning.py`` and carries
    three blocks: ``dev`` (the fusion knobs, fitted on the dev split only),
    ``abstain`` (one floor per setting, fitted on the calibration split only) and
    ``splits`` (the split fingerprints the numbers were fitted on, so a reader
    can check the test split never entered). A missing file means nobody has run
    the tuning stage yet: documented defaults, a warning, and a retriever that
    still boots — ``RETRIEVER=hybrid`` on a fresh clone must not be a crash.

    A file fitted for a different embedder kind is still used, because the
    fusion weight is a weight over *ranks* and ``dense_scale`` is exactly the
    knob that absorbs a different cosine range — but the mismatch is logged,
    since the ranking that was measured is not the ranking being served.
    """
    defaults = HybridTuning()
    path = artifact_dir / TUNING_FILE
    values: dict[str, Any] = {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _log.warning(
            "rag_tuning_missing",
            path=str(path),
            hint="run: make ml-retrieval-tune (defaults used)",
        )
    else:
        if isinstance(payload, dict):
            dev = payload.get("dev")
            abstain = payload.get("abstain")
            if isinstance(dev, dict):
                values.update({k: v for k, v in dev.items() if not k.startswith("_")})
            if isinstance(abstain, dict) and setting in abstain:
                values["abstain_score"] = abstain[setting]
            elif isinstance(abstain, dict):
                _log.warning(
                    "rag_tuning_no_abstain_for_setting",
                    setting=setting,
                    available=sorted(abstain),
                )
            fitted_kind = str(values.pop("embedder_kind", "") or payload.get("embedder_kind", ""))
            if fitted_kind and embedder_kind and fitted_kind != embedder_kind:
                _log.warning(
                    "rag_tuning_embedder_mismatch",
                    fitted=fitted_kind,
                    active=embedder_kind,
                    hint="refit: make ml-retrieval-tune",
                )
    merged = _coerce_tuning(values, defaults)
    merged.setdefault("dense_scale", dense_scale if dense_scale > 0 else defaults.dense_scale)
    overrides = {
        "dense_weight": settings.rag_hybrid_dense_weight,
        "fusion_k": settings.rag_hybrid_fusion_k,
        "candidates": settings.rag_hybrid_candidates,
        "rerank_candidates": settings.rag_rerank_candidates,
        "abstain_score": settings.rag_abstain_score,
    }
    applied = {name: value for name, value in overrides.items() if value is not None}
    if applied:
        _log.info("rag_tuning_env_override", **applied)
    return HybridTuning(**{**merged, **applied})


def _coerce_tuning(values: Mapping[str, Any], defaults: HybridTuning) -> dict[str, Any]:
    """Keep only the keys this build understands, at the right type.

    A tuning.json from a future (or hand-edited) run must not be able to boot a
    retriever with ``fusion_k=None``; unknown keys are ignored with a warning,
    because the file is written by ``ml/`` and read by ``backend/`` and the two
    are versioned independently.
    """
    fields = {field for field in defaults.__dataclass_fields__}
    unknown = sorted(set(values) - fields)
    if unknown:
        _log.warning("rag_tuning_unknown_keys", keys=unknown)
    out: dict[str, Any] = {}
    for name in sorted(set(values) & fields):
        current = getattr(defaults, name)
        try:
            out[name] = type(current)(values[name])
        except (TypeError, ValueError):
            _log.warning("rag_tuning_bad_value", key=name, value=repr(values[name]))
    return out


def _load_reranker(artifact_dir: Path, store: FaissVectorStore) -> CrossEncoder | None:
    """The fitted cross-encoder coefficients, or ``None`` when there are none.

    ``None`` means the caller downgrades to plain hybrid rather than serving
    hand-set weights under a fitted threshold. The untrained scorer still exists
    (:meth:`LexicalCrossEncoder.default`) and the Day-35 evaluation measures it,
    so "what do you get without fitting anything" is a reported number — it is
    just not a number a deployment guesses with.
    """
    idf = corpus_idf(store)
    try:
        encoder = load_cross_encoder(artifact_dir, idf=idf)
    except CrossEncoderUnavailable as exc:
        _log.warning("rag_reranker_unavailable", reason=str(exc))
        return None
    _log.info(
        "rag_reranker_loaded",
        name=encoder.name,
        trained=bool(getattr(encoder, "metadata", {}).get("trained", False)),
    )
    return encoder


def corpus_idf(store: FaissVectorStore) -> dict[str, float]:
    """Term -> IDF over the ingested corpus, for the reranker's idf_overlap.

    The IDF has to come from the same corpus the candidates came from, or the
    feature measures the wrong thing (a rare word *here* is not a rare word in
    Wikipedia). Public because the Day-35 evaluation harness has to build the
    same features over the same corpus when it fits coefficients, and a private
    helper would have tempted it to invent its own IDF.
    """
    index = Bm25Index.build(
        ((chunk.id, chunk.text, chunk.metadata) for chunk in store.chunks()),
        title_boost=0,
    )
    return {term: index.idf(term) for term in index.vocabulary}


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


__all__ = [
    "RETRIEVAL_ARTIFACT_DIR",
    "TUNING_FILE",
    "build_knowledge_retriever",
    "corpus_idf",
    "load_tuning",
    "retrieval_artifact_dir",
]
