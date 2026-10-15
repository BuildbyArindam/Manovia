"""Label-preserving paraphrases: the ``synthetic`` half of the eval set.

The hand-written set is 154 rows. The brief asks for 200+ queries, and the
honest way to get there without inventing new ground truth is to *reword* rows
whose labels a person already reviewed, never to imagine new ones. That is what
this module does, and it is deliberately narrow:

* **One transform per row, from a per-language pool.** Filler prefixes, a
  context sentence, contraction folding, a curated synonym swap, a keyword
  reduction, a casual typo. Nothing here can introduce a new topic, because
  nothing here writes new content words except from the swap maps.
* **Two screens, both recorded.** A row is dropped when it no longer shares a
  content token with its seed *or* with the titles/tags of its labelled
  documents (the wording drifted away from the label), and when it picks up a
  *rare* topical token belonging to a document it is **not** labelled with (the
  wording drifted towards a different label). Drops land in ``dropped.jsonl``,
  so "we generated 100" is checkable against "we threw 14 away".
* **Deterministic.** Each row gets its own stream from ``derive_seed(seed, id)``,
  so the output is a pure function of (input bytes, seed, code). ``dvc repro``
  re-derives it and ``dvc.lock`` holds the hash.

Every produced row carries ``source: "synthetic"``, ``generator:
"template-paraphrase-v1"``, ``seed_id`` and ``spot_checked: false`` — a human
reading a sample flips that flag, and the dataset card records what they found.
Synthetic rows never reach the test split (``retrieval.data.assign_split``).
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from manovia_ml.hashing import read_jsonl
from manovia_ml.paths import data_dir, resolve
from manovia_ml.retrieval.corpus import Document, load_documents
from manovia_ml.retrieval.data import RetrievalQuery
from manovia_ml.seeds import derive_seed

__all__ = [
    "GENERATOR_NAME",
    "Expansion",
    "augment_row",
    "expand",
    "token_screen",
]

GENERATOR_NAME = "template-paraphrase-v1"

#: Tokens that carry no topic in any language of the corpus. Used by the screens
#: only — never to filter the retrieval text itself.
_FUNCTION_WORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "being",
        "but",
        "by",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "not",
        "of",
        "on",
        "or",
        "should",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
        "am",
        "can",
        "could",
        "would",
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
        "ill",
        "ab",
        "agar",
        "apna",
        "aur",
        "bahut",
        "bhi",
        "chahiye",
        "hai",
        "hain",
        "ho",
        "hona",
        "hu",
        "hui",
        "hum",
        "ja",
        "jab",
        "jo",
        "ka",
        "kafi",
        "kar",
        "karna",
        "karte",
        "ke",
        "ki",
        "kya",
        "lekin",
        "matlab",
        "main",
        "mera",
        "meri",
        "mujhe",
        "na",
        "nahi",
        "ne",
        "pe",
        "par",
        "sab",
        "se",
        "tak",
        "to",
        "us",
        "uska",
        "uski",
        "usne",
        "wala",
        "wali",
        "ya",
        "ye",
        "yeh",
        "aap",
        "apko",
        "kaise",
        "kesa",
        "kesi",
        "kaha",
        "kab",
        "kyun",
        "kaun",
        "jitna",
        "utna",
        "bohot",
        "bahot",
        "hume",
        "tum",
        "tumhe",
        "ami",
        "amar",
        "amake",
        "amader",
        "apni",
        "ar",
        "bolo",
        "bolte",
        "che",
        "chilam",
        "chhilo",
        "dile",
        "ei",
        "ekhane",
        "eti",
        "eto",
        "haan",
        "hoy",
        "howbe",
        "kake",
        "kamon",
        "kar",
        "karbo",
        "karchhi",
        "kare",
        "karon",
        "keo",
        "ki",
        "kintu",
        "kom",
        "kor",
        "korar",
        "kore",
        "kotha",
        "kothay",
        "khub",
        "naki",
        "nije",
        "por",
        "pora",
        "shob",
        "tai",
        "thakbe",
        "thake",
        "tomar",
        "tumi",
        "beshi",
        "kive",
        "kivabe",
        "shuru",
        "korbo",
        "parbo",
        "parchi",
        "mane",
        "ashole",
    ]
)

#: Word characters *plus* the combining marks Indic scripts are written with,
#: as one character class (the construction ``app/services/nlp/lexicon.py``
#: arrived at on Day 6). Python's ``\w`` does not match vowel signs and matras
#: (Unicode Mn), and an *alternation* — ``[^\W_]+|[\u0900-\u097F]+`` — is not a
#: fix: it tokenises "बहुत" as "बह" + "ुत", which is what an earlier draft of
#: this module did and what the spot-check of the generated rows caught.
_MARKS = "\u0300-\u036f\u0900-\u097f\u0980-\u09ff\u0a00-\u0a7f\u0b00-\u0b7f"
_WORD_RE = re.compile(rf"(?:[^\W_]|[{_MARKS}])+", re.UNICODE)

#: What an apostrophe leaves behind ("don't" → "don" + "t"). The surface
#: transforms fold apostrophes away before tokenising so a contraction cannot
#: survive as two junk tokens in a keyword-style variant.
_CONTRACTION_FRAGMENTS = frozenset(
    {"m", "s", "t", "re", "ve", "ll", "d", "id", "thats", "whats", "theyre", "isnt", "arent"}
)

#: Words that must not end a truncated variant: "exam is tomorrow and feel sick
#: with" reads as a generator artefact, not as a hurried message. Trailing ones
#: are dropped, so a keyword/depersonalised variant ends on a content word.
_DANGLING_TAILS = frozenset(
    [
        "a",
        "an",
        "and",
        "but",
        "for",
        "from",
        "if",
        "in",
        "into",
        "of",
        "on",
        "or",
        "over",
        "the",
        "to",
        "under",
        "when",
        "with",
        "than",
        "then",
        "that",
        "what",
        "why",
        "how",
        "is",
        "are",
        "am",
        "be",
        "do",
        "does",
        "about",
        "every",
        "ke",
        "ki",
        "se",
        "ka",
        "par",
        "me",
        "mein",
        "aur",
        "ya",
        "o",
        "में",
        "को",
        "का",
        "की",
        "के",
        "और",
        "पर",
        "से",
        "तक",
        "ही",
        "तो",
        "मेरा",
        "this",
        "it",
    ]
)

# --------------------------------------------------------------------- #
# Surface transforms (meaning-preserving, by design)                      #
# --------------------------------------------------------------------- #

_FILLERS: dict[str, tuple[str, ...]] = {
    "en": ("honestly, ", "so ", "I mean, ", "look, ", "not sure this is the right place but "),
    "hi": ("मतलब ", "यार ", "देखिए, ", "असल में "),
    "hi-Latn": ("yaar ", "matlab ", "dekho, ", "asli baat ye hai ki "),
    "bn": ("আসলে ", "মানে ", "শুনুন, "),
    "bn-Latn": ("ashole ", "mane ", "shunen, "),
}

_CONTEXTS: dict[str, tuple[str, ...]] = {
    "en": (
        "it has been like this for a couple of weeks. ",
        "I tried ignoring it and that did not work. ",
        "I do not really know how to explain it, but ",
    ),
    "hi": (
        "यह कुछ हफ़्तों से चल रहा है। ",
        "मैंने नज़रअंदाज़ करने की कोशिश की, काम नहीं आया। ",
        "समझ नहीं आ रहा कैसे कहूँ, पर ",
    ),
    "hi-Latn": (
        "kuch hafto se aisa hi chal raha hai. ",
        "ignore karne ki koshish ki, kaam nahi aaya. ",
        "samajh nahi aa raha kaise kahu, par ",
    ),
    "bn": (
        "কয়েক সপ্তাহ ধরে এমন হচ্ছে। ",
        "উপেক্ষা করার চেষ্টা করেছি, কাজ হয়নি। ",
        "কীভাবে বলি বুঝতে পারছি না, তবে ",
    ),
    "bn-Latn": (
        "koyek soptah dhore emon hocche. ",
        "upekkha korar chesta korechi, kaaj hoyni. ",
        "kibhabe boli bujhte parchi na, tobe ",
    ),
}

#: Curated swaps. Small on purpose: a big synonym table is a generator of
#: sentences nobody writes, and a wrong swap silently changes the label.
_SYNONYMS: dict[str, dict[str, str]] = {
    "en": {
        "tired": "exhausted",
        "scared": "frightened",
        "nervous": "on edge",
        "worried": "anxious",
        "hard": "tough",
        "stop": "quit",
        "start": "begin",
        "talk": "speak",
        "bad": "awful",
        "sad": "low",
        "help": "support",
        "fast": "quickly",
    },
    "hi": {
        "बहुत": "काफ़ी",
        "तरीका": "विधि",
        "बताइए": "बताओ",
        "घबराहट": "बेचैनी",
        "मुश्किल": "कठिन",
        "डर": "भय",
        "दिनचर्या": "रूटीन",
        "इंतज़ार": "प्रतीक्षा",
        "अकेलापन": "एकलापन",
    },
    "hi-Latn": {
        "nahi": "nahin",
        "kaise": "kese",
        "bahut": "bohot",
        "batao": "bataiye",
        "mujhe": "muje",
        "karna": "karna hai",
        "pareshan": "tension me",
        "udaas": "down",
    },
    "bn": {
        "খুব": "প্রচুর",
        "কঠিন": "মুশকিল",
        "নিয়ম": "পদ্ধতি",
        "সকালে": "সকালবেলা",
        "বলব": "বলবো",
        "সাহায্য": "সহায়তা",
        "সমস্যা": "অসুবিধা",
    },
    "bn-Latn": {
        "khub": "beshi",
        "kothin": "moshkil",
        "kive": "kibhabe",
        "amon": "erokom",
        "bolbo": "bolte pari",
    },
}

_CONTRACTIONS: dict[str, str] = {
    "can't": "cannot",
    "don't": "do not",
    "doesn't": "does not",
    "i'm": "i am",
    "it's": "it is",
    "that's": "that is",
    "there's": "there is",
    "won't": "will not",
    "i've": "i have",
    "i'd": "i would",
    "what's": "what is",
    "how's": "how is",
}


def _filler(query: str, lang: str, rng: random.Random) -> str:
    return rng.choice(_FILLERS[lang]) + query[0].lower() + query[1:]


def _context(query: str, lang: str, rng: random.Random) -> str:
    return rng.choice(_CONTEXTS[lang]) + query[0].lower() + query[1:]


def _contractions(query: str, lang: str, rng: random.Random) -> str:
    del rng
    if lang != "en":
        return query
    out = query
    for short, long_form in _CONTRACTIONS.items():
        out = re.sub(rf"\b{re.escape(short)}\b", long_form, out, flags=re.IGNORECASE)
    return out


def _synonyms(query: str, lang: str, rng: random.Random) -> str:
    table = _SYNONYMS[lang]
    out = query
    for source, target in table.items():
        pattern = re.compile(
            re.escape(source), re.IGNORECASE if lang in {"en", "hi-Latn", "bn-Latn"} else 0
        )
        if pattern.search(out):
            out = pattern.sub(target, out, count=1)
            break
    del rng
    return out


def _keyword(query: str, lang: str, rng: random.Random) -> str:
    """Drop function words: the 'search box' version of the same question."""

    del lang, rng
    tokens = _WORD_RE.findall(query.replace("'", ""))
    kept = [
        token
        for token in tokens
        if token.casefold() not in _FUNCTION_WORDS
        and token.casefold() not in _CONTRACTION_FRAGMENTS
        and len(token) > 1
    ]
    if len(kept) < 2:
        return query
    return _trim_tail(" ".join(kept[:7]))


def _depersonalise(query: str, lang: str, rng: random.Random) -> str:
    """Turn 'I can't sleep' into 'not sleeping properly' — the vague cousin."""

    del rng
    if lang in {"hi", "bn"}:
        return query
    tokens = _WORD_RE.findall(query.replace("'", ""))
    first_person = {"i", "me", "my", "am", "im", "main", "mujhe", "ami", "amake", "mera", "amar"}
    kept = [token for token in tokens if token.casefold() not in first_person]
    if len(kept) < 2:
        return query
    return _trim_tail(" ".join(kept[:9]))


