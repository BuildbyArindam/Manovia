"""Dense encoders for retrieval, behind one seam, offline by default.

Four encoders, one :class:`Encoder` protocol, and every one of them also
satisfies the backend's ``app.services.rag.base.Embedder`` protocol
(``name``/``dim``/``resolve_dim()``/``embed()``) so it can be handed straight to
``run_ingest`` and scored through the production retrieval path:

* :class:`HashingEncoder` — untrained, deterministic feature hashing. The
  weakest baseline and the one a unit test can build in microseconds.
* :class:`TfidfEncoder` — corpus-fitted TF-IDF with the feature cap. Mirrors
  ``app.services.rag.embedders.TfidfEmbedder`` (same stopwords, same n-grams,
  same 0.20 cap) so "the off-the-shelf offline embedder" means the same thing
  here and in production.
* :class:`ProjectionEncoder` — **the trainable one**: lexical features into a
  low-dimensional embedding through a matrix learned by contrastive fine-tuning
  (:mod:`manovia_ml.retrieval.contrastive`). This is the encoder the sandbox can
  actually train, because it needs no checkpoint download.
* :class:`SentenceTransformerEncoder` — the real multilingual checkpoint,
  off-the-shelf or a fine-tuned local directory. Lazy import, lazy load: the
  sandbox has no Hugging Face access, so constructing it is free and the first
  ``encode`` is where it fails loudly with the instructions to run Colab.

The honest statement about the two trainable paths: they are the *same* training
code (same loss, same pairs, same hard negatives, same dev-only model
selection), and only the encoder underneath differs. The projection is what this
repository can measure today; the checkpoint is what ``ml/notebooks/
colab_retrieval_finetune.ipynb`` trains on a GPU, and it is not a different
experiment.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from manovia_ml.config import RetrievalEncoderConfig, RetrievalFeaturesConfig
from manovia_ml.hashing import sha256_bytes, sha256_text

__all__ = [
    "ARTEFACT_METADATA_FILE",
    "FEATURES_FILE",
    "PROJECTION_FILE",
    "Encoder",
    "EncoderUnavailable",
    "HashingEncoder",
    "LexicalFeatures",
    "ProjectionEncoder",
    "SentenceTransformerEncoder",
    "TfidfEncoder",
    "as_matrix",
    "build_encoder",
    "load_projection_encoder",
]

#: Word characters *plus* the combining marks Indic scripts need, as one
#: character class. See ``retrieval/augment.py`` for why an alternation is not
#: the same thing, and ``backend/app/services/rag/embedders.py`` for the
#: matching production fix (ADR 0035).
_MARKS = "\u0300-\u036f\u0900-\u097f\u0980-\u09ff\u0a00-\u0a7f\u0b00-\u0b7f"
TOKEN_PATTERN = rf"(?:[^\W\d_]|[{_MARKS}])+"
_TOKEN_RE = re.compile(TOKEN_PATTERN, re.UNICODE)
#: ``TfidfVectorizer`` rejects capture groups in ``token_pattern``; this is the
#: same expression with digits allowed (a query like "5 4 3 2 1" is a real one).
TFIDF_TOKEN_PATTERN = rf"(?:[^\W_]|[{_MARKS}])+"

#: The English function words plus the corpus-noise words the production
#: embedder drops. Kept in step with ``app.services.rag.embedders._STOPWORDS``:
#: a retriever whose two halves disagree about stopwords is two retrievers.
#: Written as text and split, because a 100-item list literal of stopwords is
#: unreadable and unreadable is how a list like this stops being maintained.
_STOPWORD_TEXT = (
    "a an and are as at be been being but by for from has have having i in is it its of on",
    "or that the this to was were will with you your me my we our they them he she his her if",
    "then than so not no do does did doing can could would should shall may might must about",
    "into over under again more most some such only own same too very just dont cant wont im",
    "ive youll youre what when where which who whom why how",
    # The corpus's own filler: self-help prose has a vocabulary of reassurance
    # that carries no retrieval signal ("keep", "really", "a lot", "things").
    "keep keeps keeping felt feels get gets really even still always much many lot day days",
    "week weeks fix fixes fixed fixing stop thing things way ways",
)
STOPWORDS: frozenset[str] = frozenset(" ".join(_STOPWORD_TEXT).split())

#: Metadata file inside a trained encoder artefact directory.
ARTEFACT_METADATA_FILE = "encoder.json"
FEATURES_FILE = "features.joblib"
PROJECTION_FILE = "projection.npz"


class EncoderUnavailable(RuntimeError):
    """An encoder cannot be built or loaded here (extra missing, model absent)."""


@runtime_checkable
class Encoder(Protocol):
    """Text in, L2-normalised vectors out, in order."""

    name: str
    dim: int

    def resolve_dim(self) -> int: ...

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """The backend ``Embedder`` shape (a list of lists, not an array)."""
        ...


def as_matrix(value: Any) -> np.ndarray:
    """numpy's typed view of a value that came back from an untyped boundary.

    ``scipy.sparse`` ships no stubs and an optional checkpoint's ``encode`` is
    imported lazily, so both hand back ``Any``. Converting once, here, keeps
    ``Any`` from travelling through the training and evaluation code — where a
    shape bug would otherwise be invisible to the type checker and only show up
    as a wrong number in a report.
    """

    result: np.ndarray = np.asarray(value, dtype=np.float64)
    return result


def _rows(matrix: np.ndarray) -> list[list[float]]:
    """A dense matrix as plain Python floats.

    ``ndarray.tolist()`` is typed ``Any`` (it recurses by shape), and every
    ``embed`` here returns ``list[list[float]]`` for the backend's ``Embedder``
    protocol — so the conversion is spelled out once, in one place, rather than
    cast at four call sites.
    """

    return [[float(value) for value in row] for row in np.asarray(matrix, dtype=np.float64)]


def _l2_normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return as_matrix(matrix / norms)


class HashingEncoder:
    """Deterministic feature hashing: untrained, fit-free, deliberately weak."""

    def __init__(self, dim: int = 384) -> None:
        if dim < 16:
            raise ValueError("hashing encoder needs at least 16 dimensions")
        self.dim = dim
        self.name = f"hashing-v1:{dim}"

    def resolve_dim(self) -> int:
        return self.dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out: np.ndarray = np.zeros((len(texts), self.dim), dtype=np.float64)
        for row, text in enumerate(texts):
            tokens = [t for t in _TOKEN_RE.findall(text.casefold()) if t not in STOPWORDS]
            features = [*tokens, *(f"{a} {b}" for a, b in itertools.pairwise(tokens))]
            for feature in features:
                digest = hashlib.blake2b(
                    f"manovia-rag-v1:{feature}".encode(), digest_size=8
                ).digest()
                value = int.from_bytes(digest, "little")
                out[row, value % self.dim] += 1.0 if (value >> 63) & 1 else -1.0
        return _l2_normalise(out)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return _rows(self.encode(texts))


@dataclass(frozen=True)
class LexicalFeatures:
    """A fitted sparse feature space: word n-grams stacked with char n-grams.

    Fitted once on the passages (and the training queries, so a query-only token
    is not silently out-of-vocabulary), persisted with the artefact, and shared
    by the TF-IDF encoder and the projection encoder — two encoders that
    disagree about their feature space cannot be compared.
    """

    word_vectorizer: Any
    char_vectorizer: Any
    config: RetrievalFeaturesConfig
    fit_texts_sha256: str

    @property
    def dim(self) -> int:
        return int(len(self.word_vectorizer.vocabulary_) + len(self.char_vectorizer.vocabulary_))

    @property
    def name(self) -> str:
        return f"lexical-v1:{sha256_text(self.fit_texts_sha256 + str(self.dim))[:12]}"

    @classmethod
    def fit(cls, texts: Sequence[str], config: RetrievalFeaturesConfig) -> LexicalFeatures:
        from sklearn.feature_extraction.text import TfidfVectorizer

        corpus = list(texts)
        if not corpus:
            raise ValueError("cannot fit lexical features on zero texts")
        word = TfidfVectorizer(
            ngram_range=tuple(config.word_ngram_range),
            sublinear_tf=config.sublinear_tf,
            token_pattern=TFIDF_TOKEN_PATTERN,
            stop_words=sorted(STOPWORDS),
            min_df=config.min_df,
            max_features=config.word_max_features,
            norm="l2",
        )
        char = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=tuple(config.char_ngram_range),
            sublinear_tf=config.sublinear_tf,
            min_df=config.min_df,
            max_features=config.char_max_features,
            norm="l2",
        )
        word.fit(corpus)
        char.fit(corpus)
        return cls(
            word_vectorizer=word,
            char_vectorizer=char,
            config=config,
            fit_texts_sha256=sha256_text("\x1e".join(corpus)),
        )

    def transform(self, texts: Sequence[str]) -> Any:
        """The stacked, L2-normalised sparse matrix for ``texts``."""

        from scipy.sparse import hstack

        word_part = self.word_vectorizer.transform(list(texts))
        char_part = self.char_vectorizer.transform(list(texts))
        stacked = hstack([word_part, char_part]).tocsr()
        return _normalise_sparse(stacked)

    def save(self, directory: Path) -> None:
        import joblib

        directory.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "word": self.word_vectorizer,
                "char": self.char_vectorizer,
                "config": self.config.model_dump(mode="json"),
                "fit_texts_sha256": self.fit_texts_sha256,
            },
            directory / FEATURES_FILE,
        )

    @classmethod
    def load(cls, directory: Path) -> LexicalFeatures:
        import joblib

        path = directory / FEATURES_FILE
        if not path.is_file():
            raise EncoderUnavailable(f"no fitted features at {path}")
        payload = joblib.load(path)
        return cls(
            word_vectorizer=payload["word"],
            char_vectorizer=payload["char"],
            config=RetrievalFeaturesConfig.model_validate(payload["config"]),
            fit_texts_sha256=str(payload["fit_texts_sha256"]),
        )


def _normalise_sparse(matrix: Any) -> Any:
    from sklearn.preprocessing import normalize

    return normalize(matrix, norm="l2", copy=False)


class TfidfEncoder:
    """Corpus-fitted TF-IDF with the production feature cap.

    The cap is the calibration that made the Day-13 offline retriever usable
    (ADR 0013): after L2 normalisation each feature is clipped at
    :data:`FEATURE_CAP` and the vector re-normalised, so one shared *rare* word
    cannot manufacture relevance the way an uncapped cosine lets it.
    """

    FEATURE_CAP: float = 0.20

    def __init__(self, features: LexicalFeatures) -> None:
        self._features = features
        self.dim = features.dim
        self.name = f"tfidf-v1:{features.name.split(':', 1)[1]}"

    @classmethod
    def fit(
        cls, texts: Sequence[str], config: RetrievalFeaturesConfig | None = None
    ) -> TfidfEncoder:
        return cls(LexicalFeatures.fit(texts, config or RetrievalFeaturesConfig()))

    @property
    def features(self) -> LexicalFeatures:
        return self._features

    def resolve_dim(self) -> int:
        return self.dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim))
        matrix = self._features.transform(texts).toarray()
        capped = np.minimum(matrix, self.FEATURE_CAP)
        return _l2_normalise(capped)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return _rows(self.encode(texts))

    def save(self, directory: Path) -> None:
        self._features.save(directory)


class ProjectionEncoder:
    """Lexical features → a learned low-dimensional embedding.

    ``encode`` is ``normalise(W @ x)``. ``W`` is learned by contrastive
    fine-tuning (MultipleNegativesRankingLoss, in-batch plus mined hard
    negatives) in :mod:`manovia_ml.retrieval.contrastive`; nothing else about
    the encoder changes, which is the point — the comparison against
    :class:`TfidfEncoder` isolates *what training did* from *what features are*.

    The projection is dense and small (``dim`` by ``features.dim`` floats, a few
    MB) and is saved as an ``.npz`` inside the artefact directory, which
    ``ml/.gitignore`` keeps out of Git like every other weight.
    """

    def __init__(
        self, features: LexicalFeatures, weights: np.ndarray, *, name_suffix: str = ""
    ) -> None:
        if weights.ndim != 2 or weights.shape[1] != features.dim:
            raise ValueError(f"projection has shape {weights.shape}, want (dim, {features.dim})")
        self._features = features
        self._weights = np.ascontiguousarray(weights, dtype=np.float64)
        self.dim = int(weights.shape[0])
        self.name = f"projection-v1:{features.name.split(':', 1)[1]}{name_suffix}"

    @property
    def features(self) -> LexicalFeatures:
        return self._features

    @property
    def weights(self) -> np.ndarray:
        return as_matrix(self._weights)

    def resolve_dim(self) -> int:
        return self.dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim))
        # (n, D) sparse @ (D, dim) dense -> (n, dim)
        projected = self._features.transform(texts) @ self._weights.T
        return _l2_normalise(as_matrix(projected))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return _rows(self.encode(texts))

    def save(self, directory: Path, metadata: dict[str, Any] | None = None) -> None:
        """Write weights + features + metadata. Called by ingest and by the CLI."""

        directory.mkdir(parents=True, exist_ok=True)
        self._features.save(directory)
        np.savez_compressed(directory / PROJECTION_FILE, weights=self._weights)
        payload = {
            "kind": "projection",
            "name": self.name,
            "dim": self.dim,
            "feature_dim": self._features.dim,
            "features": self._features.config.model_dump(mode="json"),
            "weights_sha256": sha256_bytes(self._weights.tobytes()),
            **(metadata or {}),
        }
        (directory / ARTEFACT_METADATA_FILE).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, directory: Path) -> ProjectionEncoder:
        path = Path(directory)
        projection_path = path / PROJECTION_FILE
        if not projection_path.is_file():
            raise EncoderUnavailable(f"no projection weights at {projection_path}")
        with np.load(projection_path) as data:
            weights = np.asarray(data["weights"], dtype=np.float64)
        features = LexicalFeatures.load(path)
        metadata = _read_metadata(path)
        return cls(features, weights, name_suffix=metadata.get("name_suffix", ""))


def _read_metadata(directory: Path) -> dict[str, Any]:
    path = directory / ARTEFACT_METADATA_FILE
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EncoderUnavailable(f"{path}: unreadable metadata: {exc}") from exc
    return loaded if isinstance(loaded, dict) else {}


def load_projection_encoder(directory: Path | str) -> ProjectionEncoder:
    """Load a trained projection artefact, or raise :class:`EncoderUnavailable`."""

    return ProjectionEncoder.load(Path(directory))


class SentenceTransformerEncoder:
    """A real multilingual checkpoint: off-the-shelf or fine-tuned.

    ``model_id`` may be a Hugging Face id (the off-the-shelf comparison) or a
    local directory produced by the contrastive fine-tune (the fine-tuned one).
    The import and the load are both lazy and both guarded, so configuring this
    encoder in a sandbox with no hub access costs nothing until the first
    encode, and the error it raises names the notebook to run instead.
    """

    def __init__(self, model_id: str, *, device: str = "cpu", batch_size: int = 16) -> None:
        model_id = model_id.strip()
        if not model_id:
            raise ValueError("model_id must not be empty")
        self.model_id = model_id
        self.device = device
        self.batch_size = max(1, batch_size)
        self.dim = 0
        self.name = f"sentence-transformers:{model_id}"
        self._model: Any | None = None
        self._lock = threading.Lock()

    def resolve_dim(self) -> int:
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
                raise EncoderUnavailable(
                    "sentence-transformers is not installed; install the backend's 'rag' "
                    "extra (pip install -e '../backend[rag]'), or evaluate the offline "
                    "encoders (encoder.kind: projection|tfidf|hashing)"
                ) from exc
            try:
                self._model = SentenceTransformer(self.model_id, device=self.device)
            except Exception as exc:  # a hub download failure is the common case here
                raise EncoderUnavailable(
                    f"could not load {self.model_id!r}: {type(exc).__name__}. A checkpoint "
                    "needs Hugging Face access; train or download it with "
                    "ml/notebooks/colab_retrieval_finetune.ipynb and point encoder.model_id "
                    "at the local directory."
                ) from exc
            self.dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        model = self._ensure_model()
        if not texts:
            return np.zeros((0, self.dim))
        vectors = model.encode(
            list(texts),
            batch_size=self.batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        self.dim = int(vectors.shape[1])
        return as_matrix(vectors)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return _rows(self.encode(texts))


def build_encoder(
    config: RetrievalEncoderConfig,
    *,
    fit_texts: Sequence[str] | None = None,
) -> Encoder:
    """The encoder a config names, fitting or loading as the kind requires.

    ``fit_texts`` is mandatory for ``tfidf`` and for ``projection`` with no
    ``artifact_dir`` (both are corpus-fitted); the other kinds ignore it.
    ``artifact_dir`` is read only when ``kind`` is ``projection``: a config that
    says TF-IDF gets TF-IDF, whatever else it points at.
    """

    kind = config.kind
    if kind == "hashing":
        return HashingEncoder(dim=config.dim)
    if kind == "sentence_transformers":
        return SentenceTransformerEncoder(config.model_id)
    # `artifact_dir` names a *projection* artefact, so it is honoured only for
    # that kind. Checking it first used to make `kind: tfidf` with a stray
    # artifact_dir silently load trained weights instead of fitting TF-IDF —
    # which is how the harness ingested a projection encoder into a directory
    # called `store-tfidf` and then could not reload it.
    if kind == "projection" and config.artifact_dir.strip():
        return load_projection_encoder(Path(config.artifact_dir))
    if fit_texts is None:
        raise ValueError(f"encoder.kind {kind!r} needs fit_texts (the passage embed texts)")
    features = LexicalFeatures.fit(fit_texts, config.features)
    if kind == "tfidf":
        return TfidfEncoder(features)
    # projection with no artefact: an untrained random projection, which is the
    # honest starting point of a contrastive run (and a useful ablation — it
    # separates "a smaller space helps" from "training helps").
    generator = np.random.default_rng(20261014)
    weights = generator.normal(scale=1.0 / np.sqrt(features.dim), size=(config.dim, features.dim))
    return ProjectionEncoder(features, weights, name_suffix=":untrained")
