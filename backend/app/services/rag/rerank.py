"""The reranker stage: a cross-encoder over the fused top-N candidates.

A bi-encoder (the dense retriever) has to compress a whole passage into one
vector *before* it ever sees the query, which is why it is fast and why it is
wrong in the same places every time: it cannot notice that the query says
"before a hard moment" and the passage says "walking into an exam hall" unless
some dimension was reserved for that pairing in advance. A cross-encoder reads
the query *and* the passage together and can.

Two implementations sit behind :class:`CrossEncoder`:

* :class:`LexicalCrossEncoder` — the one this repository can run offline, and
  the one the Day-35 numbers are measured with. It is a **linear model over
  interaction features**: query/passage token overlap, IDF-weighted overlap,
  title overlap, word-bigram overlap, character-trigram overlap (the
  script-robust one, and the reason a romanised query can still be scored
  against a Devanagari passage by the *rest* of the evidence), the script match,
  the tag match, and the three first-stage scores (dense cosine, BM25, fused
  confidence). Trained, it is a logistic regression whose coefficients live in a
  local artefact directory; untrained, it uses :data:`DEFAULT_WEIGHTS` and says
  so in its ``name``.
* :class:`TransformerCrossEncoder` — the real thing: a multilingual
  cross-encoder checkpoint (or a distilled one trained in
  ``ml/notebooks/colab_retrieval_finetune.ipynb``), imported lazily and only
  ever paid for on the first call, exactly like
  :class:`~app.services.rag.embedders.SentenceTransformerEmbedder`.

The output is a probability in ``[0, 1]`` either way, because the abstain
threshold is fitted on it: "the best candidate scored 0.31" has to mean the same
kind of thing whether the reranker is a logistic regression or a distilled
MiniLM, or the calibration is not transferable.

Cost: this is the stage that adds latency, and the Day-35 report measures it
rather than assuming it (top-20 candidates, one feature extraction each, on one
CPU: a couple of milliseconds for the lexical model, ~100-300 ms for a
transformer — which is why the transformer version is opt-in).
"""

from __future__ import annotations

import itertools
import json
import math
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog

from app.services.rag.embedders import tokenize

_log = structlog.get_logger()

__all__ = [
    "BM25_SATURATION_PIVOT",
    "DEFAULT_WEIGHTS",
    "FEATURE_NAMES",
    "FEATURE_VERSION",
    "RERANKER_METADATA_FILE",
    "RERANKER_WEIGHTS_FILE",
    "CrossEncoder",
    "CrossEncoderError",
    "CrossEncoderUnavailable",
    "LexicalCrossEncoder",
    "NullCrossEncoder",
    "RerankCandidate",
    "TransformerCrossEncoder",
    "cross_encoder_features",
    "load_cross_encoder",
    "saturate_bm25",
]

#: The features, in the order the coefficient vector is stored. Adding one is a
#: breaking change to any saved artefact, so ``FEATURE_VERSION`` travels with it
#: and a mismatch refuses to load rather than scoring with shifted columns.
FEATURE_NAMES: tuple[str, ...] = (
    "bias",
    "fused_confidence",
    "dense_cosine",
    "bm25_saturated",
    "token_overlap",
    "idf_overlap",
    "title_overlap",
    "bigram_overlap",
    "char_trigram_overlap",
    "script_match",
    "tag_match",
    "log_query_length",
)
FEATURE_VERSION = 1

#: Saturation pivot for the ``bm25_saturated`` feature: ``score / (pivot + score)``.
#: BM25 on this corpus puts a strong multi-term match around 8-13 and a weak
#: one under 2, so a pivot of 5 spreads those over ~0.3-0.7 instead of squeezing
#: everything into 0.6-0.95 the way a pivot of 1 does (measured, Day 35: the
#: out-of-scope query "best crypto to buy today" saturated at 0.77 with pivot 1
#: and at 0.40 with pivot 5). The pivot is part of the feature contract, so it
#: travels in the artefact metadata and a mismatch refuses to load — changing it
#: silently would rescale every coefficient that was fitted against it.
BM25_SATURATION_PIVOT = 5.0

#: Metadata file inside a trained reranker artefact directory.
RERANKER_METADATA_FILE = "reranker.json"
#: Coefficient file (a JSON vector — not a pickle, so the numbers a deployment
#: serves can be read, diffed and reviewed without loading code).
RERANKER_WEIGHTS_FILE = "reranker_weights.json"