def _typo(query: str, lang: str, rng: random.Random) -> str:
    """One casual spelling slip, Latin scripts only (a typo in Devanagari is a
    different phenomenon — a wrong matra changes the word, not its spelling)."""

    if lang not in {"en", "hi-Latn", "bn-Latn"}:
        return query
    words = query.split()
    if len(words) < 3:
        return query
    index = rng.randrange(len(words))
    word = words[index]
    if len(word) < 5:
        return query
    position = rng.randrange(1, len(word) - 1)
    words[index] = word[:position] + word[position + 1 :]
    return " ".join(words)


#: Transforms that strip words away change the query's *shape*: what is left is
#: a search-box fragment, and labelling it with the seed's "scenario" style
#: would misdescribe the row the metrics are sliced by.
_TRUNCATING: dict[str, str] = {"keyword": "keyword", "depersonalise": "keyword"}


def _trim_tail(text: str) -> str:
    """Drop trailing function words a truncation left hanging."""

    words = _no_immediate_repeat(text).split()
    while len(words) > 2 and words[-1].casefold().strip(",.") in _DANGLING_TAILS:
        words.pop()
    return " ".join(words)


def _no_immediate_repeat(text: str) -> str:
    """Collapse a word a prefix just duplicated ("মানে কাউন্সেলিং মানে …")."""

    words = text.split()
    out: list[str] = []
    for word in words:
        if out and out[-1].casefold().strip(",.") == word.casefold().strip(",."):
            continue
        out.append(word)
    return " ".join(out)


