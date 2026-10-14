"""Expanding the hand-written seed cases into 2,000+ synthetic training rows.

Three stages, in this order, each one recorded per row so a human can audit what
happened to any sentence:

1. **Template + paraphrase** (:mod:`manovia_ml.training.safety.providers`).
   Every seed gets up to ``paraphrases_per_seed`` rewrites from the configured
   paraphraser — the offline :class:`FakeParaphraser` by default, the backend's
   LLM chain when a human runs ``--provider llm``.
2. **Adversarial augmentation** (:func:`adversarial_variants`). Typos, spacing
   tricks, romanisation of Devanagari/Bengali, English code-mixing and emoji.
   These are the transforms a real message arrives with and a clean corpus never
   sees, so a model trained only on clean text learns the *clean* distribution.
3. **Filtering** (:func:`filter_rows`). The rules engine is the arbiter of label
   consistency, plus a method-word screen, a cue-retention check and a
   de-duplication pass. Every dropped row is counted by reason and written to the
   review file with its seed id, because a silent filter is a silent dataset.

**Provenance is not optional.** Every row carries ``provenance.source``
(``seed``/``paraphrase``/``adversarial``), the seed id it came from, the generator
name, the strategy and rule that produced it, the transforms applied, and what
the rules engine thought of it. That is the "human-checkable provenance" the
brief asks for, and it is what makes a suspicious row findable later.

The frozen Day-9 test split never enters this module: the caller passes seeds in,
and the only files this package writes are the ones the DVC stage names.
"""

from __future__ import annotations

import random
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from manovia_ml.seeds import seeded_rng
from manovia_ml.training.safety.taxonomy import severity

__all__ = [
    "ADVERSARIAL_TRANSFORMS",
    "BENGALI_TO_LATIN",
    "CODE_MIX",
    "DEVANAGARI_TO_LATIN",
    "EMOJI",
    "ExpansionConfig",
    "ExpansionResult",
    "GeneratedRow",
    "RulesVerdict",
    "adversarial_variants",
    "code_mix",
    "emoji",
    "expand",
    "method_word_problems",
    "romanise",
    "spacing",
    "typo",
]


# --------------------------------------------------------------------------- #
# The row shape                                                                #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GeneratedRow:
    """One synthetic training row, with everything needed to audit it."""

    id: str
    text: str
    lang: str
    label: str
    category: str
    notes: str
    source: str  # seed | paraphrase | adversarial
    seed_id: str
    generator: str
    strategy: str
    rule_id: str
    transforms: tuple[str, ...] = ()
    rules_level: str = ""
    rules_agrees: bool = True
    cue_retained: bool = True

    def as_dict(self) -> dict[str, Any]:
        """The JSONL shape: the eval dataset's fields plus a ``provenance`` block."""
        return {
            "id": self.id,
            "text": self.text,
            "lang": self.lang,
            "label": self.label,
            "category": self.category,
            "notes": self.notes,
            "provenance": {
                "source": self.source,
                "seed_id": self.seed_id,
                "generator": self.generator,
                "strategy": self.strategy,
                "rule_id": self.rule_id,
                "transforms": list(self.transforms),
                "rules_level": self.rules_level,
                "rules_agrees": self.rules_agrees,
                "cue_retained": self.cue_retained,
            },
        }


@dataclass(frozen=True)
class RulesVerdict:
    """The slice of a rules-engine assessment the filter needs.

    Deliberately not the backend's ``RiskAssessment``: ``ml/`` must run without
    the backend checkout, and the filter only ever asks three questions — what
    level, which categories, and were any hits discounted by pragmatics.
    """

    level: str
    categories: tuple[str, ...] = ()
    negated_hits: int = 0
    figurative_hits: int = 0

    @property
    def discounted(self) -> bool:
        return self.negated_hits > 0 or self.figurative_hits > 0


#: What the filter does when no rules engine is importable. The backend is
#: installed in this repository's environment, so this is the degraded path for a
# machine that only wants the linear/catalogue checks; it is reported in the run
# rather than hidden.
UNAVAILABLE = RulesVerdict(level="", categories=())


# --------------------------------------------------------------------------- #
# Adversarial transforms                                                       #
# --------------------------------------------------------------------------- #

