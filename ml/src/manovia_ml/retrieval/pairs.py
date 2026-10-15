"""(query, positive passage) pairs, and the hard negatives mined around them.

Contrastive fine-tuning lives or dies on its negatives. Random negatives from a
40-document corpus are trivial: after a few steps the model separates "sleep"
from "exams" and stops learning anything. The pairs that teach it are the
*near misses* — the passage that shares a tag and half a sentence with the right
one but answers a different question ("jumping to the worst case" next to
"sorting worries: can I act on this or not?"), and the passage that a lexical
baseline ranks first for a vague query.

Mining is therefore two signals, merged:

1. **Lexical near-miss.** Score every passage with the *untuned* encoder (the
   same TF-IDF space the Day-13 baseline lives in), take the top
   ``mine_candidates``, drop every passage whose document is labelled relevant,
   and keep the best ``hard_negatives``.
2. **Tag near-miss.** Among those, prefer passages whose document shares a tag
   with a relevant document. Sharing ``thinking-traps`` with the right answer
   while being the wrong answer is exactly the confusion a fine-tune should
   remove, and a purely lexical ranking under-samples it.

Both are deterministic (scores, then chunk id), so the pair file is a pure
function of (corpus, queries, encoder, seed) and can be a DVC-tracked artefact.

Nothing here touches the ``test`` split: pairs are built from ``train`` (and, for
the reranker's dev-selected threshold, ``dev``) only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from manovia_ml.retrieval.corpus import Passage
from manovia_ml.retrieval.data import RetrievalQuery
from manovia_ml.retrieval.embeddings import Encoder

__all__ = ["MiningReport", "TrainingPair", "build_pairs", "mine_hard_negatives"]


@dataclass(frozen=True)
class TrainingPair:
    """One training example: a query, the passage that answers it, and near misses."""

    query_id: str
    query: str
    lang: str
    #: Chunk id of the positive passage.
    positive: str
    positive_doc: str
    #: Chunk ids, hardest first.
    hard_negatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class MiningReport:
    """What mining did, so a report can say why a run learned little."""

    queries: int
    pairs: int
    positives_per_query: dict[int, int] = field(default_factory=dict)
    negatives_per_pair: dict[int, int] = field(default_factory=dict)
    #: Queries whose every top candidate was a relevant document, so no near miss
    #: was available. Not an error — a small corpus does that — but a number a
    #: reader needs to judge how hard the negatives were.
    zero_negative_pairs: int = 0
    tag_preferring_choices: int = 0


def mine_hard_negatives(
    *,
    passages: Sequence[Passage],
    scores: Sequence[float],
    relevant_docs: Sequence[str],
    candidates: int,
    negatives: int,
) -> tuple[list[str], int]:
    """The ``negatives`` hardest non-relevant passages for one query.

    ``scores`` is the query's similarity to every passage, in passage order.
    Returns the chunk ids and how many of them were chosen for sharing a tag
    with a relevant document.
    """

    if negatives <= 0:
        return [], 0
    relevant = set(relevant_docs)
    relevant_tags: set[str] = set()
    for passage in passages:
        if passage.doc_id in relevant:
            relevant_tags.update(passage.tags)

    order = sorted(
        range(len(passages)),
        key=lambda index: (-float(scores[index]), passages[index].chunk_id),
    )
    pool = [
        index
        for index in order[: max(candidates, negatives)]
        if passages[index].doc_id not in relevant
    ]
    if not pool:
        # Nothing outside the relevant documents made the candidate window; widen
        # to the whole corpus rather than return no negatives at all.
        pool = [index for index in order if passages[index].doc_id not in relevant]

    tagged = [index for index in pool if relevant_tags & set(passages[index].tags)]
    untagged = [index for index in pool if not (relevant_tags & set(passages[index].tags))]
    chosen = (tagged + untagged)[:negatives]
    tag_choices = sum(1 for index in chosen if index in set(tagged))
    return [passages[index].chunk_id for index in chosen], tag_choices


def build_pairs(
    queries: Sequence[RetrievalQuery],
    passages: Sequence[Passage],
    *,
    encoder: Encoder,
    negatives: int = 4,
    candidates: int = 20,
    max_positives_per_query: int = 2,
) -> tuple[list[TrainingPair], MiningReport]:
    """Build the training pairs for a split, mining hard negatives as it goes.

    A query with several relevant documents yields one pair per document (up to
    ``max_positives_per_query``), each with the *first* matching passage of that
    document as the positive — the passage the retriever would actually serve.
    """

    by_doc: dict[str, Passage] = {}
    for passage in passages:
        # Chunk 00 is the passage that carries the title-anchored opening, and
        # for a 200-word document it is usually the whole document.
        by_doc.setdefault(passage.doc_id, passage)

    answerable = [query for query in queries if query.has_answer]
    if not answerable:
        raise ValueError("no answerable queries in this split; nothing to contrast")

    query_texts = [query.query for query in answerable]
    passage_texts = [passage.embed_text for passage in passages]
    query_vectors = encoder.encode(query_texts)
    passage_matrix = encoder.encode(passage_texts)
    if query_vectors.shape[1] != passage_matrix.shape[1]:
        raise ValueError("query and passage embeddings disagree on dimension")
    similarities = np.asarray(query_vectors @ passage_matrix.T, dtype=np.float64)

    pairs: list[TrainingPair] = []
    positives_hist: dict[int, int] = {}
    negatives_hist: dict[int, int] = {}
    zero_negative = 0
    tag_choices = 0
    for row, query in enumerate(answerable):
        scores = similarities[row]
        positives = [doc for doc in query.relevant_docs if doc in by_doc][:max_positives_per_query]
        positives_hist[len(positives)] = positives_hist.get(len(positives), 0) + 1
        mined, tagged = mine_hard_negatives(
            passages=passages,
            scores=list(scores),
            relevant_docs=query.relevant_docs,
            candidates=candidates,
            negatives=negatives,
        )
        tag_choices += tagged
        negatives_hist[len(mined)] = negatives_hist.get(len(mined), 0) + len(positives)
        if not mined:
            zero_negative += len(positives)
        for doc_id in positives:
            passage = by_doc[doc_id]
            pairs.append(
                TrainingPair(
                    query_id=query.id,
                    query=query.query,
                    lang=query.lang,
                    positive=passage.chunk_id,
                    positive_doc=doc_id,
                    hard_negatives=tuple(mined),
                )
            )

    report = MiningReport(
        queries=len(answerable),
        pairs=len(pairs),
        positives_per_query=dict(sorted(positives_hist.items())),
        negatives_per_pair=dict(sorted(negatives_hist.items())),
        zero_negative_pairs=zero_negative,
        tag_preferring_choices=tag_choices,
    )
    return pairs, report