#: Which transforms are allowed for a no-answer row. Prefixing "it has been like
#: this for a couple of weeks." to "what is the weather tomorrow" produces a
#: sentence no user would send, and an unrealistic no-answer row makes the
#: abstain metrics look better than they are.
_NO_ANSWER_POOL = ("contractions", "filler", "keyword", "synonyms", "typo")

#: The pool, in the order the seeded choice draws from it. Weights are implicit:
#: one uniform draw, so no transform dominates the expanded set.
TRANSFORMS: dict[str, Callable[[str, str, random.Random], str]] = {
    "filler": _filler,
    "context": _context,
    "contractions": _contractions,
    "synonyms": _synonyms,
    "keyword": _keyword,
    "depersonalise": _depersonalise,
    "typo": _typo,
}


# --------------------------------------------------------------------- #
# Screens                                                                 #
# --------------------------------------------------------------------- #


def _content_tokens(text: str) -> set[str]:
    return {
        token.casefold()
        for token in _WORD_RE.findall(text)
        if token.casefold() not in _FUNCTION_WORDS
    }


def _other_topic_tags(docs: Sequence[Document], relevant: Sequence[str]) -> set[str]:
    """Curated topic words that belong to documents this row is *not* labelled with.

    Tags are the corpus's own controlled vocabulary ("grounding", "exams",
    "loneliness", "thinking-traps"), so they are the right thing to screen on.
    An earlier version screened on unique *title* words as well and rejected
    paraphrases for containing "morning", "small" or "help" — words that are in
    a title by accident and carry no topic. A tag collision is different: it
    means the reworded query now names another document's subject, and the
    inherited label is no longer safe.
    """

    owners: dict[str, set[str]] = {}
    for doc in docs:
        for tag in doc.tags:
            for token in (tag.casefold(), *re.split(r"[-\s]+", tag.casefold())):
                if len(token) >= 5 and token not in _FUNCTION_WORDS:
                    owners.setdefault(token, set()).add(doc.id)
    relevant_set = set(relevant)
    return {
        token
        for token, doc_ids in owners.items()
        if not (doc_ids & relevant_set) and len(doc_ids) <= 2
    }


