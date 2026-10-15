"""BM25 unit tests: exact scoring, filters, and the Indic tokenisation it rests on.

The corpus for these tests is three documents, one of which exists only to make
a point: ``breathing-box`` is the document a *lexical* query ("box breathing
count") should find, and the Day-35 eval set says the Day-13 dense-only
retriever does not always find it. BM25 is the half of the hybrid that fixes
that, so its tests are written as "the term that is there is the term that
wins".
"""

import pytest

from app.services.rag.base import ChunkMetadata
from app.services.rag.bm25 import Bm25Index
from app.services.rag.embedders import tokenize


def meta(
    doc_id: str, title: str, language: str = "en", tags: tuple[str, ...] = ()
) -> ChunkMetadata:
    return ChunkMetadata(
        doc_id=doc_id,
        title=title,
        chunk_index=0,
        chunk_total=1,
        language=language,
        tags=tags,
    )


CHUNKS = (
    (
        "breathing-box#00",
        "Box breathing counts the breath in four equal steps of four counts each.",
        meta("breathing-box", "Box breathing", tags=("breathing",)),
    ),
    (
        "sleep-rhythm#00",
        "Sleep improves when the day has a shape and the evening is dim.",
        meta("sleep-rhythm", "A steady sleep rhythm", tags=("sleep",)),
    ),
    (
        "sleep-rhythm#01",
        "Sleep also improves when the evening is quiet and the light is low.",
        meta("sleep-rhythm", "A steady sleep rhythm", tags=("sleep",)),
    ),
    (
        "sleep-rhythm-hi#00",
        "नींद तब बेहतर होती है जब दिन का एक ढाँचा हो और शाम को रोशनी कम हो।",
        meta("sleep-rhythm-hi", "सोने का समय", language="hi", tags=("sleep",)),
    ),
    (
        "sleep-rhythm-bn#00",
        "দিনের একটা ছন্দ থাকলে ঘুম ভালো হয়, আর সন্ধ্যা হলে আলো কমিয়ে দিন।",
        meta("sleep-rhythm-bn", "ঘুমের ছন্দ", language="bn", tags=("sleep",)),
    ),
)


@pytest.fixture()
def index() -> Bm25Index:
    return Bm25Index.build(CHUNKS)


# ------------------------------------------------------------------ tokeniser #


class TestTokenize:
    """The shared lexical tokeniser: one definition of "a word", for every script.

    Regression for the Day-35 bug this replaced: the token pattern was an
    *alternation* (``\\w+|<marks>``), so a Devanagari or Bengali word was cut at
    every combining mark — "अकेलापन" became several fragments and "ঘুম" never
    matched its own document. The pattern is now a character-class union, and
    these tests fail loudly if it drifts back.
    """

    def test_hindi_words_survive_their_matras(self) -> None:
        assert tokenize("बहुत अकेलापन लगता है") == ["बहुत", "अकेलापन", "लगता", "है"]

    def test_bengali_words_stay_whole(self) -> None:
        assert tokenize("ঘুম না এলে কী করব") == ["ঘুম", "না", "এলে", "কী", "করব"]

    def test_gurmukhi_and_oriya_are_words_too(self) -> None:
        assert tokenize("ਸ਼ਾਂਤ ਸਾਹ") == ["ਸ਼ਾਂਤ", "ਸਾਹ"]
        assert tokenize("ଶାନ୍ତ ଶ୍ୱାସ") == ["ଶାନ୍ତ", "ଶ୍ୱାସ"]

    def test_latin_combining_marks_do_not_split_a_word(self) -> None:
        # "sa\u0301ns" is one word with a combining acute, not two tokens.
        assert tokenize("sa\u0301ns breathing") == ["sa\u0301ns", "breathing"]

    def test_case_folds_and_digits_stay(self) -> None:
        assert tokenize("Box 4-7-8 Breathing") == ["box", "4", "7", "8", "breathing"]

    def test_punctuation_is_not_a_word(self) -> None:
        assert tokenize("stop. wait — now?") == ["stop", "wait", "now"]

    def test_empty_and_symbol_only_input(self) -> None:
        assert tokenize("") == []
        assert tokenize("... — !!") == []


