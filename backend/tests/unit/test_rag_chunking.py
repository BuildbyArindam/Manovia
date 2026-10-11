"""Chunker unit tests: determinism, overlap, and structural boundaries.

The ingest is idempotent *because* the chunker is deterministic, so these
tests pin the properties the ingest relies on more than exact split points.
"""

from itertools import pairwise

from app.services.rag.chunking import MIN_WORDS_PER_CHUNK, chunk_text, chunk_texts

LONG_TEXT = (
    "# Introduction\n\n"
    "Sleep is the foundation on which a steady mind is built. When you sleep "
    "badly for a few nights in a row, everything else gets harder: focus goes, "
    "mood dips, small problems look enormous. This first paragraph is here to "
    "give the chunker something structural to work with.\n\n"
    "## Why a rhythm matters\n\n"
    "The body loves a rhythm. Waking at the same time every day anchors the "
    "internal clock, even on weekends. Morning light helps. Caffeine has a "
    "long tail, so a late cup keeps whispering at midnight. A wind-down hour "
    "tells the body what is coming next, and it listens.\n\n"
    "## At night, when the mind races\n\n"
    "If thoughts will not stop, get up briefly and write them down. The page "
    "can hold them until morning better than you can. Then return to bed and "
    "let the breath slow on a long, easy out-breath. Nothing needs solving "
    "right now, at this hour, in this dark.\n\n"
    "One more short paragraph to make sure there is a trailing structure "
    "boundary for the splitter to land on."
)


def test_chunks_are_deterministic() -> None:
    first = chunk_text(LONG_TEXT, target_words=60, overlap_words=15)
    second = chunk_text(LONG_TEXT, target_words=60, overlap_words=15)
    assert first == second
    assert len(first) > 1


def test_chunks_come_in_order_and_cover_the_document() -> None:
    pieces = chunk_text(LONG_TEXT, target_words=60, overlap_words=15)
    assert pieces[0].startswith("# Introduction")
    assert pieces[-1].rstrip().endswith("boundary for the splitter to land on.")
    # Every chunk respects the word floor (the loader's minimum).
    assert all(len(p.split()) >= MIN_WORDS_PER_CHUNK for p in pieces)


def test_overlap_is_real_text_shared_between_neighbours() -> None:
    pieces = chunk_text(LONG_TEXT, target_words=60, overlap_words=15)
    for previous, nxt in pairwise(pieces):
        # The closing words of chunk N recur at the top of chunk N+1 (the
        # boundary may introduce a heading, so compare word sets, not a
        # verbatim window).
        tail_words = set(previous.split()[-10:])
        head_words = set(nxt.split()[:20])
        assert len(tail_words & head_words) >= 5


def test_split_never_lands_mid_word() -> None:
    pieces = chunk_text(LONG_TEXT, target_words=30, overlap_words=8)
    original_words = LONG_TEXT.split()
    for piece in pieces:
        for word in piece.split():
            assert word in original_words, f"mangled word {word!r}"


def test_short_text_is_a_single_chunk() -> None:
    text = "Only a couple of sentences. Nothing to split here."
    assert chunk_text(text, target_words=220, overlap_words=50) == [text]


def test_empty_text_yields_no_chunks() -> None:
    assert chunk_text("   \n\n  ", target_words=220, overlap_words=50) == []


def test_chunk_texts_maps_over_documents() -> None:
    result = chunk_texts([LONG_TEXT, "Short one."], target_words=60, overlap_words=15)
    assert len(result) == 2
    assert len(result[0]) > 1 and result[1] == ["Short one."]