#: The transform names, in the order they are tried. Recorded per row.
ADVERSARIAL_TRANSFORMS: Final[tuple[str, ...]] = (
    "typo",
    "spacing",
    "romanise",
    "code_mix",
    "emoji",
)

#: Emoji a person might actually put next to a low or benign message. Nothing
#: celebratory is offered for a risk row — that would be a label error.
EMOJI: Final[dict[str, tuple[str, ...]]] = {
    "risk": ("😞", "😢", "💔", "😔", "🥺"),
    "benign": ("🙂", "👍", "😊", "☕", "🌧️"),
}

#: English fragments spliced into an Indic sentence (code-mixing). Chosen to be
#: register-plausible for a young Indian/Bangladeshi speaker typing in a mixed
#: register; none of them asserts or denies risk, so the label survives.
CODE_MIX: Final[dict[str, tuple[str, ...]]] = {
    "hi": ("seriously", "yaar", "i cannot", "honestly", "please"),
    "hi-Latn": ("seriously", "yaar", "i cannot", "honestly", "please"),
    "bn": ("seriously", "yaar", "i cannot", "honestly", "please"),
    "en": (),
}

#: Devanagari -> Latin. Approximate on purpose: real romanisation is approximate
#: (that is why ``hi-Latn`` exists as its own language in the eval set), and a
#: transliterator that were perfect would produce text nobody actually types.
DEVANAGARI_TO_LATIN: Final[dict[str, str]] = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ee",
    "उ": "u",
    "ऊ": "oo",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऋ": "ri",
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "n",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "n",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    "ळ": "l",
    "ा": "aa",
    "ि": "i",
    "ी": "ee",
    "ु": "u",
    "ू": "oo",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ृ": "ri",
    # the keys are Devanagari signs; that is the point of the table
    "्": "",
    "ं": "n",
    "ँ": "n",
    "ः": "h",  # noqa: RUF001  Devanagari visarga; the table is Devanagari by design
    "़": "",
    "्‍": "",
    "​": "",
}

#: Bengali -> Latin, same caveat.
BENGALI_TO_LATIN: Final[dict[str, str]] = {
    "অ": "o",
    "আ": "aa",
    "ই": "i",
    "ঈ": "ee",
    "উ": "u",
    "ঊ": "oo",
    "ঋ": "ri",
    "এ": "e",
    "ঐ": "ai",
    "ও": "o",
    "ঔ": "au",
    "ক": "k",
    "খ": "kh",
    "গ": "g",
    "ঘ": "gh",
    "ঙ": "n",
    "চ": "ch",
    "ছ": "chh",
    "জ": "j",
    "ঝ": "jh",
    "ञ": "n",
    "ট": "t",
    "ঠ": "th",
    "ড": "d",
    "ঢ": "dh",
    "ণ": "n",
    "ত": "t",
    "থ": "th",
    "দ": "d",
    "ধ": "dh",
    "ন": "n",
    "প": "p",
    "ফ": "ph",
    "ব": "b",
    "ভ": "bh",
    "ম": "m",
    "য": "y",
    "র": "r",
    "ল": "l",
    "শ": "sh",
    "ষ": "sh",
    "স": "s",
    "হ": "h",
    "া": "aa",
    "ি": "i",
    "ী": "ee",
    "ু": "u",
    "ূ": "oo",
    "ে": "e",
    "ৈ": "ai",
    "ো": "o",
    "ৌ": "au",
    "্": "",
    "ং": "n",
    "ঃ": "h",
    "ৎ": "t",
    "़": "",
}

#: Keyboard neighbours for the typo transform (Latin only; Indic typos are
#: handled by the spelling variants in the paraphraser and by spacing).
_NEIGHBOURS: Final[dict[str, str]] = {
    "a": "s",
    "b": "v",
    "c": "x",
    "d": "f",
    "e": "r",
    "f": "d",
    "g": "f",
    "h": "g",
    "i": "u",
    "j": "h",
    "k": "j",
    "l": "k",
    "m": "n",
    "n": "m",
    "o": "p",
    "p": "o",
    "q": "w",
    "r": "e",
    "s": "a",
    "t": "r",
    "u": "y",
    "v": "c",
    "w": "q",
    "x": "z",
    "y": "t",
    "z": "x",
}

