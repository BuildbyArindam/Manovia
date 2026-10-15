"""Day 35 — fine-tuned retrieval: eval set, contrastive training, hybrid, reranking.

This subpackage is the *learning* half of retrieval. The serving half lives in
the backend (``app/services/rag/``: BM25, reciprocal-rank fusion, the
cross-encoder seam and ``HybridKnowledgeRetriever``), because that is the code
a chat turn actually runs. What lives here:

* :mod:`manovia_ml.retrieval.corpus` — the knowledge corpus and its passages,
  read without importing the backend (and byte-identical to it when the backend
  *is* importable; ``backend/tests/unit/test_rag_corpus_parity.py`` proves it).
* :mod:`manovia_ml.retrieval.data` — the retrieval eval set: schema,
  validation against the corpus, and the deterministic four-way split
  (train/dev/calibration/test) whose hashes the reports quote.
* :mod:`manovia_ml.retrieval.augment` — the label-preserving paraphraser that
  expands the hand-written rows into the ``synthetic`` ones (offline, seeded,
  screened; a DVC stage, never a committed byte).
* :mod:`manovia_ml.retrieval.pairs` — (query, positive passage) pairs plus
  hard-negative mining from near-miss passages.
* :mod:`manovia_ml.retrieval.embeddings` / :mod:`manovia_ml.retrieval.contrastive`
  / :mod:`manovia_ml.retrieval.train_embedding` — contrastive fine-tuning with
  MultipleNegativesRankingLoss.
* :mod:`manovia_ml.retrieval.reranker` — cross-encoder feature extraction and
  the (distilled) reranker fit.
* :mod:`manovia_ml.retrieval.tuning` — fusion weight and abstain thresholds,
  fitted on dev/calibration only.
* :mod:`manovia_ml.retrieval.harness` — scoring a retrieval setting against a
  split: Recall@k, MRR, per-language, latency percentiles, failure cases.
* :mod:`manovia_ml.evaluation.retrieval` — the CLI that runs all of it, logs to
  MLflow and writes ``ml/reports/day35-retrieval.md``.

Decisions: ``docs/adr/0035-fine-tuned-retrieval-hybrid-rerank.md``.
"""

from __future__ import annotations

__all__ = [
    "RETRIEVAL_EVAL_VERSION",
]

#: Bumped when the eval-set schema changes in a way that invalidates a split.
RETRIEVAL_EVAL_VERSION = 1
