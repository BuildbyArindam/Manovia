"""Chunking: splitting a document body into overlapping passages.

The brief calls for 200-300-token chunks with overlap. Token counts differ by
a factor of 2-3 between an English word and a Devanagari or Bengali word
under a multilingual tokenizer, so this module chunks by **whitespace words**
(settings ``RAG_CHUNK_WORDS``, default 220 — inside the brief's band for
English, and the honest knob to turn for other scripts) rather than by model
tokens. ``RAG_CHUNK_OVERLAP_WORDS`` (default 50) keeps each boundary shared
between neighbouring chunks, so a passage that straddles a split is
retrievable from either side.

The split point is chosen on structure, never mid-word: paragraph boundaries
first, then sentence ends (including the Indic danda ``।``), and only a long
run-on paragraph is split at a word edge. The function is pure and
deterministic — the same text always produces the same chunks, which is what
makes ingest idempotent.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

#: Sentence enders for the three corpus languages, kept so a chunk boundary
#: can land at a real sentence end. Followed by whitespace + capital/Devanagari
#: or just whitespace (danda is unambiguous).
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")

MIN_WORDS_PER_CHUNK = 20


def _split_sentences(paragraph: str) -> list[str]:
    parts = _SENTENCE_END.split(paragraph.strip())
    return [part.strip() for part in parts if part.strip()]


def _word_count(text: str) -> int:
    return len(text.split())


def chunk_text(
    text: str,
    *,
    target_words: int = 220,
    overlap_words: int = 50,
    min_words: int = MIN_WORDS_PER_CHUNK,
) -> list[str]:
    """Split ``text`` into overlapping chunks of roughly ``target_words`` words.

    Never returns empty chunks; a text smaller than one chunk comes back as a
    single chunk unchanged. ``overlap_words`` is an upper bound — the overlap
    is always cut at a sentence boundary at or under it, so no sentence is
    ever sliced in half.
    """
    if target_words < min_words:
        raise ValueError("target_words must be at least min_words")
    if not 0 <= overlap_words < target_words:
        raise ValueError("overlap_words must be between 0 and target_words")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if not paragraphs:
        return []

    sentences: list[str] = []
    for paragraph in paragraphs:
        sentences.extend(_split_sentences(paragraph))
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    def _flush() -> None:
        nonlocal current, current_words
        if current:
            chunks.append(" ".join(current))
        current, current_words = [], 0

    for sentence in sentences:
        words = _word_count(sentence)
        if words > target_words:
            # A monster sentence: hard-split it at word edges so no chunk
            # exceeds the target by much.
            if current_words >= min_words:
                _flush()
            pieces = _hard_split(sentence, target_words)
            for piece in pieces[:-1]:
                chunks.append(piece)
            current, current_words = [pieces[-1]], _word_count(pieces[-1])
            continue

        if current_words + words > target_words and current_words >= min_words:
            _flush()
            overlap: list[str] = []
            overlap_words_count = 0
            # Walk the previous chunk's sentences backwards, taking whole
            # sentences up to the overlap budget.
            for previous in reversed(_sentences_of(chunks[-1])):
                if overlap_words_count + _word_count(previous) > overlap_words:
                    break
                overlap.insert(0, previous)
                overlap_words_count += _word_count(previous)
            current, current_words = overlap, overlap_words_count

        current.append(sentence)
        current_words += words

    _flush()
    # Merge a trailing fragment into its predecessor instead of shipping a
    # tiny last chunk.
    if len(chunks) > 1 and _word_count(chunks[-1]) < min_words:
        chunks[-2] = f"{chunks[-2]} {chunks.pop()}"
    return chunks


def _sentences_of(chunk: str) -> list[str]:
    return _split_sentences(chunk)


def _hard_split(sentence: str, target_words: int) -> list[str]:
    words = sentence.split()
    return [" ".join(words[i : i + target_words]) for i in range(0, len(words), target_words)]


def chunk_texts(texts: Sequence[str], **kwargs: int) -> list[list[str]]:
    """Chunk several texts with one configuration."""
    return [chunk_text(text, **kwargs) for text in texts]


__all__ = ["chunk_text", "chunk_texts"]