#: The method-word screen. Mirrors the one in ``evals/build_crisis_dataset.py``
#: and ``backend/tests/safety/test_escalation.py``: synthetic crisis data that
#: described a means would be unsafe training material, so a row that grows one
#: during augmentation is dropped rather than repaired. Latin words match on word
#: boundaries, Indic ones as substrings (combining marks make ``\\b`` unreliable
#: there).
_METHOD_WORDS_LATIN: Final[tuple[str, ...]] = (
    "pill",
    "pills",
    "tablet",
    "tablets",
    "overdose",
    "dose",
    "rope",
    "noose",
    "hang",
    "hanging",
    "gun",
    "firearm",
    "shoot",
    "knife",
    "razor",
    "blade",
    "poison",
    "jump",
    "bridge",
    "railway",
    "platform",
    "gas",
    "method",
    "successful",
    "unsuccessful",
    "zehar",
    "zehreele",
    "goli",
    "goliyan",
    "goliyon",
    "phansi",
    "faansi",
    "fanda",
    "bandook",
    "chaku",
    "chhuri",
)

_METHOD_WORDS_INDIC: Final[tuple[str, ...]] = (
    "ज़हर",
    "ज़हरीले",
    "ज़हरीली",
    "गोली",
    "गोलियाँ",
    "गोलियों",
    "फांसी",
    "फँदा",
    "बंदूक",
    "चाकू",
    "छुरी",
    "বিষ",
    "বন্দুক",
    "ফাঁসি",
    "ফাঁস",
    "ঝুলে",
    "ঝুলতে",
    "ছুরি",
    "ব্লেড",
)


def method_word_problems(text: str) -> list[str]:
    """Method words found in ``text``; empty means the row is safe to keep."""
    problems: list[str] = []
    lowered = text.casefold()
    for word in _METHOD_WORDS_LATIN:
        if _latin_word_in(lowered, word):
            problems.append(word)
    for word in _METHOD_WORDS_INDIC:
        if word in text:
            problems.append(word)
    return problems


def _latin_word_in(haystack: str, word: str) -> bool:
    import re

    return re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", haystack) is not None


def typo(text: str, rng: random.Random) -> str:
    """One keyboard-slip typo: a swapped, dropped, doubled or substituted character."""
    indices = [index for index, char in enumerate(text) if char.casefold() in _NEIGHBOURS]
    if not indices:
        return text
    index = rng.choice(indices)
    kind = rng.choice(("swap", "drop", "double", "substitute"))
    if kind == "swap" and index + 1 < len(text):
        return text[:index] + text[index + 1] + text[index] + text[index + 2 :]
    if kind == "drop":
        return text[:index] + text[index + 1 :]
    if kind == "double":
        return text[: index + 1] + text[index] + text[index + 1 :]
    char = text[index]
    return text[:index] + _NEIGHBOURS[char.casefold()] + text[index + 1 :]


def spacing(text: str, rng: random.Random) -> str:
    """A spacing trick: a space pushed inside a word, or one removed before a comma."""
    words = text.split(" ")
    candidates = [i for i, word in enumerate(words) if len(word) > 4]
    if candidates:
        index = rng.choice(candidates)
        word = words[index]
        cut = rng.randrange(1, len(word))
        words[index] = f"{word[:cut]} {word[cut:]}"
        return " ".join(words)
    if ", " in text:
        return text.replace(", ", ",", 1)
    return text


def romanise(text: str, lang: str) -> str:
    """Devanagari/Bengali -> Latin, and the language code with it."""
    if lang in ("hi", "hi-Latn"):
        return "".join(DEVANAGARI_TO_LATIN.get(char, char) for char in text)
    if lang == "bn":
        return "".join(BENGALI_TO_LATIN.get(char, char) for char in text)
    return text


def code_mix(text: str, lang: str, rng: random.Random) -> str:
    """Splice one English word or fragment into an Indic sentence."""
    options = CODE_MIX.get(lang, ())
    if not options:
        return text
    fragment = rng.choice(options)
    if rng.random() < 0.5:
        return f"{fragment}, {text}"
    return f"{text} {fragment}"


