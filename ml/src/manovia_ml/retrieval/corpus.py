"""The knowledge corpus and its passages, readable from ``ml/`` on its own.

Retrieval eval has to score against exactly the passages production serves, so
this module reproduces the backend's two-step build — front-matter parse, then
``chunk_text(body, 220, 50)`` with the title-anchored ``embed_text`` — and it
does so **without requiring the backend to be installed**:

* when ``app.content.knowledge`` and ``app.services.rag.chunking`` import, they
  are used directly, so there is nothing to drift;
* otherwise a local reader/chunker of the same shape takes over, and
  ``backend/tests/unit/test_rag_corpus_parity.py`` asserts the two agree on
  every document in the real corpus.

The alternative — importing the backend unconditionally — would make the ML
package uninstallable on its own (``ml/pyproject.toml`` deliberately treats
``app.*`` as an optional, lazily imported dependency, the same way
``manovia_ml.training.emotion.baselines`` does), and the alternative of only
ever duplicating the logic would leave the eval scoring passages that no chat
turn ever sees.

Nothing here writes anything: the corpus is read-only input, and its
fingerprint (``corpus_fingerprint``) is what a run logs so a report can say
which bytes it scored.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from manovia_ml.paths import ml_root

__all__ = [
    "CHUNK_OVERLAP_WORDS",
    "CHUNK_WORDS",
    "Document",
    "Passage",
    "build_passages",
    "corpus_fingerprint",
    "knowledge_dir",
    "load_documents",
]

#: Production defaults (``RAG_CHUNK_WORDS`` / ``RAG_CHUNK_OVERLAP_WORDS``). The
#: eval must use the same numbers or it measures a different chunking.
CHUNK_WORDS = 220
CHUNK_OVERLAP_WORDS = 50

#: Relative to the repository root, which is ``ml_root().parent``.
KNOWLEDGE_RELPATH = Path("backend") / "app" / "content" / "knowledge"

_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")
_MIN_WORDS_PER_CHUNK = 20


@dataclass(frozen=True)
class Document:
    """One curated knowledge document (the unit an eval label names)."""

    id: str
    title: str
    language: str
    tags: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class Passage:
    """One chunk: what a retriever scores, and what a person would be shown."""

    chunk_id: str
    doc_id: str
    title: str
    #: The chunk body alone — this is what the UI/prompt shows.
    text: str
    #: Title-anchored form an embedder sees (``ingest.plan_ingest`` builds the
    #: same string): the curated topical terms appear twice, so a short vague
    #: query still has something to match.
    embed_text: str
    language: str
    tags: tuple[str, ...]
    chunk_index: int
    chunk_total: int


def knowledge_dir() -> Path:
    """The markdown corpus directory, resolved from the ML root."""

    candidate = ml_root().parent / KNOWLEDGE_RELPATH
    if not candidate.is_dir():
        raise FileNotFoundError(
            f"knowledge corpus not found at {candidate}; run the retrieval eval from inside "
            "a full checkout of this repository"
        )
    return candidate


def load_documents(directory: Path | None = None) -> tuple[Document, ...]:
    """Every document in the corpus, sorted by id.

    Uses the backend's loader (and therefore its validation) when importable;
    otherwise parses the front-matter locally. Both paths read the same files.
    """

    root = directory or knowledge_dir()
    backend_docs = _load_with_backend(root)
    if backend_docs is not None:
        return backend_docs

    docs: list[Document] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.suffix != ".md":
            continue
        docs.append(_parse_document(entry.name, entry.read_text(encoding="utf-8")))
    if not docs:
        raise ValueError(f"knowledge corpus is empty: {root}")
    return tuple(docs)


def _load_with_backend(root: Path) -> tuple[Document, ...] | None:
    """The backend loader, or ``None`` when the backend is not installed."""

    try:
        from app.content.knowledge import load_knowledge_docs
    except ImportError:
        return None
    loaded = load_knowledge_docs(root)
    return tuple(
        Document(
            id=doc.id,
            title=doc.title,
            language=doc.language,
            tags=tuple(doc.tags),
            body=doc.body,
        )
        for doc in loaded
    )


def _parse_document(name: str, raw: str) -> Document:
    """Local front-matter reader: the same fields, none of the validation.

    Validation is the backend loader's job (and a corpus that fails it fails
    ``make test`` long before an eval reads it). What this must not do is
    silently mis-read a document, so anything unexpected raises.
    """

    if not raw.startswith("---"):
        raise ValueError(f"{name}: document must start with YAML front-matter")
    end = raw.find("\n---", 3)
    if end == -1:
        raise ValueError(f"{name}: front-matter is never closed")
    meta: Any = yaml.safe_load(raw[3:end])
    if not isinstance(meta, dict):
        raise ValueError(f"{name}: front-matter must be a mapping")
    missing = [key for key in ("id", "title", "tags", "language") if key not in meta]
    if missing:
        raise ValueError(f"{name}: front-matter missing {', '.join(missing)}")
    doc_id = str(meta["id"])
    if doc_id != name.removesuffix(".md"):
        raise ValueError(f"{name}: id {doc_id!r} does not match the filename stem")
    tags = meta["tags"]
    return Document(
        id=doc_id,
        title=str(meta["title"]).strip(),
        language=str(meta["language"]),
        tags=tuple(str(tag) for tag in tags) if isinstance(tags, list) else (),
        body=raw[end + 4 :].strip(),
    )


def build_passages(
    docs: Sequence[Document],
    *,
    chunk_words: int = CHUNK_WORDS,
    overlap_words: int = CHUNK_OVERLAP_WORDS,
) -> tuple[Passage, ...]:
    """Chunk every document into the passages a retriever scores."""

    chunker = _chunker()
    passages: list[Passage] = []
    for doc in docs:
        pieces = chunker(doc.body, target_words=chunk_words, overlap_words=overlap_words)
        if not pieces:
            raise ValueError(f"{doc.id}: chunker produced no chunks")
        total = len(pieces)
        for index, piece in enumerate(pieces):
            passages.append(
                Passage(
                    chunk_id=f"{doc.id}#{index:02d}",
                    doc_id=doc.id,
                    title=doc.title,
                    text=piece,
                    embed_text=f"{doc.title}\n{doc.title}\n\n{piece}",
                    language=doc.language,
                    tags=doc.tags,
                    chunk_index=index,
                    chunk_total=total,
                )
            )
    return tuple(passages)


def _chunker() -> Any:
    """The backend chunker when importable, else the local copy of it."""

    try:
        from app.services.rag.chunking import chunk_text
    except ImportError:
        return _chunk_text_local
    return chunk_text


def _chunk_text_local(text: str, *, target_words: int, overlap_words: int) -> list[str]:
    """A line-for-line copy of ``app.services.rag.chunking.chunk_text``.

    Kept in sync by ``backend/tests/unit/test_rag_corpus_parity.py``, which
    runs both implementations over the real corpus and compares every chunk.
    """

    if target_words < _MIN_WORDS_PER_CHUNK:
        raise ValueError("target_words must be at least min_words")
    if not 0 <= overlap_words < target_words:
        raise ValueError("overlap_words must be between 0 and target_words")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    sentences: list[str] = []
    for paragraph in paragraphs:
        sentences.extend(part.strip() for part in _SENTENCE_END.split(paragraph) if part.strip())
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    def flush() -> None:
        nonlocal current, current_words
        if current:
            chunks.append(" ".join(current))
        current, current_words = [], 0

    for sentence in sentences:
        words = len(sentence.split())
        if words > target_words:
            if current_words >= _MIN_WORDS_PER_CHUNK:
                flush()
            pieces = _hard_split(sentence, target_words)
            chunks.extend(pieces[:-1])
            current, current_words = [pieces[-1]], len(pieces[-1].split())
            continue

        if current_words + words > target_words and current_words >= _MIN_WORDS_PER_CHUNK:
            flush()
            overlap: list[str] = []
            overlap_count = 0
            previous_chunk = chunks[-1]
            previous_sentences = [
                part.strip() for part in _SENTENCE_END.split(previous_chunk) if part.strip()
            ]
            for previous in reversed(previous_sentences):
                if overlap_count + len(previous.split()) > overlap_words:
                    break
                overlap.insert(0, previous)
                overlap_count += len(previous.split())
            current, current_words = overlap, overlap_count

        current.append(sentence)
        current_words += words

    flush()
    if len(chunks) > 1 and len(chunks[-1].split()) < _MIN_WORDS_PER_CHUNK:
        chunks[-2] = f"{chunks[-2]} {chunks.pop()}"
    return chunks


def _hard_split(sentence: str, target_words: int) -> list[str]:
    words = sentence.split()
    return [" ".join(words[i : i + target_words]) for i in range(0, len(words), target_words)]


def corpus_fingerprint(passages: Sequence[Passage]) -> str:
    """sha256 over the passage ids, texts and languages.

    A run logs this so a report can state which corpus it scored: two evals
    that disagree are only comparable if this string matches.
    """

    digest = hashlib.sha256()
    for passage in passages:
        digest.update(passage.chunk_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(passage.language.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(passage.embed_text.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()
