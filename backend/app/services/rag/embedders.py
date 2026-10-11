"""Embedders: turning chunk and query text into vectors.

Three implementations sit behind one :class:`~app.services.rag.base.Embedder`
protocol (AGENTS.md: every external dependency sits behind an interface with
an offline stand-in):

* **SentenceTransformerEmbedder** — the real thing: a multilingual
  ``sentence-transformers`` checkpoint named by ``RAG_EMBEDDING_MODEL_ID``
  (env; never hard-coded — AGENTS.md). It ships in the optional ``rag``
  extra, loads lazily on first use, and needs the model weights, which means
  a Hugging Face download the first time.
* **TfidfEmbedder** — the offline workhorse: scikit-learn's TF-IDF over word
  1-2 grams, **fitted on the corpus at ingest** and persisted with the store,
  so queries are embedded in exactly the space the chunks live in. IDF is
  what makes lexical retrieval shapable: words that appear everywhere
  ("what", "help", "people") are discounted and the words that name a topic
  dominate. Measured on this corpus, it separates on-topic from off-topic
  queries cleanly where raw feature hashing could not (ADR 0013).
* **HashingEmbedder** — the zero-state fallback: deterministic feature
  hashing, no fit, no persistence. Deliberately weak against full-length
  chunks (no IDF), which is why it is an escape hatch (``RAG_EMBEDDER=hashing``)
  and a unit-test tool rather than the default.

``build_embedder_factory`` picks per ``RAG_EMBEDDER``; ``auto`` (the default)
uses the real model when its package is importable and fits TF-IDF otherwise.
The factory receives the chunk texts because the TF-IDF embedder needs them
to fit. Whatever an ingest builds with is recorded in ``embedder.json``
next to the store, and the retrieval side loads **that** — the store itself
is the source of truth for its own vector space, so a settings change can
never silently query an index in the wrong space.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final, cast

import numpy as np
import structlog

from app.services.rag.base import Embedder, EmbedderUnavailable

_log = structlog.get_logger()

#: Word-ish tokens: letters and digits in any script **plus** the Devanagari
#: and Bengali combining-mark ranges. The Day 6 lesson: Python's ``\w`` stops
#: at combining marks (Unicode Mn), so a bare ``\w+`` cannot match an Indic
#: word — "अकेला" would split into "अक" and "ल" and never match anything.
_TOKEN_RE = re.compile(r"[^\W_]+|[\u0900-\u097F\u0980-\u09FF]+", re.UNICODE)
#: The same character class for the sklearn vectorizer (which rejects
#: capture groups in ``token_pattern``, hence no parentheses anywhere).
_TFIDF_TOKEN_PATTERN = r"[^\W_]+|[\u0900-\u097F\u0980-\u09FF]+"

#: Small English stopword list — including question words, which carry no
#: topic and otherwise dominate short queries. Indic languages get no
#: stopword list: the corpus's hi/bn documents are few, and a wrong stopword
#: there costs more than the noise it removes.
_STOPWORDS: frozenset[str] = frozenset(
    (
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "having",
        "i",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "you",
        "your",
        "me",
        "my",
        "we",
        "our",
        "they",
        "them",
        "he",
        "she",
        "his",
        "her",
        "if",
        "then",
        "than",
        "so",
        "not",
        "no",
        "do",
        "does",
        "did",
        "doing",
        "can",
        "could",
        "would",
        "should",
        "shall",
        "may",
        "might",
        "must",
        "about",
        "into",
        "over",
        "under",
        "again",
        "more",
        "most",
        "some",
        "such",
        "only",
        "own",
        "same",
        "too",
        "very",
        "just",
        "dont",
        "cant",
        "wont",
        "im",
        "ive",
        "youll",
        "youre",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "how",
        # Corpus-noise words: near-ubiquitous in this knowledge base, so they
        # carry no topic signal and compress every cosine score toward the
        # middle. Kept deliberately small and reviewed against the corpus.
        "keep",
        "keeps",
        "keeping",
        "felt",
        "feels",
        "get",
        "gets",
        "really",
        "even",
        "still",
        "always",
        "much",
        "many",
        "lot",
        "day",
        "days",
        "week",
        "weeks",
        # Self-help meta words: in this corpus they are always metaphorical
        # ("it does not fix the problem") or scaffolding, so as query words
        # they collide across unrelated intents.
        "fix",
        "fixes",
        "fixed",
        "fixing",
        "stop",
        "thing",
        "things",
        "way",
        "ways",
    )
)

_HASH_PREFIX = "manovia-rag-v1:"

#: The fitted TF-IDF embedder is persisted alongside the store under this name.
TFIDF_EMBEDDER_FILE: Final = "embedder.joblib"
#: Marker describing the embedder a store was built with; the retrieval side
#: reads it so it reconstructs the same vector space.
EMBEDDER_MARKER_FILE: Final = "embedder.json"

#: Embedder kinds, mirrored by the settings validator in app.core.config.
EMBEDDER_KINDS: Final = ("auto", "sentence_transformers", "tfidf", "hashing")

#: Bump when any embedder's fit or embed behaviour changes in a way that
#: yields different vectors for the same text (stopword list, n-grams, model
#: card). Part of the ingest signature, so the change forces a rebuild instead
#: of leaving a store whose vectors no longer match what today's code computes.
EMBEDDER_FIT_VERSION: Final = 4

#: Similarity floors that suit each embedding space when the deployment has
#: not set RAG_MIN_SIMILARITY explicitly. The TF-IDF figure is measured on
#: this corpus (ADR 0013); the sentence-transformers figure is the usual
#: cosine neighbourhood for related short passages and should be re-checked
#: once the real checkpoint is installed (see PROGRESS.md "needs a human").
RECOMMENDED_MIN_SIMILARITY: Final[dict[str, float]] = {
    "sentence_transformers": 0.35,
    # Measured on this corpus (ADR 0013): with title anchoring and feature
    # capping, on-topic queries score 0.11-0.25 and out-of-domain ones
    # ~0-0.14, with heavy overlap. The floor sits between, deliberately
    # biased toward recall for a *fallback* embedder: a missed card just
    # shortens an answer, and the v2 prompt explicitly tells the model to
    # ignore references that do not fit, so a marginal card is low-harm.
    "tfidf": 0.12,
    "hashing": 0.25,
}


class HashingEmbedder:
    """Deterministic feature-hashing embedder. Offline, fast, fit-free.

    Every word unigram and bigram (lowercased, stopwords dropped for English
    function words and question words) is hashed to a bucket with a sign; the
    vector is L2-normalised. The same text always yields the same vector on
    every machine and every run.

    Honest limitation, kept here because the class ships: without IDF, a
    short query's overlap with a long chunk is a small fraction of the
    chunk's energy, so cosine scores are low for everything and the
    separation between related and unrelated queries is poor on full-length
    documents. Use it for tests and as a fallback, not for production
    retrieval — that is what TfidfEmbedder (offline) and
    SentenceTransformerEmbedder (semantic) are for.
    """

    def __init__(self, dim: int = 384) -> None:
        if dim < 64:
            raise ValueError("hashing embedder needs at least 64 dimensions")
        self.dim = dim
        self.name = f"hashing-v1:{dim}"

    def resolve_dim(self) -> int:
        return self.dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = [token for token in _TOKEN_RE.findall(text.casefold()) if token not in _STOPWORDS]
        features: list[str] = list(tokens)
        features.extend(f"{a} {b}" for a, b in itertools.pairwise(tokens))
        for feature in features:
            digest = hashlib.blake2b(f"{_HASH_PREFIX}{feature}".encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "little")
            bucket = value % self.dim
            sign = 1.0 if (value >> 63) & 1 else -1.0
            vec[bucket] += sign
        norm = sum(v * v for v in vec) ** 0.5
        if norm == 0.0:
            return vec  # all-zero: every similarity is 0, below any threshold
        return [v / norm for v in vec]


class TfidfEmbedder:
    """TF-IDF (word 1-2 grams, sublinear tf) fitted on the corpus, offline.

    Fitted at ingest on the planned chunk texts, persisted with the store
    (joblib), and loaded by the retrieval side so queries land in the same
    vector space. The identity string hashes the fitted vocabulary and idf
    vector, so the same corpus always yields the same name — which is what
    makes ingest idempotent even though this embedder is corpus-fitted.

    Two calibration choices, both measured on this corpus (ADR 0013):

    * **Stopwords + min_df=2.** Function words and corpus-ubiquitous content
      words carried no topic signal and compressed every score toward the
      middle, drowning the discriminative ones.
    * **Feature capping (:data:`FEATURE_CAP`).** After L2 normalisation each
      feature is clipped and the vector re-normalised, in the spirit of
      BM25's term saturation: without it, one shared *rare* word ("fix" in
      "how to fix a leaking tap") could outweigh three shared topic words
      and manufacture relevance out of a coincidence. With it, a single-term
      collision tops out below the serving floor, and topical matches --
      which share several terms -- clear it.
    """

    #: Post-normalisation feature ceiling (see class docstring).
    FEATURE_CAP: float = 0.20

    def __init__(self) -> None:
        # Any: scikit-learn's type surface is partial; the vectorizer is
        # created lazily and its exact class is an implementation detail.
        self._vec: Any | None = None
        self.dim: int = 0
        self.name: str = "tfidf-unfitted"

    def fit(self, texts: Sequence[str]) -> TfidfEmbedder:
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            sublinear_tf=True,
            token_pattern=_TFIDF_TOKEN_PATTERN,
            stop_words=sorted(_STOPWORDS),
            min_df=2,
            max_features=30000,
            norm="l2",
        )
        vectorizer.fit(list(texts))
        self._attach(vectorizer)
        return self

    def _attach(self, vectorizer: Any) -> None:
        self._vec = vectorizer
        self.dim = len(vectorizer.vocabulary_)
        self.name = f"tfidf-v1:{self._fit_hash(vectorizer)}"

    @staticmethod
    def _fit_hash(vectorizer: Any) -> str:
        # Cast every numpy scalar to a plain Python number before hashing:
        # json.dumps refuses numpy types, and the hash must be stable across
        # runs for ingest idempotence.
        payload = json.dumps(
            {
                "vocab": sorted((str(k), int(v)) for k, v in vectorizer.vocabulary_.items()),
                "idf": [round(float(v), 6) for v in vectorizer.idf_],
            },
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.blake2b(payload, digest_size=8).hexdigest()[:12]

    @property
    def fitted(self) -> bool:
        return self._vec is not None

    def resolve_dim(self) -> int:
        if self._vec is None:
            raise EmbedderUnavailable("tfidf embedder used before fit()")
        return self.dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._vec is None:
            raise EmbedderUnavailable("tfidf embedder used before fit()")
        if not texts:
            return []
        matrix = self._vec.transform(list(texts)).toarray()
        capped = np.minimum(matrix, self.FEATURE_CAP)
        norms = np.linalg.norm(capped, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0  # an all-zero row stays all-zero
        return cast("list[list[float]]", (capped / norms).tolist())

    def save(self, directory: Path) -> None:
        """Persist the fitted vectorizer. Ingest-only; requires fit()."""
        if self._vec is None:
            raise EmbedderUnavailable("nothing to save: tfidf embedder is not fitted")
        import joblib

        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump(self._vec, directory / TFIDF_EMBEDDER_FILE)

    @classmethod
    def load(cls, directory: Path) -> TfidfEmbedder:
        import joblib

        try:
            vectorizer = joblib.load(directory / TFIDF_EMBEDDER_FILE)
        except OSError as exc:  # missing or unreadable artifact
            raise EmbedderUnavailable(
                f"no fitted tfidf embedder in {directory}; re-run the ingest"
            ) from exc
        embedder = cls()
        embedder._attach(vectorizer)
        return embedder


class SentenceTransformerEmbedder:
    """The real embedder: a multilingual sentence-transformers checkpoint.

    The package and the checkpoint load lazily on first use — constructing
    this class never touches the network, so a deployment can configure it
    and only pay (or fail) when the first embedding is asked for. The load
    is guarded by a lock so concurrent first calls cannot build two models,
    and a failed load is retried next call rather than cached, because the
    usual causes (no network yet, disk full) can be transient.
    """

    def __init__(
        self,
        model_id: str,
        *,
        device: str = "cpu",
        batch_size: int = 16,
    ) -> None:
        model_id = model_id.strip()
        if not model_id:
            raise ValueError("model_id must not be empty")
        self.model_id = model_id
        self.device = device
        self.batch_size = max(1, batch_size)
        # The true dim is only known once the model has loaded; loading sets it.
        self.dim: int = 0
        self.name = f"sentence-transformers:{model_id}"
        # Any because the sentence-transformers package ships no type
        # information (same approach as transformers in app/services/nlp/hf.py).
        self._model: Any | None = None
        self._lock = threading.Lock()

    def resolve_dim(self) -> int:
        """Load the model if needed, then report its vector size."""
        self._ensure_model()
        return self.dim

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbedderUnavailable(
                    "sentence-transformers is not installed; install the 'rag' "
                    "extra, or set RAG_EMBEDDER=tfidf to run offline"
                ) from exc
            try:
                self._model = SentenceTransformer(self.model_id, device=self.device)
            except Exception as exc:
                raise EmbedderUnavailable(
                    f"could not load embedding model {self.model_id!r} "
                    "(download failed or the id is wrong); "
                    "set RAG_EMBEDDER=tfidf to run offline"
                ) from exc
            dim = int(getattr(self._model, "get_sentence_embedding_dimension", lambda: 0)())
            if dim <= 0:
                raise EmbedderUnavailable(f"could not read a vector size from {self.model_id!r}")
            self.dim = dim
            _log.info("rag_embedder_loaded", embedder=self.name, dim=dim)
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._ensure_model()
        if not texts:
            return []
        vectors = model.encode(
            list(texts),
            batch_size=self.batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        self.dim = int(vectors.shape[1])
        return [row.tolist() for row in vectors]


# --------------------------------------------------------------------- #
# Factory: kind + config -> an embedder builder                            #
# --------------------------------------------------------------------- #


#: What the ingest writes to ``embedder.json`` and the retrieval side reads.
def embedder_marker(kind: str, *, model_id: str, dim: int) -> dict[str, str | int]:
    """The identity of an embedder *configuration* (pre-fit for tfidf)."""
    kind = kind.strip().casefold()
    return {"kind": kind, "model_id": model_id, "dim": dim, "fit_version": EMBEDDER_FIT_VERSION}


def build_embedder_factory(
    kind: str, *, model_id: str, dim: int
) -> Callable[[Sequence[str]], Embedder]:
    """Build the embedder factory ``RAG_EMBEDDER`` asks for.

    The factory maps planned chunk texts to a ready-to-use embedder: the
    TF-IDF embedder fits itself on those texts; the other kinds ignore them.
    ``auto`` prefers the sentence-transformers checkpoint when its package
    imports and falls back to fitting TF-IDF — no network is touched at
    factory-build time, so the choice is safe to make inside an ingest run.
    """
    kind = kind.strip().casefold()
    if kind not in EMBEDDER_KINDS:
        raise ValueError(f"unknown embedder kind {kind!r}")

    if kind == "hashing":

        def _hashing(_texts: Sequence[str]) -> Embedder:
            return HashingEmbedder(dim=dim)

        return _hashing

    if kind == "sentence_transformers":

        def _st(_texts: Sequence[str]) -> Embedder:
            return SentenceTransformerEmbedder(model_id)

        return _st

    if kind == "tfidf":

        def _tfidf(texts: Sequence[str]) -> Embedder:
            return TfidfEmbedder().fit(texts)

        return _tfidf

    # auto
    def _auto(texts: Sequence[str]) -> Embedder:
        try:
            import sentence_transformers  # noqa: F401

            return SentenceTransformerEmbedder(model_id)
        except ImportError:
            _log.info(
                "rag_embedder_fallback",
                requested="sentence_transformers",
                using="tfidf",
                reason="sentence-transformers not installed",
            )
            return TfidfEmbedder().fit(texts)

    return _auto


__all__ = [
    "EMBEDDER_KINDS",
    "EMBEDDER_MARKER_FILE",
    "RECOMMENDED_MIN_SIMILARITY",
    "TFIDF_EMBEDDER_FILE",
    "HashingEmbedder",
    "SentenceTransformerEmbedder",
    "TfidfEmbedder",
    "build_embedder_factory",
    "embedder_marker",
]