def token_screen(candidate: str, *, seed: RetrievalQuery, docs: Sequence[Document]) -> str | None:
    """``None`` when the paraphrase survives; a reason string when it does not."""

    candidate_tokens = _content_tokens(candidate)
    if not candidate_tokens:
        return "no-content-tokens"

    allowed = _content_tokens(seed.query)
    for doc in docs:
        if doc.id in seed.relevant_docs:
            allowed |= _content_tokens(doc.title)
            allowed |= {tag.casefold() for tag in doc.tags}
    if not (candidate_tokens & allowed):
        return "drifted-from-label"

    # Only *newly introduced* words can be drift: the seed's own wording is the
    # reviewer's wording, and rejecting a row for keeping the word "panic" that
    # its hand-written seed already contained (the spot-check found exactly
    # that) would punish fidelity.
    rare = _other_topic_tags(docs, seed.relevant_docs) - _content_tokens(seed.query)
    picked_up = candidate_tokens & rare
    if picked_up:
        return f"picks-up-other-topic:{sorted(picked_up)[0]}"
    return None


# --------------------------------------------------------------------- #
# Expansion                                                               #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class Expansion:
    """The result of one augmentation run."""

    rows: tuple[RetrievalQuery, ...]
    dropped: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def synthetic(self) -> tuple[RetrievalQuery, ...]:
        return tuple(row for row in self.rows if row.source == "synthetic")