#: Hand-set weights for the untrained scorer, in ``FEATURE_NAMES`` order.
#: Deliberately conservative: it leans on the first stage (which is measured)
#: and adds the two overlap features that are hardest to fake (IDF-weighted
#: overlap and character trigrams). The trained coefficients replace all of it;
#: this exists so ``RETRIEVER=hybrid_rerank`` is never *unusable* before anyone
#: has run a fit, and its ``name`` says "untrained" so a report cannot mistake
#: one for the other.
DEFAULT_WEIGHTS: tuple[float, ...] = (
    -3.10,  # bias: an unsupported candidate should land near 0.05, not 0.5
    0.60,  # fused_confidence — deliberately *small*, see the note below
    1.70,  # dense_cosine
    1.70,  # bm25_saturated
    1.80,  # token_overlap
    2.40,  # idf_overlap
    1.00,  # title_overlap
    1.00,  # bigram_overlap
    1.40,  # char_trigram_overlap
    0.20,  # script_match
    0.40,  # tag_match
    0.00,  # log_query_length
)
# Why `fused_confidence` carries almost no weight here: it is an *ordinal*
# number. Reciprocal rank fusion gives a chunk that both systems ranked first a
# confidence of ~1.0 whether the query was "how do I stop a panic attack" or
# "best crypto to buy today" — measured on the Day-35 corpus, where the
# out-of-scope crypto query fused at 0.995. Leaning on it would make the
# untrained scorer unable to abstain, which is the one thing it must still do
# before anybody has fitted coefficients. So it leans on the cardinal evidence
# (cosine, saturated BM25, lexical overlap) and treats the rank agreement as a
# mild tie-breaker. The trained reranker learns its own balance from data.


class CrossEncoderError(RuntimeError):
    """Base class for "this cross-encoder cannot be used here"."""


class CrossEncoderUnavailable(CrossEncoderError):
    """The requested cross-encoder is not usable (artefact missing, mismatched).

    Mirrors :class:`~app.services.rag.base.EmbedderUnavailable`: the caller
    decides what to fall back to. The factory downgrades ``RETRIEVER=
    hybrid_rerank`` to plain ``hybrid`` rather than serving hand-set
    coefficients under a threshold that was calibrated on fitted ones — a
    retriever that still works, and a log line that says what happened.
    """


@dataclass(frozen=True)
class RerankCandidate:
    """Everything the reranker is allowed to know about one candidate.

    The first-stage scores travel with the candidate on purpose: a reranker that
    ignores how the candidate got here throws away the one piece of evidence
    that was computed over the whole corpus.
    """

    chunk_id: str
    doc_id: str
    title: str
    text: str
    language: str
    tags: tuple[str, ...]
    dense_score: float = 0.0
    bm25_score: float = 0.0
    fused_confidence: float = 0.0


@runtime_checkable
class CrossEncoder(Protocol):
    """Scores (query, candidate) pairs; higher is better, in [0, 1]."""

    name: str

    def score(self, query: str, candidates: Sequence[RerankCandidate]) -> list[float]: ...


class NullCrossEncoder:
    """Pass-through: the fused confidence is the score, and nothing is reranked.

    This is how ``RETRIEVER=hybrid`` says "no reranker" —
    :class:`~app.services.rag.hybrid.HybridKnowledgeRetriever` checks for it and
    then keeps the fusion's own order, scoring candidates with
    :func:`~app.services.rag.hybrid.fused_evidence` instead (the RRF confidence
    is ordinal, so it cannot be a threshold). The two settings therefore differ
    by exactly one stage, and the measured difference is that stage's.
    """

    name = "null"

    def score(self, query: str, candidates: Sequence[RerankCandidate]) -> list[float]:
        del query
        return [max(0.0, min(1.0, candidate.fused_confidence)) for candidate in candidates]


# --------------------------------------------------------------------- #
# Features                                                                #
# --------------------------------------------------------------------- #

_DEVANAGARI = "\u0900-\u097f"
_BENGALI = "\u0980-\u09ff"
_SCRIPT_OF_LANGUAGE = {"en": "latin", "hi": "devanagari", "bn": "bengali"}


def _dominant_script(text: str) -> str:
    counts = {"latin": 0, "devanagari": 0, "bengali": 0}
    for character in text:
        code = ord(character)
        if 0x0900 <= code <= 0x097F:
            counts["devanagari"] += 1
        elif 0x0980 <= code <= 0x09FF:
            counts["bengali"] += 1
        elif character.isalpha():
            counts["latin"] += 1
    return max(counts, key=lambda key: (counts[key], key))