# --------------------------------------------------------------------- scoring #


class TestBm25Index:
    def test_the_exact_term_wins(self, index: Bm25Index) -> None:
        matches = index.search("box breathing four counts")
        assert matches[0].id == "breathing-box#00"

    def test_a_query_with_no_shared_term_scores_nothing(self, index: Bm25Index) -> None:
        # BM25 has nothing to say about a chunk it shares no term with, and
        # inventing a rank for silence is what makes a fused list lie.
        assert index.search("cryptocurrency portfolio rebalancing") == []

    def test_idf_prefers_the_rare_term(self, index: Bm25Index) -> None:
        # "sleep" is in two of the five chunks, "box" in one, so "box" carries
        # more information — the property the whole lexical half rests on.
        assert index.idf("box") > index.idf("sleep")
        # An unseen term gets the df=0 maximum: the formula, not a special case.
        # (The reranker's IDF map is built from `vocabulary`, so an unseen query
        # token gets a neutral weight there rather than this one.)
        assert index.idf("qqqneverseen") > index.idf("box")

    def test_language_filter_is_applied_after_ranking(self, index: Bm25Index) -> None:
        every = index.search("sleep")
        hindi = index.search("नींद", where={"language": "hi"})
        assert {match.metadata.language for match in every} >= {"en"}
        assert [match.id for match in hindi] == ["sleep-rhythm-hi#00"]

    def test_bengali_query_finds_the_bengali_document(self, index: Bm25Index) -> None:
        matches = index.search("ঘুম ভালো হয় কীভাবে")
        assert matches[0].id == "sleep-rhythm-bn#00"

    def test_k_caps_the_result_list(self, index: Bm25Index) -> None:
        assert len(index.search("sleep", k=1)) == 1

    def test_score_pair_agrees_with_search(self, index: Bm25Index) -> None:
        from_search = {match.id: match.score for match in index.search("sleep rhythm dim")}
        for chunk_id, score in from_search.items():
            assert index.score_pair("sleep rhythm dim", chunk_id) == pytest.approx(score)

    def test_score_pair_of_an_unknown_chunk_is_zero(self, index: Bm25Index) -> None:
        assert index.score_pair("sleep", "not-in-the-index#00") == 0.0

    def test_title_boost_makes_the_title_matter(self) -> None:
        plain = Bm25Index.build(CHUNKS, title_boost=0)
        boosted = Bm25Index.build(CHUNKS, title_boost=4)
        query = "steady rhythm"
        assert boosted.score_pair(query, "sleep-rhythm#00") > plain.score_pair(
            query, "sleep-rhythm#00"
        )

    def test_a_longer_document_is_not_rewarded_for_being_long(self, index: Bm25Index) -> None:
        # `b` is the length-normalisation knob: at b=1 a long chunk must not
        # out-score a short one on the same term frequency.
        strict = Bm25Index.build(CHUNKS, b=1.0)
        assert strict.score_pair("sleep", "sleep-rhythm#00") <= index.score_pair(
            "sleep", "sleep-rhythm#00"
        )

    def test_an_empty_index_answers_nothing(self) -> None:
        empty = Bm25Index.build([])
        assert len(empty) == 0
        assert empty.search("anything") == []
        assert empty.idf("anything") == 0.0

    def test_a_duplicate_chunk_id_is_refused(self) -> None:
        with pytest.raises(ValueError, match="duplicate chunk id"):
            Bm25Index.build([*CHUNKS, CHUNKS[0]])

    @pytest.mark.parametrize(("k1", "b", "boost"), [(-1.0, 0.5, 2), (1.2, 1.5, 2), (1.2, 0.5, -1)])
    def test_unusable_knobs_are_refused(self, k1: float, b: float, boost: int) -> None:
        with pytest.raises(ValueError):
            Bm25Index.build(CHUNKS, k1=k1, b=b, title_boost=boost)

    def test_the_vocabulary_is_the_inverted_lists(self, index: Bm25Index) -> None:
        assert "box" in index.vocabulary
        # Three, not one: the term is in the body once and the title is indexed
        # twice more (title_boost=2), which is the anchor both halves share.
        assert index.vocabulary["box"] == {"breathing-box#00": 3}