def emoji(text: str, label: str, rng: random.Random) -> str:
    """Append one emoji from the register-appropriate set."""
    pool = EMOJI["risk" if label not in ("none", "low") else "benign"]
    return f"{text} {rng.choice(pool)}"


def adversarial_variants(
    row: GeneratedRow,
    *,
    count: int,
    rng: random.Random,
) -> list[GeneratedRow]:
    """Up to ``count`` adversarial variants of one already-generated row.

    Each variant applies exactly one transform, so the row's provenance names a
    single thing that changed. A transform that changes nothing (a typo on a
    three-letter word) returns the text unchanged and is skipped rather than
    recorded as a no-op.
    """
    out: list[GeneratedRow] = []
    for index in range(count):
        transform = ADVERSARIAL_TRANSFORMS[index % len(ADVERSARIAL_TRANSFORMS)]
        new_lang = row.lang
        if transform == "typo":
            new_text = typo(row.text, rng)
        elif transform == "spacing":
            new_text = spacing(row.text, rng)
        elif transform == "romanise":
            new_text = romanise(row.text, row.lang)
            if row.lang == "hi":
                new_lang = "hi-Latn"
            elif row.lang == "bn":
                new_lang = "bn-Latn"
        elif transform == "code_mix":
            new_text = code_mix(row.text, row.lang, rng)
        else:
            new_text = emoji(row.text, row.label, rng)
        if new_text == row.text:
            continue
        out.append(
            GeneratedRow(
                id=f"{row.id}-{transform}",
                text=new_text,
                lang=new_lang,
                label=row.label,
                category=row.category,
                notes=f"{row.notes} [adversarial: {transform}]",
                source="adversarial",
                seed_id=row.seed_id,
                generator=row.generator,
                strategy=transform,
                rule_id=f"adv.{transform}",
                transforms=(*row.transforms, transform),
                rules_level=row.rules_level,
                rules_agrees=row.rules_agrees,
                cue_retained=row.cue_retained,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Configuration and the driver                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExpansionConfig:
    """How big the expansion is and how much of it is adversarial."""

    #: Rewrites per seed from the paraphraser.
    paraphrases_per_seed: int = 4
    #: Adversarial variants per *kept* row.
    adversarial_per_row: int = 1
    #: Hard cap, so a mis-set config cannot write a million rows.
    max_rows: int = 20_000
    #: Rows are shuffled with this seed before the cap is applied, so the cap
    #: takes a representative slice rather than the first N seeds.
    seed: int = 20261013

    def validate(self) -> None:
        if self.paraphrases_per_seed < 0:
            raise ValueError("paraphrases_per_seed must be >= 0")
        if self.adversarial_per_row < 0:
            raise ValueError("adversarial_per_row must be >= 0")
        if self.max_rows < 1:
            raise ValueError("max_rows must be >= 1")


@dataclass
class ExpansionResult:
    """The kept rows, the dropped ones, and the counts that explain both."""

    rows: list[GeneratedRow] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)
    drop_reasons: Counter[str] = field(default_factory=Counter)
    seed_count: int = 0
    paraphrase_count: int = 0
    adversarial_count: int = 0

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "seeds": self.seed_count,
            "paraphrases": self.paraphrase_count,
            "adversarial": self.adversarial_count,
            "kept": len(self.rows),
            "dropped": len(self.dropped),
            "drop_reasons": dict(sorted(self.drop_reasons.items())),
        }