def _bigrams(tokens: Sequence[str]) -> set[str]:
    return {f"{a}_{b}" for a, b in itertools.pairwise(tokens)}


def _trigrams(text: str) -> set[str]:
    folded = "".join(text.casefold().split())
    return {folded[i : i + 3] for i in range(max(0, len(folded) - 2))}


def _overlap(numerator: Iterable[str], denominator_size: int) -> float:
    if denominator_size <= 0:
        return 0.0
    return min(1.0, len(set(numerator)) / denominator_size)


def cross_encoder_features(
    query: str,
    candidate: RerankCandidate,
    *,
    idf: Mapping[str, float] | None = None,
) -> list[float]:
    """The feature vector, in :data:`FEATURE_NAMES` order.

    Every feature is in ``[0, 1]`` except the bias and ``log_query_length``, so
    a coefficient is readable as "how much this evidence moves the probability".
    """

    query_tokens = [token for token in tokenize(query) if token]
    passage_tokens = set(tokenize(candidate.text)) | set(tokenize(candidate.title))
    title_tokens = set(tokenize(candidate.title))
    tag_tokens = {part for tag in candidate.tags for part in tokenize(tag.replace("-", " "))}

    shared = [token for token in query_tokens if token in passage_tokens]
    total_idf = sum((idf or {}).get(token, 1.0) for token in query_tokens) or 1.0
    matched_idf = sum((idf or {}).get(token, 1.0) for token in shared)

    query_trigrams = _trigrams(query)
    passage_trigrams = _trigrams(candidate.text[:1200]) | _trigrams(candidate.title)
    script_match = (
        1.0
        if _dominant_script(query) == _SCRIPT_OF_LANGUAGE.get(candidate.language, "latin")
        else 0.0
    )

    return [
        1.0,
        _clip(candidate.fused_confidence),
        _clip(candidate.dense_score),
        _clip(saturate_bm25(candidate.bm25_score)),
        _overlap(shared, len(query_tokens)),
        _clip(matched_idf / total_idf),
        _overlap([token for token in query_tokens if token in title_tokens], len(query_tokens)),
        _overlap(
            _bigrams(query_tokens) & _bigrams(sorted(passage_tokens)), len(_bigrams(query_tokens))
        ),
        _overlap(query_trigrams & passage_trigrams, len(query_trigrams)),
        script_match,
        _overlap([token for token in query_tokens if token in tag_tokens], len(query_tokens)),
        math.log1p(len(query_tokens)),
    ]


def saturate_bm25(score: float) -> float:
    """Map an unbounded BM25 score into ``[0, 1)`` with :data:`BM25_SATURATION_PIVOT`.

    Shared with :func:`app.services.rag.hybrid.fused_evidence` so that the
    number a deployment abstains on means the same thing before and after a
    reranker is fitted.
    """

    if score <= 0.0:
        return 0.0
    return _clip(score / (BM25_SATURATION_PIVOT + score))