def augment_row(
    seed: RetrievalQuery,
    *,
    docs: Sequence[Document],
    seed_value: int,
    index: int = 0,
) -> tuple[RetrievalQuery | None, dict[str, Any] | None]:
    """One paraphrase of one row, or ``(None, drop record)``."""

    rng = random.Random(derive_seed(seed_value, f"{seed.id}:{index}"))
    names = sorted(_NO_ANSWER_POOL if seed.style == "no-answer" else TRANSFORMS)
    # Draw up to three transforms so a rejected one does not cost the row.
    for attempt in range(3):
        name = names[(rng.randrange(len(names)) + attempt) % len(names)]
        candidate = TRANSFORMS[name](seed.query, seed.lang, rng).strip()
        if candidate.casefold() == seed.query.casefold() or len(candidate) < 4:
            continue
        reason = token_screen(candidate, seed=seed, docs=docs)
        if reason is not None:
            return None, {
                "seed_id": seed.id,
                "transform": name,
                "candidate": candidate,
                "reason": reason,
            }
        row = RetrievalQuery(
            id=f"rt-syn-{index:04d}-{_short(seed.id)}",
            query=candidate,
            lang=seed.lang,
            style=_TRUNCATING.get(name, seed.style),
            relevant_docs=list(seed.relevant_docs),
            has_answer=seed.has_answer,
            source="synthetic",
            generator=GENERATOR_NAME,
            seed_id=seed.id,
            spot_checked=False,
            no_answer_reason=seed.no_answer_reason,
            note=f"paraphrase of {seed.id} via {name}",
        )
        return row, None
    return None, {"seed_id": seed.id, "transform": "none", "candidate": "", "reason": "no-variant"}


def _short(seed_id: str) -> str:
    return seed_id.removeprefix("rt-").replace("-", "")


