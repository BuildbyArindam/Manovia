"""Run the Day-35 retrieval settings over the eval set — through the code that serves them.

The rule this module exists to enforce: **the evaluation imports the backend.**
A retrieval number is only worth reporting if it came out of the retriever a
chat turn actually uses, so this harness builds
:class:`app.services.rag.retriever.KnowledgeRetriever` and
:class:`app.services.rag.hybrid.HybridKnowledgeRetriever` over a store ingested
from the real corpus, and times them. Reimplementing BM25, fusion or reranking
in ``ml/`` to make the eval convenient would produce numbers about a system
nobody ships — the exact failure mode AGENTS.md ground rule 6 ("cards before
claims") is there to prevent.

The backend is imported lazily and optionally. ``ml/`` must stay offline and
installable on its own (and CI's ``ml-smoke`` job installs no backend), so:

* :func:`ensure_backend` puts ``backend/`` on ``sys.path`` when it is a sibling
  checkout and raises :class:`HarnessUnavailable` when it is not;
* every setting that needs it is reported as *skipped, with the reason*, rather
  than silently dropped from the report;
* :func:`rank_documents` is the one ranking path that needs no backend at all,
  and it is what contrastive training selects its best epoch on — training must
  not depend on a package that may not be installed.

Two things this harness measures that a plain IR script would not:

* **Latency**, per query, from :func:`time.perf_counter` around the real
  ``await retriever.rank(...)``. The reranker's cost is the reason a deployment
  might not want it, so it is measured rather than assumed.
* **The score behind the ordering**, via ``rank(..., apply_floor=False)``. The
  abstain threshold is fitted on those scores, and ``top_score`` is their
  *maximum* rather than the first one's: the hybrid orders by rank fusion and
  abstains on a cardinal score, and those are deliberately different numbers.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from manovia_ml.config import RetrievalConfig
from manovia_ml.paths import ml_root, resolve
from manovia_ml.retrieval.corpus import Passage
from manovia_ml.retrieval.data import RetrievalQuery
from manovia_ml.retrieval.embeddings import (
    FEATURES_FILE,
    PROJECTION_FILE,
    Encoder,
    LexicalFeatures,
    ProjectionEncoder,
    TfidfEncoder,
    build_encoder,
)
from manovia_ml.retrieval.metrics import QueryResult

__all__ = [
    "BACKEND_RELPATH",
    "SETTINGS",
    "TUNING_FILE",
    "Harness",
    "HarnessUnavailable",
    "SettingSpec",
    "backend_root",
    "encoder_artifact_dir",
    "encoder_for",
    "ensure_backend",
    "language_of",
    "rank_documents",
    "read_tuning",
]

#: The backend checkout, as a path relative to the repository root.
BACKEND_RELPATH = "backend"
#: The calibrated-knobs file the backend also reads at boot.
TUNING_FILE = "tuning.json"


class HarnessUnavailable(RuntimeError):
    """The backend (or one of its extras) is not importable here.

    Raised rather than worked around: a retrieval number produced without the
    serving code would be a number about a different system.
    """


def backend_root() -> Path:
    """``backend/`` in the same checkout as this ``ml/`` tree."""

    return ml_root().parent / BACKEND_RELPATH


def ensure_backend() -> None:
    """Make ``app.*`` importable, or say exactly why it cannot be."""

    try:  # already importable (a dev install, or the backend on PYTHONPATH)
        import app.services.rag.hybrid

        return
    except ImportError:
        pass
    root = backend_root()
    if not (root / "app" / "services" / "rag" / "hybrid.py").is_file():
        raise HarnessUnavailable(
            f"no backend checkout at {root}; the retrieval harness measures the serving "
            "code, so it needs one. Run it from a full checkout, or use "
            "manovia_ml.retrieval.harness.rank_documents for the backend-free path."
        ) from None
    sys.path.insert(0, str(root))
    try:
        import app.services.rag.hybrid  # noqa: F401
    except ImportError as exc:  # a backend dependency is missing (faiss, structlog)
        raise HarnessUnavailable(
            f"backend found at {root} but not importable ({exc}); install the backend "
            "extras into this environment: pip install -e backend[dev]"
        ) from exc


def language_of(row: RetrievalQuery) -> str:
    """The document language a production turn would filter on for this query.

    ``hi-Latn`` and ``bn-Latn`` map to ``hi`` and ``bn``: the retriever's
    language filter is about the *corpus* side, and a romanised Hindi query is
    still a Hindi conversation.
    """

    return row.lang.split("-", 1)[0]


def rank_documents(
    encoder: Encoder,
    query: str,
    passages: Sequence[Passage],
    *,
    limit: int = 5,
) -> tuple[str, ...]:
    """Document ids an encoder ranks best for ``query``, backend-free.

    One card per document (the production rule), scored by the document's best
    passage, ties broken on the chunk id so the ordering is a pure function of
    the inputs. This is the ranking contrastive training selects its best epoch
    on, and the one the report quotes when the backend is not installed.
    """

    if limit < 1 or not passages:
        return ()
    vectors = encoder.encode([query, *(passage.embed_text for passage in passages)])
    query_vector, passage_vectors = vectors[0], vectors[1:]
    best: dict[str, tuple[float, str]] = {}
    for passage, vector in zip(passages, passage_vectors, strict=True):
        score = float(query_vector @ vector)
        current = best.get(passage.doc_id)
        if current is None or (score, passage.chunk_id) > current:
            best[passage.doc_id] = (score, passage.chunk_id)
    ordered = sorted(best.items(), key=lambda item: (-item[1][0], item[1][1], item[0]))
    return tuple(doc_id for doc_id, _ in ordered[:limit])


def read_tuning(path: Path | str) -> dict[str, Any]:
    """``tuning.json`` as plain data: ``{"dev": {...}, "abstain": {...}, ...}``.

    ``ml/`` writes this file and the backend reads it defensively at boot; the
    harness reads it as data so a sweep can start from the fitted values instead
    of from defaults. A missing or unreadable file is an empty dict — the caller
    decides whether that is fatal (for a sweep it is not: it means "untuned").
    """

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@dataclass(frozen=True)
class SettingSpec:
    """One row of the comparison table: a retriever, an encoder, a reranker.

    ``dense_weight`` overrides the fitted fusion weight for exactly one row,
    which is how ``bm25`` (weight 0) and the dev sweep are expressed without a
    second code path.
    """

    name: str
    #: "baseline" (Day-13 dense only) or "hybrid" (fusion, with or without rerank).
    retriever: str = "hybrid"
    #: "hashing" | "tfidf" | "projection" | "sentence_transformers".
    encoder: str = "tfidf"
    #: "none" | "trained" | "untrained".
    reranker: str = "none"
    #: What the report calls this row.
    label: str = ""
    dense_weight: float | None = None
    tuning_overrides: Mapping[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        return self.label or self.name

    @property
    def needs_encoder_artifact(self) -> bool:
        return self.encoder == "projection"

    @property
    def needs_reranker_artifact(self) -> bool:
        return self.reranker == "trained"


#: The rows the Day-35 report quotes, in the order it quotes them. The four the
#: brief asks for are here (baseline, fine-tuned dense, hybrid, hybrid+rerank);
#: the rest exist because a claim like "the reranker is worth its latency" is
#: only checkable against the alternatives somebody would actually pick.
SETTINGS: tuple[SettingSpec, ...] = (
    SettingSpec(
        "baseline",
        retriever="baseline",
        encoder="tfidf",
        label="Day-13 baseline — dense only, off-the-shelf TF-IDF",
    ),
    SettingSpec(
        "dense_finetuned",
        retriever="baseline",
        encoder="projection",
        label="Fine-tuned dense only (contrastive, MNRL + hard negatives)",
    ),
    SettingSpec(
        "bm25",
        encoder="tfidf",
        dense_weight=0.0,
        label="BM25 only (lexical half of the hybrid)",
    ),
    SettingSpec(
        "hybrid",
        encoder="tfidf",
        label="Hybrid — BM25 + off-the-shelf dense, rank fusion",
    ),
    SettingSpec(
        "hybrid_finetuned",
        encoder="projection",
        label="Hybrid — BM25 + fine-tuned dense",
    ),
    SettingSpec(
        "hybrid_rerank_untrained",
        encoder="tfidf",
        reranker="untrained",
        label="Hybrid + hand-set cross-encoder (no fit run)",
    ),
    SettingSpec(
        "hybrid_rerank",
        encoder="tfidf",
        reranker="trained",
        label="Hybrid + trained cross-encoder",
    ),
    SettingSpec(
        "hybrid_finetuned_rerank",
        encoder="projection",
        reranker="trained",
        label="Hybrid (fine-tuned dense) + trained cross-encoder",
    ),
)


class Harness:
    """Builds the retrievers, runs the queries, times them.

    Stores are ingested once per encoder kind into ``workdir`` and reused across
    settings, because ingesting 40 documents through a fitted encoder is the
    expensive part of a sweep and it does not depend on the fusion weight.
    """

    def __init__(
        self,
        config: RetrievalConfig,
        *,
        workdir: Path,
        encoder_dirs: Mapping[str, Path] | None = None,
        reranker_dir: Path | None = None,
        tuning: Mapping[str, Any] | None = None,
    ) -> None:
        self.config = config
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._encoder_dirs = dict(encoder_dirs or {})
        self._reranker_dir = reranker_dir
        self._tuning: dict[str, Any] = dict(tuning or {})
        self._stores: dict[str, tuple[Any, Any]] = {}
        self._retrievers: dict[str, Any] = {}

    # ------------------------------------------------------------ availability

    def unavailable_reason(self, spec: SettingSpec) -> str | None:
        """Why this row cannot be measured here, or ``None`` when it can.

        Reported verbatim in the eval report: "could not run" is a result, and a
        table with a silently missing row is a table that lies.
        """

        try:
            ensure_backend()
        except HarnessUnavailable as exc:
            return f"backend unavailable: {exc}"
        if spec.needs_encoder_artifact:
            directory = self._encoder_dirs.get(spec.encoder)
            if directory is None:
                return (
                    "no fine-tuned encoder: run `make ml-retrieval-train` (or the Colab "
                    "notebook for a real multilingual checkpoint)"
                )
            if not (Path(directory) / "encoder.json").is_file():
                return f"fine-tuned encoder artifact incomplete at {directory}"
        if spec.needs_reranker_artifact:
            directory = self._reranker_dir
            if directory is None or not (Path(directory) / "reranker_weights.json").is_file():
                return "no trained reranker: run `make ml-retrieval-rerank`"
        return None

    # ---------------------------------------------------------------- building

    def _encoder(self, kind: str, fit_texts: Sequence[str]) -> Any:
        """The embedder for a setting: an ml encoder, which *is* a backend embedder.

        ``manovia_ml.retrieval.embeddings.Encoder`` declares the same four
        members as ``app.services.rag.base.Embedder`` (``name``, ``dim``,
        ``resolve_dim``, ``embed``) for exactly this reason — one encoder
        implementation, usable on both sides of the seam, no adapter to keep in
        sync and no second place for a bug to hide.
        """

        if kind == "projection":
            directory = self._encoder_dirs[kind]
            return ProjectionEncoder.load(Path(directory))
        # `model_copy` does not revalidate, so the copy has to be self-consistent
        # by hand: a kind that is not `projection` has no trained artefact, and
        # leaving the configured directory in place would load weights into a
        # setting that claims to be off-the-shelf.
        encoder_config = self.config.encoder.model_copy(update={"kind": kind, "artifact_dir": ""})
        return build_encoder(encoder_config, fit_texts=fit_texts)

    def _store(self, kind: str) -> tuple[Any, Any]:
        """An ingested store and the embedder that built it, per encoder kind."""

        cached = self._stores.get(kind)
        if cached is not None:
            return cached
        from app.content.knowledge import load_knowledge_docs
        from app.services.rag.ingest import run_ingest
        from app.services.rag.store import FaissVectorStore

        persist = self.workdir / f"store-{kind}"
        corpus_dir = self.config.data.corpus_dir.strip()
        docs = load_knowledge_docs(Path(corpus_dir) if corpus_dir else None)
        encoder_holder: dict[str, Any] = {}

        def factory(texts: Sequence[str]) -> Any:
            # The ingest hands the factory the *title-anchored* chunk texts it
            # built, which is the only correct thing to fit a corpus encoder on:
            # a vocabulary fitted on bare bodies would not hold the title terms
            # the query side is anchored on.
            encoder = self._encoder(kind, fit_texts=list(texts))
            encoder_holder["encoder"] = encoder
            return encoder

        run_ingest(
            docs,
            persist_dir=persist,
            embedder_factory=factory,
            embedder_config={"kind": kind, "model_id": self.config.encoder.model_id, "dim": 0},
            chunk_words=self.config.data.chunk_words,
            overlap_words=self.config.data.chunk_overlap_words,
        )
        store = FaissVectorStore.load(persist)
        encoder = encoder_holder.get("encoder")
        if encoder is None:
            # The ingest found the store already up to date and never fitted:
            # rebuild the encoder from the persisted state instead.
            encoder = self._load_fitted_encoder(kind, persist)
        result = (store, encoder)
        self._stores[kind] = result
        return result

    def _load_fitted_encoder(self, kind: str, persist: Path) -> Any:
        """Reload the encoder an ingest persisted, in the format *this* module wrote.

        ``run_ingest`` persists fitted state by calling ``embedder.save(dir)``,
        and the encoders here are the ml ones — so the artefact is
        ``features.joblib`` (plus ``projection.npz`` for a trained projection),
        not the backend's ``embedder.joblib``. Loading it with the backend's
        ``TfidfEmbedder.load`` raised ``EmbedderUnavailable`` two stages after
        the ingest that wrote it, which is the kind of error that sends somebody
        re-ingesting a store that was never broken. The backend format is still
        honoured as a fallback, so a store ingested by ``make ingest`` works too.
        """

        from app.services.rag.embedders import HashingEmbedder, TfidfEmbedder

        if kind == "hashing":
            marker = json.loads((persist / "embedder.json").read_text(encoding="utf-8"))
            return HashingEmbedder(dim=int(marker.get("dim", self.config.encoder.dim)))
        if (persist / PROJECTION_FILE).is_file():
            return ProjectionEncoder.load(persist)
        if (persist / FEATURES_FILE).is_file():
            return TfidfEncoder(LexicalFeatures.load(persist))
        try:
            return TfidfEmbedder.load(persist)
        except Exception as exc:
            raise HarnessUnavailable(
                f"cannot reload a fitted {kind!r} encoder from {persist}: {exc}"
            ) from exc

    def idf(self, spec: SettingSpec) -> dict[str, float]:
        """Term -> IDF over the corpus this row's store was ingested with.

        The offline reranker fitter needs exactly the IDF the serving path uses:
        ``idf_overlap`` is a feature, and a feature computed against a different
        corpus is a different feature wearing the same name.
        """

        from app.services.rag.factory import corpus_idf

        store, _ = self._store(spec.encoder)
        return dict(corpus_idf(store))

    def build(self, spec: SettingSpec) -> Any:
        """The retriever for one row, built the way the factory builds it."""

        cached = self._retrievers.get(spec.name)
        if cached is not None:
            return cached
        reason = self.unavailable_reason(spec)
        if reason is not None:
            raise HarnessUnavailable(reason)
        from app.services.rag.factory import corpus_idf
        from app.services.rag.hybrid import HybridKnowledgeRetriever
        from app.services.rag.rerank import LexicalCrossEncoder, load_cross_encoder
        from app.services.rag.retriever import KnowledgeRetriever

        store, encoder = self._store(spec.encoder)
        floor = self._floor_for(spec.encoder)
        if spec.retriever == "baseline":
            retriever = KnowledgeRetriever(
                store, encoder, min_similarity=floor, fallback_language="en"
            )
        else:
            tuning = self._tuning_for(spec, dense_scale=2.0 * floor)
            reranker: Any = None
            if spec.reranker == "trained":
                if self._reranker_dir is None:  # unavailable_reason checked first
                    raise HarnessUnavailable("no reranker artifact directory configured")
                reranker = load_cross_encoder(self._reranker_dir, idf=corpus_idf(store))
            elif spec.reranker == "untrained":
                reranker = LexicalCrossEncoder.default(idf=corpus_idf(store))
            retriever = HybridKnowledgeRetriever(
                store,
                encoder,
                reranker=reranker,
                tuning=tuning,
                min_similarity=floor,
                fallback_language="en",
                language_filter=self.config.hybrid.language_filter,
            )
        self._retrievers[spec.name] = retriever
        return retriever

    def _floor_for(self, kind: str) -> float:
        """The dense floor for an encoder kind, from the backend's own table."""

        from app.services.rag.embedders import RECOMMENDED_MIN_SIMILARITY

        return float(RECOMMENDED_MIN_SIMILARITY.get(kind, 0.25))

    def _tuning_for(self, spec: SettingSpec, *, dense_scale: float) -> Any:
        """The fitted knobs, with this row's overrides applied on top."""

        from app.services.rag.hybrid import HybridTuning

        dev = self._tuning.get("dev")
        values: dict[str, Any] = dict(dev) if isinstance(dev, dict) else {}
        abstain = self._tuning.get("abstain")
        if isinstance(abstain, dict):
            key = "hybrid_rerank" if spec.reranker == "trained" else "hybrid"
            if key in abstain:
                values["abstain_score"] = abstain[key]
        values.pop("embedder_kind", None)
        values.pop("dense_scale", None)
        values.update(spec.tuning_overrides)
        if spec.dense_weight is not None:
            values["dense_weight"] = spec.dense_weight
        known = set(HybridTuning.__dataclass_fields__)
        usable = {key: value for key, value in values.items() if key in known}
        return replace(HybridTuning(**usable), dense_scale=dense_scale)

    # ------------------------------------------------------------------ running

    def run(
        self,
        queries: Sequence[RetrievalQuery],
        spec: SettingSpec,
        *,
        limit: int = 5,
        repeats: int = 3,
        language_filter: bool | None = None,
    ) -> list[QueryResult]:
        """Every query through one setting, with a latency per query.

        ``apply_floor=False``: ranking quality and the abstain decision are
        measured from the same run, on the scores the floor would be applied to.
        Filtering first would make the threshold unfittable — there would be no
        score to threshold on the queries where the system says nothing.
        """

        retriever = self.build(spec)
        filtered = (
            self.config.hybrid.language_filter if language_filter is None else language_filter
        )
        results: list[QueryResult] = []
        for row in queries:
            language = language_of(row) if filtered else None
            started = time.perf_counter()
            error: str | None = None
            ranked: list[Any] = []
            try:
                for _ in range(max(1, repeats)):
                    ranked = asyncio.run(
                        retriever.rank(row.query, limit=limit, language=language, apply_floor=False)
                    )
            except Exception as exc:  # reported per row, never silently dropped
                error = f"{type(exc).__name__}: {exc}"
            elapsed_ms = (time.perf_counter() - started) * 1000.0 / max(1, repeats)
            scores = [float(item.score) for item in ranked]
            results.append(
                QueryResult(
                    query_id=row.id,
                    lang=row.lang,
                    style=row.style,
                    relevant=tuple(row.relevant_docs),
                    retrieved=tuple(item.source_id for item in ranked[:limit]),
                    top_score=max(scores) if scores else None,
                    latency_ms=elapsed_ms,
                    error=error,
                )
            )
        return results

    def sweep(
        self,
        queries: Sequence[RetrievalQuery],
        spec: SettingSpec,
        weights: Sequence[float],
        *,
        limit: int = 5,
        score: Callable[[Sequence[QueryResult]], float],
    ) -> list[tuple[float, float]]:
        """``(dense_weight, score)`` for every weight, on one split.

        Each weight gets its own retriever instance (a fresh name in the cache),
        because the fusion weight is baked into the retriever rather than passed
        per query — which is exactly how production holds it.
        """

        out: list[tuple[float, float]] = []
        for weight in weights:
            variant = SettingSpec(
                name=f"{spec.name}__w{weight:.2f}",
                retriever=spec.retriever,
                encoder=spec.encoder,
                reranker=spec.reranker,
                label=f"{spec.describe()} @ dense_weight={weight:.2f}",
                dense_weight=weight,
                tuning_overrides=spec.tuning_overrides,
            )
            results = self.run(queries, variant, limit=limit, repeats=1)
            out.append((weight, score(results)))
        return out


def encoder_artifact_dir(config: RetrievalConfig) -> Path:
    """Where a fitted encoder lives (gitignored: weights never enter Git)."""

    configured = config.encoder.artifact_dir.strip()
    if configured:
        return resolve(configured)
    return resolve(f"data/retrieval_artifacts/{config.name}")


def encoder_for(config: RetrievalConfig) -> Encoder:
    """The encoder a config names, fitted or loaded (a thin, testable wrapper)."""

    directory = encoder_artifact_dir(config)
    if config.encoder.kind == "projection" and (directory / "encoder.json").is_file():
        return ProjectionEncoder.load(directory)
    return build_encoder(config.encoder)