def _clip(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


# --------------------------------------------------------------------- #
# Implementations                                                       #
# --------------------------------------------------------------------- #


class LexicalCrossEncoder:
    """A linear cross-encoder over :func:`cross_encoder_features`."""

    def __init__(
        self,
        weights: Sequence[float],
        *,
        idf: Mapping[str, float] | None = None,
        name: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        resolved = [float(weight) for weight in weights]
        if len(resolved) != len(FEATURE_NAMES):
            raise ValueError(f"expected {len(FEATURE_NAMES)} weights, got {len(resolved)}")
        self.weights = tuple(resolved)
        self._idf = dict(idf or {})
        self.metadata: dict[str, Any] = dict(metadata or {})
        self.name = name or (
            "lexical-cross-encoder-v1"
            if self.metadata.get("trained")
            else "lexical-cross-encoder-v1:untrained"
        )

    @classmethod
    def default(cls, *, idf: Mapping[str, float] | None = None) -> LexicalCrossEncoder:
        """The untrained scorer: documented hand-set weights."""

        return cls(DEFAULT_WEIGHTS, idf=idf, metadata={"trained": False})

    def logits(self, query: str, candidate: RerankCandidate) -> float:
        features = cross_encoder_features(query, candidate, idf=self._idf)
        return sum(weight * feature for weight, feature in zip(self.weights, features, strict=True))

    def score(self, query: str, candidates: Sequence[RerankCandidate]) -> list[float]:
        return [_sigmoid(self.logits(query, candidate)) for candidate in candidates]


class TransformerCrossEncoder:
    """A real cross-encoder checkpoint, loaded lazily and only once."""

    def __init__(self, model_id: str, *, device: str = "cpu", batch_size: int = 16) -> None:
        model_id = model_id.strip()
        if not model_id:
            raise ValueError("model_id must not be empty")
        self.model_id = model_id
        self.device = device
        self.batch_size = max(1, batch_size)
        self.name = f"cross-encoder:{model_id}"
        self._model: Any | None = None
        self._lock = threading.Lock()

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise CrossEncoderUnavailable(
                    "sentence-transformers is not installed; install the 'rag' extra, or use "
                    "RETRIEVER=hybrid (no reranker) / the offline lexical reranker"
                ) from exc
            try:
                self._model = CrossEncoder(self.model_id, device=self.device)
            except Exception as exc:
                raise CrossEncoderUnavailable(
                    f"could not load cross-encoder {self.model_id!r}: {type(exc).__name__}. "
                    "A checkpoint needs Hugging Face access; train or download it with "
                    "ml/notebooks/colab_retrieval_finetune.ipynb and point "
                    "RETRIEVER_RERANKER_MODEL_ID at the local directory."
                ) from exc
            _log.info("rag_reranker_loaded", reranker=self.name)
        return self._model

    def score(self, query: str, candidates: Sequence[RerankCandidate]) -> list[float]:
        if not candidates:
            return []
        model = self._ensure_model()
        pairs = [[query, f"{candidate.title}\n{candidate.text}"] for candidate in candidates]
        raw = model.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)
        return [_sigmoid(float(value)) for value in raw]


def load_cross_encoder(
    directory: Path | str, *, idf: Mapping[str, float] | None = None
) -> LexicalCrossEncoder:
    """Load trained coefficients from an artefact directory.

    The weights are JSON, not a pickle: the numbers a deployment serves should be
    readable and reviewable without executing anything, and a JSON vector can be
    diffed in a pull request. Raises :class:`CrossEncoderUnavailable` when the
    directory is missing, incomplete, or was written by a different feature
    version — the caller decides what to fall back to, and
    :func:`app.services.rag.factory.build_knowledge_retriever` falls back to the
    untrained scorer with a warning rather than serving a shifted model.

    The return type is the concrete class rather than the :class:`CrossEncoder`
    protocol because loading *is* provenance: whoever reads an artefact off disk
    needs ``metadata`` — which split it was fitted on, which feature version, which
    BM25 pivot — and the protocol, which only promises ``name`` and ``score``, would
    make every caller cast to get it.
    """

    path = Path(directory)
    weights_path = path / RERANKER_WEIGHTS_FILE
    metadata_path = path / RERANKER_METADATA_FILE
    if not weights_path.is_file():
        raise CrossEncoderUnavailable(f"no reranker weights at {weights_path}")
    try:
        payload = json.loads(weights_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossEncoderUnavailable(f"{weights_path}: unreadable: {exc}") from exc
    names = [str(name) for name in payload.get("features", [])]
    if names != list(FEATURE_NAMES):
        raise CrossEncoderUnavailable(
            f"{weights_path}: feature names {names[:3]}… do not match this build's "
            f"{list(FEATURE_NAMES)[:3]}…; retrain the reranker"
        )
    coefficients = payload.get("weights")
    if not isinstance(coefficients, list) or len(coefficients) != len(FEATURE_NAMES):
        raise CrossEncoderUnavailable(f"{weights_path}: expected {len(FEATURE_NAMES)} weights")
    metadata: dict[str, Any] = {"trained": True, "artifact_dir": str(path)}
    if metadata_path.is_file():
        try:
            extra = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            extra = {}
        if isinstance(extra, dict):
            metadata.update(extra)
    version = int(metadata.get("feature_version", FEATURE_VERSION))
    if version != FEATURE_VERSION:
        raise CrossEncoderUnavailable(
            f"{path}: artefact feature_version {version}, this build is {FEATURE_VERSION}"
        )
    pivot = float(metadata.get("bm25_saturation_pivot", BM25_SATURATION_PIVOT))
    if abs(pivot - BM25_SATURATION_PIVOT) > 1e-9:
        raise CrossEncoderUnavailable(
            f"{path}: artefact was fitted with bm25_saturation_pivot {pivot}, this build "
            f"saturates at {BM25_SATURATION_PIVOT}; the coefficients would be rescaled"
        )
    return LexicalCrossEncoder([float(value) for value in coefficients], idf=idf, metadata=metadata)