def expand(
    seeds: Sequence[RetrievalQuery],
    *,
    docs: Sequence[Document],
    target: int,
    seed: int,
) -> Expansion:
    """Expand ``seeds`` with up to ``target`` synthetic rows.

    Rows are visited in a seeded shuffled order, so which hand-written rows get
    a paraphrase is a function of the seed rather than of file order.
    """

    order = list(seeds)
    random.Random(derive_seed(seed, "augment-order")).shuffle(order)
    rows: list[RetrievalQuery] = []
    dropped: list[dict[str, Any]] = []
    seen_queries = {row.query.casefold() for row in seeds}
    index = 0
    for source_row in order:
        if len(rows) >= target:
            break
        produced, drop = augment_row(source_row, docs=docs, seed_value=seed, index=index)
        index += 1
        if produced is None:
            if drop is not None:
                dropped.append(drop)
            continue
        if produced.query.casefold() in seen_queries:
            dropped.append(
                {"seed_id": source_row.id, "transform": produced.note, "reason": "duplicate"}
            )
            continue
        seen_queries.add(produced.query.casefold())
        rows.append(produced)

    # Ids must be dense and sorted for a stable file hash: renumber once, after
    # the screens, so a dropped row cannot leave a hole that moves every later id.
    renumbered: list[RetrievalQuery] = []
    for position, row in enumerate(rows, start=1):
        assert row.seed_id is not None
        renumbered.append(
            RetrievalQuery(
                id=f"rt-syn-{position:04d}",
                query=row.query,
                lang=row.lang,
                style=row.style,
                relevant_docs=list(row.relevant_docs),
                has_answer=row.has_answer,
                source="synthetic",
                generator=row.generator,
                seed_id=row.seed_id,
                spot_checked=False,
                no_answer_reason=row.no_answer_reason,
                note=row.note,
            )
        )
    return Expansion(rows=tuple(renumbered), dropped=tuple(dropped))


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m manovia_ml.retrieval.augment`` — write the expanded eval set."""

    parser = argparse.ArgumentParser(description="Expand the hand-written retrieval eval set.")
    parser.add_argument(
        "--src", default=None, help="hand-written JSONL (default: the committed one)"
    )
    parser.add_argument("--out-dir", default="data/retrieval")
    parser.add_argument("--target", type=int, default=100, help="synthetic rows to produce")
    parser.add_argument("--seed", type=int, default=20261013)
    args = parser.parse_args(argv)

    src = resolve(args.src) if args.src else data_dir() / "retrieval" / "retrieval_queries.jsonl"
    out_dir = resolve(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    seeds = [RetrievalQuery.model_validate(row) for row in read_jsonl(src)]
    docs = load_documents()
    known = {doc.id for doc in docs}
    for seed in seeds:
        unknown = [doc for doc in seed.relevant_docs if doc not in known]
        if unknown:
            raise SystemExit(f"{seed.id}: labels {unknown} are not in the corpus")

    result = expand(seeds, docs=docs, target=args.target, seed=args.seed)
    all_rows = [*seeds, *result.rows]
    _write_jsonl(out_dir / "expanded.jsonl", [row.model_dump(mode="json") for row in all_rows])
    _write_jsonl(out_dir / "dropped.jsonl", list(result.dropped))
    manifest = {
        "generator": GENERATOR_NAME,
        "seed": args.seed,
        "source": str(src),
        "handwritten_rows": len(seeds),
        "synthetic_rows": len(result.rows),
        "total_rows": len(all_rows),
        "dropped": len(result.dropped),
        "dropped_reasons": _reason_counts(result.dropped),
        "by_lang": _counts_by(row.lang for row in all_rows),
        "by_source": _counts_by(row.source for row in all_rows),
    }
    (out_dir / "expand_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"handwritten={len(seeds)} synthetic={len(result.rows)} dropped={len(result.dropped)} "
        f"total={len(all_rows)}"
    )
    print(f"wrote {out_dir / 'expanded.jsonl'}")
    return 0


def _reason_counts(dropped: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in dropped:
        reason = str(row.get("reason", "")).split(":", 1)[0]
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _counts_by(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":  # pragma: no cover - exercised through the DVC stage
    raise SystemExit(main())