def expand(
    seeds: Sequence[Mapping[str, Any]],
    *,
    paraphraser: Any,
    config: ExpansionConfig,
    rules_assessor: Callable[[str, str], RulesVerdict] | None = None,
    generator_name: str = "fake-v1",
) -> ExpansionResult:
    """Expand ``seeds`` into rows. Pure: it writes nothing and touches no network.

    Every candidate is annotated with what the rules engine made of it
    (:meth:`GeneratedRow.rules_level`, ``rules_agrees``, ``cue_retained``) and
    then either kept or dropped with a recorded reason. Nothing is dropped
    silently, because a dataset whose filter cannot be audited is a dataset
    nobody can trust.
    """
    config.validate()
    result = ExpansionResult(seed_count=len(seeds))
    seen: set[str] = set()

    for position, seed in enumerate(seeds):
        text = str(seed["text"])
        lang = str(seed.get("lang", "en"))
        label = str(seed["label"]).strip().lower()
        category = str(seed.get("category", "none"))
        seed_id = str(seed.get("id", f"seed-{position}"))
        notes = str(seed.get("notes", "synthetic"))
        seed_verdict = rules_assessor(text, lang) if rules_assessor else UNAVAILABLE

        candidates: list[GeneratedRow] = [
            GeneratedRow(
                id=f"{seed_id}-s",
                text=text,
                lang=lang,
                label=label,
                category=category,
                notes=notes,
                source="seed",
                seed_id=seed_id,
                generator=generator_name,
                strategy="seed",
                rule_id="seed",
            )
        ]
        for index, paraphrase in enumerate(
            paraphraser.paraphrase(
                text, lang=lang, label=label, category=category, limit=config.paraphrases_per_seed
            )
        ):
            candidates.append(
                GeneratedRow(
                    id=f"{seed_id}-p{index + 1}",
                    text=paraphrase.text,
                    lang=lang,
                    label=label,
                    category=category,
                    notes=f"{notes} [paraphrase: {paraphrase.rule_id}]",
                    source="paraphrase",
                    seed_id=seed_id,
                    generator=generator_name,
                    strategy=paraphrase.strategy,
                    rule_id=paraphrase.rule_id,
                )
            )

        annotated = [
            _annotate(
                candidate,
                assessor=rules_assessor,
                seed_text=text,
                seed_verdict=seed_verdict,
            )
            for candidate in candidates
        ]
        result.paraphrase_count += max(0, len(annotated) - 1)

        kept: list[GeneratedRow] = []
        for candidate in annotated:
            reason = _drop_reason(candidate)
            if reason is not None:
                result.dropped.append((candidate.id, reason))
                result.drop_reasons[reason] += 1
                continue
            key = _dedupe_key(candidate.text)
            if key in seen:
                result.dropped.append((candidate.id, "duplicate"))
                result.drop_reasons["duplicate"] += 1
                continue
            seen.add(key)
            kept.append(candidate)

        for candidate in kept:
            rng = seeded_rng(config.seed, "adversarial", candidate.id)
            for variant in adversarial_variants(
                candidate, count=config.adversarial_per_row, rng=rng
            ):
                variant = _annotate(
                    variant,
                    assessor=rules_assessor,
                    seed_text=candidate.text,
                    seed_verdict=_verdict_of(candidate),
                )
                reason = _drop_reason(variant)
                if reason is not None:
                    result.dropped.append((variant.id, reason))
                    result.drop_reasons[reason] += 1
                    continue
                key = _dedupe_key(variant.text)
                if key in seen:
                    result.dropped.append((variant.id, "duplicate"))
                    result.drop_reasons["duplicate"] += 1
                    continue
                seen.add(key)
                result.rows.append(variant)
                result.adversarial_count += 1
            result.rows.append(candidate)

    if len(result.rows) > config.max_rows:
        rng = seeded_rng(config.seed, "cap")
        rng.shuffle(result.rows)
        overflow = len(result.rows) - config.max_rows
        for row in result.rows[config.max_rows :]:
            result.dropped.append((row.id, "over_max_rows"))
        result.drop_reasons["over_max_rows"] += overflow
        result.rows = result.rows[: config.max_rows]
    return result


def _verdict_of(row: GeneratedRow) -> RulesVerdict:
    """Rebuild the verdict a row was annotated with, for the cue check."""
    return RulesVerdict(level=row.rules_level)


