"""MultipleNegativesRankingLoss, and the contrastive fine-tune around it.

This is the loss the brief names, implemented the way ``sentence-transformers``
defines it rather than the way a blog post paraphrases it:

    scores[i, j] = scale * cos(query_i, candidate_j)
    candidates   = [positive_1 … positive_B, negative_1 … negative_M]
    loss         = cross_entropy(scores, labels=arange(B))

so every other query's positive in the batch is a negative for this query (the
"multiple negatives" part), and the mined hard negatives are appended as extra
columns. ``scale`` is the inverse temperature (20.0 upstream).

Why numpy and not torch here: this sandbox has no GPU and, more decisively, no
Hugging Face access, so there is no checkpoint to fine-tune — installing torch
would buy nothing that runs. The encoder that *can* be trained offline is
: class:`~manovia_ml.retrieval.embeddings.ProjectionEncoder` (a linear map out of
the lexical feature space), and for a linear map the whole loss has a closed
gradient: softmax → embeddings → L2 normalisation → ``W``. It is ~60 lines, it is
exact, and it trains in seconds on a laptop CPU. The transformer path uses the
real ``sentence_transformers.losses.MultipleNegativesRankingLoss`` in
``ml/notebooks/colab_retrieval_finetune.ipynb``: same pairs, same loss, same
dev-only model selection, different encoder.

Determinism is the same three-part story as everywhere else in ``ml/``
(``manovia_ml.seeds``): a seeded generator for the batch order, a seeded
initialisation for ``W``, and Adam's state kept dense so no dictionary iteration
order can leak in.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from manovia_ml.retrieval.corpus import Passage
from manovia_ml.retrieval.embeddings import LexicalFeatures, ProjectionEncoder, as_matrix
from manovia_ml.retrieval.pairs import TrainingPair

__all__ = [
    "AdamState",
    "EpochRecord",
    "TrainResult",
    "contrastive_step",
    "multiple_negatives_ranking",
    "train_projection",
]


def _softmax(scores: np.ndarray) -> np.ndarray:
    shifted = scores - scores.max(axis=1, keepdims=True)
    exponentials = np.exp(shifted)
    return as_matrix(exponentials / exponentials.sum(axis=1, keepdims=True))


def multiple_negatives_ranking(
    query_embeddings: np.ndarray,
    positive_embeddings: np.ndarray,
    negative_embeddings: np.ndarray | None = None,
    *,
    scale: float = 20.0,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray | None]:
    """The MNRL loss and its gradients with respect to the *embeddings*.

    All three inputs are L2-normalised rows. Returns ``(loss, d_query,
    d_positive, d_negative)``; the last is ``None`` when no hard negatives were
    passed, which reduces the loss to plain in-batch negatives.
    """

    batch = query_embeddings.shape[0]
    if batch == 0:
        return 0.0, query_embeddings, positive_embeddings, negative_embeddings
    if positive_embeddings.shape[0] != batch:
        raise ValueError("one positive per query")

    candidates = (
        np.vstack([positive_embeddings, negative_embeddings])
        if negative_embeddings is not None and negative_embeddings.shape[0]
        else positive_embeddings
    )
    scores = scale * (query_embeddings @ candidates.T)
    probabilities = _softmax(scores)
    diagonal = np.clip(probabilities[np.arange(batch), np.arange(batch)], 1e-12, 1.0)
    loss = float(-np.log(diagonal).mean())

    onehot = np.zeros_like(probabilities)
    onehot[np.arange(batch), np.arange(batch)] = 1.0
    d_scores = (probabilities - onehot) / batch * scale
    d_query = d_scores @ candidates
    d_candidates = d_scores.T @ query_embeddings
    if negative_embeddings is not None and negative_embeddings.shape[0]:
        return (
            loss,
            d_query,
            d_candidates[:batch],
            d_candidates[batch:],
        )
    return loss, d_query, d_candidates, None


def _denormalise_gradient(d_embedding: np.ndarray, projected: np.ndarray) -> np.ndarray:
    """Push a gradient back through ``e = u / ||u||``.

    ``de/du = (I - e eᵗ) / ||u||``, so ``du = (de - e (de·e)) / ||u||``. Rows
    whose projection collapsed to zero get a zero gradient rather than a NaN: a
    query with no lexical features at all cannot teach the projection anything,
    and dividing by zero to find that out is how a run starts producing NaNs.
    """

    norms = np.linalg.norm(projected, axis=1, keepdims=True)
    safe = np.where(norms == 0.0, 1.0, norms)
    embeddings = projected / safe
    along = (d_embedding * embeddings).sum(axis=1, keepdims=True)
    gradient = (d_embedding - embeddings * along) / safe
    gradient[norms[:, 0] == 0.0] = 0.0
    return as_matrix(gradient)


@dataclass
class AdamState:
    """Dense Adam moments for the projection matrix."""

    first: np.ndarray
    second: np.ndarray
    step: int = 0

    @classmethod
    def zeros_like(cls, weights: np.ndarray) -> AdamState:
        return cls(first=np.zeros_like(weights), second=np.zeros_like(weights))

    def update(
        self,
        weights: np.ndarray,
        gradient: np.ndarray,
        *,
        lr: float,
        beta1: float = 0.9,
        beta2: float = 0.999,
        eps: float = 1e-8,
    ) -> None:
        self.step += 1
        self.first = beta1 * self.first + (1 - beta1) * gradient
        self.second = beta2 * self.second + (1 - beta2) * (gradient * gradient)
        first_hat = self.first / (1 - beta1**self.step)
        second_hat = self.second / (1 - beta2**self.step)
        weights -= lr * first_hat / (np.sqrt(second_hat) + eps)


@dataclass(frozen=True)
class EpochRecord:
    epoch: int
    train_loss: float
    steps: int
    #: ``None`` when no selection callback was supplied (a smoke run).
    select_score: float | None = None


@dataclass(frozen=True)
class TrainResult:
    """What a contrastive run produced, and enough of *how* to judge it."""

    weights: np.ndarray
    history: tuple[EpochRecord, ...] = field(default_factory=tuple)
    best_epoch: int | None = None
    pairs: int = 0
    negatives: int = 0
    feature_dim: int = 0
    dim: int = 0
    #: The encoder built from these weights (the artefact the harness scores).
    encoder: ProjectionEncoder | None = None

    def best_weights(self) -> np.ndarray:
        """The weights of the best-scoring epoch, i.e. what ``encoder`` holds."""

        assert self.encoder is not None  # invariant: `encoder` is set in __post_init__
        return self.encoder.weights


def contrastive_step(
    weights: np.ndarray,
    query_features: Any,
    positive_features: Any,
    negative_features: Any | None,
    *,
    scale: float,
) -> tuple[float, np.ndarray]:
    """One forward/backward pass; returns the loss and ``dW``.

    The feature matrices are sparse (``scipy.sparse``), the weights are dense,
    and the product stays dense — at this feature size that is both faster and
    simpler than a sparse gradient accumulator.
    """

    query_projected = np.asarray(query_features @ weights.T, dtype=np.float64)
    positive_projected = np.asarray(positive_features @ weights.T, dtype=np.float64)
    query_norms = _safe_norm(query_projected)
    positive_norms = _safe_norm(positive_projected)

    negative_projected = None
    negative_norms = None
    negative_embeddings = None
    if negative_features is not None and negative_features.shape[0]:
        negative_projected = np.asarray(negative_features @ weights.T, dtype=np.float64)
        negative_norms = _safe_norm(negative_projected)
        negative_embeddings = negative_projected / np.where(
            negative_norms == 0, 1.0, negative_norms
        )

    loss, d_query, d_positive, d_negative = multiple_negatives_ranking(
        query_projected / np.where(query_norms == 0, 1.0, query_norms),
        positive_projected / np.where(positive_norms == 0, 1.0, positive_norms),
        negative_embeddings,
        scale=scale,
    )

    gradient = _denormalise_gradient(d_query, query_projected).T @ query_features
    gradient = (
        gradient + _denormalise_gradient(d_positive, positive_projected).T @ positive_features
    )
    if d_negative is not None and negative_projected is not None:
        gradient = (
            gradient + _denormalise_gradient(d_negative, negative_projected).T @ negative_features
        )
    return loss, as_matrix(gradient)


def _safe_norm(matrix: np.ndarray) -> np.ndarray:
    return as_matrix(np.linalg.norm(matrix, axis=1, keepdims=True))


def train_projection(
    pairs: Sequence[TrainingPair],
    passages: Sequence[Passage],
    *,
    features: LexicalFeatures,
    dim: int,
    epochs: int = 3,
    batch_size: int = 32,
    lr: float = 0.05,
    scale: float = 20.0,
    seed: int = 20261014,
    select_score: Callable[[np.ndarray], float] | None = None,
) -> TrainResult:
    """Contrastively fine-tune a projection out of the lexical feature space.

    ``select_score`` is called with the candidate weights after every epoch and
    must be computed on a **tuning** split (dev), never on the split a number is
    quoted from. The best-scoring epoch's weights are the result; when no
    callback is given the last epoch wins, which is what a smoke run wants.
    """

    if not pairs:
        raise ValueError("no training pairs")
    passage_by_id = {passage.chunk_id: passage for passage in passages}
    missing = sorted({pair.positive for pair in pairs} - set(passage_by_id))
    if missing:
        raise ValueError(f"pairs reference unknown passages: {missing[:3]}")

    query_features = features.transform([pair.query for pair in pairs])
    positive_features = features.transform(
        [passage_by_id[pair.positive].embed_text for pair in pairs]
    )
    negative_rows: list[np.ndarray] = []
    negative_texts: list[str] = []
    for pair in pairs:
        start = len(negative_texts)
        for chunk_id in pair.hard_negatives:
            if chunk_id not in passage_by_id:
                raise ValueError(f"hard negative {chunk_id!r} is not a known passage")
            negative_texts.append(passage_by_id[chunk_id].embed_text)
        negative_rows.append(np.arange(start, len(negative_texts), dtype=np.int64))
    negative_features = features.transform(negative_texts) if negative_texts else None

    generator = np.random.default_rng(seed)
    weights = generator.normal(scale=1.0 / np.sqrt(features.dim), size=(dim, features.dim))
    state = AdamState.zeros_like(weights)

    history: list[EpochRecord] = []
    best_score: float | None = None
    best_weights = weights.copy()
    best_epoch: int | None = None
    pair_count = len(pairs)

    for epoch in range(1, epochs + 1):
        order = generator.permutation(pair_count)
        total_loss = 0.0
        steps = 0
        for start in range(0, pair_count, batch_size):
            batch = order[start : start + batch_size]
            if batch.size < 2:
                # MNRL's in-batch negatives need at least two queries to have
                # anything to contrast; a trailing singleton is dropped rather
                # than turned into a degenerate one-class step.
                continue
            negative_batch = (
                np.concatenate([negative_rows[index] for index in batch])
                if negative_features is not None
                else None
            )
            loss, gradient = contrastive_step(
                weights,
                query_features[batch],
                positive_features[batch],
                negative_features[negative_batch]
                if negative_features is not None and negative_batch is not None
                else None,
                scale=scale,
            )
            state.update(weights, gradient, lr=lr)
            total_loss += loss
            steps += 1
        average = total_loss / steps if steps else 0.0
        score = select_score(weights) if select_score is not None else None
        history.append(
            EpochRecord(epoch=epoch, train_loss=average, steps=steps, select_score=score)
        )
        if score is not None and (best_score is None or score > best_score):
            best_score = score
            best_weights = weights.copy()
            best_epoch = epoch

    encoder = ProjectionEncoder(features, best_weights)
    return TrainResult(
        weights=best_weights,
        history=tuple(history),
        best_epoch=best_epoch,
        pairs=pair_count,
        negatives=len(negative_texts),
        feature_dim=features.dim,
        dim=dim,
        encoder=encoder,
    )