def _annotate(
    row: GeneratedRow,
    *,
    assessor: Callable[[str, str], RulesVerdict] | None,
    seed_text: str,
    seed_verdict: RulesVerdict,
) -> GeneratedRow:
    """Fill in the rules-engine fields and decide whether the cue survived."""
    verdict = UNAVAILABLE
    if assessor is not None:
        try:
            verdict = assessor(row.text, row.lang)
        except Exception:  # pragma: no cover - a broken engine must not kill a build
            verdict = UNAVAILABLE
    level = verdict.level
    # The deterministic gate may be *more lenient* than the label — that gap is
    # exactly what the model is for — but never stricter. A benign row the rules
    # engine escalates is a contradiction this pipeline cannot resolve, so it is
    # dropped rather than trained on.
    agrees = (not level) or severity(level) <= severity(row.label)
    cue = _cue_retained(
        seed_text=seed_text,
        text=row.text,
        seed_verdict=seed_verdict,
        verdict=verdict,
        label=row.label,
    )
    return GeneratedRow(
        id=row.id,
        text=row.text,
        lang=row.lang,
        label=row.label,
        category=row.category,
        notes=row.notes,
        source=row.source,
        seed_id=row.seed_id,
        generator=row.generator,
        strategy=row.strategy,
        rule_id=row.rule_id,
        transforms=row.transforms,
        rules_level=level,
        rules_agrees=agrees,
        cue_retained=cue,
    )


def _cue_retained(
    *,
    seed_text: str,
    text: str,
    seed_verdict: RulesVerdict,
    verdict: RulesVerdict,
    label: str,
) -> bool:
    """Did the rewrite keep the thing that makes the label true?

    Two directions, because a paraphrase can spoil a row either way:

    * **Risk row:** if the rules engine matched a risk category on the seed and
      matches nothing on the rewrite, the risk cue is gone and the label is now
      unsupported. Failing that, a content-token overlap floor catches a rewrite
      that replaced the whole sentence.
    * **Benign row:** if the rewrite gained risk language the seed did not have,
      the benign label is now unsupported.
    """
    seed_categories = {item for item in seed_verdict.categories if item != "none"}
    new_categories = {item for item in verdict.categories if item != "none"}
    if label in ("none", "low"):
        return not new_categories - seed_categories
    if seed_categories and not new_categories:
        return False
    return _content_overlap(seed_text, text) >= 0.5


#: Words too common to carry meaning, per language. Only used by the overlap
#: floor; an incomplete list makes the floor stricter, never looser.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "that",
        "this",
        "with",
        "have",
        "when",
        "what",
        "just",
        "like",
        "know",
        "feel",
        "feels",
        "feeling",
        "think",
        "about",
        "there",
        "their",
        "would",
        "could",
        "should",
        "been",
        "being",
        "were",
        "will",
        "your",
        "from",
        "they",
        "them",
        "then",
        "than",
        "into",
        "over",
        "very",
        "much",
        "more",
        "always",
        "never",
        "every",
        "still",
        "even",
        "also",
        "because",
        "after",
        "before",
        "again",
        "away",
        "back",
        "down",
        "some",
        "only",
        "मैं",
        "हूँ",
        "है",
        "नहीं",
        "कोई",
        "कुछ",
        "बहुत",
        "अब",
        "और",
        "पर",
        "ami",
        "hindi",
        "hai",
        "nahi",
        "kuch",
        "bahut",
        "abhi",
        "aur",
        "par",
        "আমি",
        "নাহি",
        "কিছু",
        "বহুত",
        "এবং",
        "কিন্তু",
    }
)


def _content_overlap(seed_text: str, text: str) -> float:
    """Share of the seed's content tokens that survive in the rewrite."""
    seed_tokens = _content_tokens(seed_text)
    if not seed_tokens:
        return 1.0
    new_tokens = _content_tokens(text)
    kept = sum(1 for token in seed_tokens if token in new_tokens)
    return kept / len(seed_tokens)


def _content_tokens(text: str) -> list[str]:
    return [
        token.casefold()
        for token in text.split()
        if len(token) >= 4 and token.casefold() not in _STOPWORDS
    ]


def _drop_reason(row: GeneratedRow) -> str | None:
    """Why this row may not enter the training set, or ``None`` if it may."""
    if not row.text.strip():
        return "empty_text"
    if len(row.text) > 400:
        return "too_long"
    if unicodedata.normalize("NFC", row.text) != row.text:
        return "not_nfc"
    if method_word_problems(row.text):
        return "method_word"
    if not row.rules_agrees:
        return "rules_inconsistent"
    if not row.cue_retained:
        return "cue_lost"
    return None


def _dedupe_key(text: str) -> str:
    """Case- and whitespace-insensitive identity for the de-duplication pass."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Read a JSONL file without loading it all into memory."""
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield _loads(line)


def _loads(line: str) -> dict[str, Any]:
    import json

    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    return payload
