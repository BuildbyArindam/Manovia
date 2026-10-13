"""Where paraphrases come from: an interface, an offline Fake, and the real LLM.

The brief asks for "template + paraphrase generation using the LLM provider
(through the Fake in tests)". That sentence has two halves and this module is
both:

* :class:`FakeParaphraser` — deterministic, offline, no network, no model. It is
  what the test suite, the smoke run and CI use, and it is the *default* so that
  a data build can never silently spend money or leak a synthetic crisis row to
  a vendor. It rewrites text with hand-written, cue-preserving substitutions and
  register wrappers (below), which is why its output is boring — boring is the
  point: a paraphrase that changes the meaning is a label error.
* :class:`BackendLLMParaphraser` — the real provider, reached through the
  backend's own LLM chain (``app.services.llm``), imported lazily so ``ml/``
  still installs without the backend checkout. It is only constructed by an
  explicit ``--provider llm`` in a documented human-run step, and every reply it
  returns goes through the same validation the Fake's output goes through.

Both return :class:`Paraphrase` objects that carry the strategy used, because a
row whose provenance cannot be read is a row nobody can review (AGENTS.md ML
ground rule 5: every run is reproducible).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

__all__ = [
    "BN_SYNONYMS",
    "BN_WRAPPERS",
    "EN_SYNONYMS",
    "EN_WRAPPERS",
    "HI_SYNONYMS",
    "HI_WRAPPERS",
    "LATN_SYNONYMS",
    "LATN_WRAPPERS",
    "FakeParaphraser",
    "Paraphrase",
    "Paraphraser",
    "apply_synonyms",
    "apply_wrapper",
    "reorder_clauses",
]


@dataclass(frozen=True)
class Paraphrase:
    """One rewritten sentence plus the strategy that produced it."""

    text: str
    #: Which deterministic strategy fired: ``synonym``, ``wrapper``, ``reorder``
    #: or ``llm``. Recorded in the row's provenance.
    strategy: str
    #: The id of the rule (synonym pair / wrapper / template) that fired.
    rule_id: str


class Paraphraser(Protocol):
    """The interface the augmentation driver codes against."""

    #: Short name recorded in provenance: ``fake-v1``, ``backend-llm``, ...
    name: str

    def paraphrase(
        self, text: str, *, lang: str, label: str, category: str, limit: int = 4
    ) -> list[Paraphrase]:
        """Up to ``limit`` rewrites of ``text``. Never raises; may return fewer."""
        ...


# --------------------------------------------------------------------------- #
# English                                                                     #
# --------------------------------------------------------------------------- #

#: Phrase-level swaps that keep the risk cue (or the benign content) intact.
#: Contractions expand and vice versa; near-synonyms swap. Nothing here maps a
#: benign phrase onto a crisis phrase, which is what keeps a paraphrase from
#: silently changing the label.
EN_SYNONYMS: Final[tuple[tuple[str, str, str], ...]] = (
    ("en.syn.cant", r"\bcan't\b", "cannot"),
    ("en.syn.dont", r"\bdon't\b", "do not"),
    ("en.syn.wont", r"\bwon't\b", "will not"),
    ("en.syn.im", r"\bI'm\b", "I am"),
    ("en.syn.ive", r"\bI've\b", "I have"),
    ("en.syn.its", r"\bit's\b", "it is"),
    ("en.syn.scared", r"\bscared\b", "afraid"),
    ("en.syn.hopeless", r"\bhopeless\b", "without hope"),
    ("en.syn.kill_myself", r"\bkill myself\b", "end my life"),
    ("en.syn.want_to_die", r"\bwant to die\b", "wish I were dead"),
    ("en.syn.hurt_myself", r"\bhurt myself\b", "harm myself"),
    ("en.sym.no_way_out", r"\bno way out\b", "no way forward"),
    ("en.syn.every_day", r"\bevery day\b", "each day"),
    ("en.syn.lately", r"\blately\b", "these days"),
    ("en.syn.do_not_know", r"\bdon't know\b", "have no idea"),
)

#: Register wrappers: a prefix and/or suffix that leaves the cue untouched.
EN_WRAPPERS: Final[tuple[tuple[str, str, str], ...]] = (
    ("en.wrap.honestly", "honestly, ", ""),
    ("en.wrap.lately", "lately ", ""),
    ("en.wrap.these_days", "", " these days"),
    ("en.wrap.not_told", "I have not told anyone, but ", ""),
    ("en.wrap.no_idea", "I do not know what to do, ", ""),
    ("en.wrap.sorry", "sorry, ", ""),
    ("en.wrap.again", "", " again"),
)

#: Romanised Hindi. The swaps are spelling and register variants (``hain`` ->
#: ``hai``) plus a few near-synonyms; the wrappers carry the variation.
LATN_SYNONYMS: Final[tuple[tuple[str, str, str], ...]] = (
    ("hiLatn.syn.hain", r"\bhain\b", "hai"),
    ("hiLatn.syn.nahi", r"\bnahi\b", "nahin"),
    ("hiLatn.syn.hoon", r"\bhoon\b", "hun"),
    ("hiLatn.syn.bahut", r"\bbahut\b", "bohut"),
    ("hiLatn.syn.kya", r"\bkya\b", "kia"),
    ("hiLatn.syn.ghar", r"\bghar\b", "gahr"),
    ("hiLatn.syn.abhi", r"\babhi\b", "ab"),
    ("hiLatn.syn.tang", r"\btang\b", "tang aa chuka"),
    ("hiLatn.syn.dard", r"\bdard\b", "takleef"),
    ("hiLatn.syn.darr", r"\bdarr\b", "dar"),
)

LATN_WRAPPERS: Final[tuple[tuple[str, str, str], ...]] = (
    ("hiLatn.wrap.sach", "sach batau, ", ""),
    ("hiLatn.wrap.ab", "ab ", ""),
    ("hiLatn.wrap.dino_se", "", " kuch dino se"),
    ("hiLatn.wrap.kisi_ko", "kisi ko nahi bataya, par ", ""),
    ("hiLatn.wrap.samajh", "samajh nahi aa raha kya karu, ", ""),
    ("hiLatn.wrap.phir", "", " phir se"),
)

#: Devanagari Hindi.
HI_SYNONYMS: Final[tuple[tuple[str, str, str], ...]] = (
    ("hi.syn.nahi", r"\bनहीं\b", "नहि"),
    ("hi.syn.hai", r"\bहै\b", "हैं"),
    ("hi.syn.bahut", r"\bबहुत\b", "खूब"),
    ("hi.syn.dard", r"\bदर्द\b", "तकलीफ"),
    ("hi.syn.darr", r"\bडर\b", "भय"),
    ("hi.syn.tang", r"\bतंग\b", "बेचैन"),
    ("hi.syn.ab", r"\bअब\b", "अब तो"),
    ("hi.syn.roz", r"\bरोज़\b", "हर दिन"),
)

HI_WRAPPERS: Final[tuple[tuple[str, str, str], ...]] = (
    ("hi.wrap.sach", "सच कहूँ, ", ""),
    ("hi.wrap.ab", "अब ", ""),
    ("hi.wrap.dino_se", "", " कुछ दिनों से"),
    ("hi.wrap.kisi_ko", "किसी को नहीं बताया, पर ", ""),
    ("hi.wrap.samajh", "समझ नहीं आ रहा क्या करूँ, ", ""),
    ("hi.wrap.phir", "", " फिर से"),
)

#: Bengali.
BN_SYNONYMS: Final[tuple[tuple[str, str, str], ...]] = (
    ("bn.syn.khub", r"\bখুব\b", "বেশ"),
    ("bn.syn.bhai", r"\bভয়\b", "ত্রাস"),
    ("bn.syn.dukho", r"\bদুঃখ\b", "কষ্ট"),
    ("bn.syn.ekhon", r"\bএখন\b", "এক্ষুনি"),
    ("bn.syn.proti", r"\bপ্রতি\b", "প্রতিদিন"),
    ("bn.syn.valo", r"\bভালো\b", "ভাল"),
)

BN_WRAPPERS: Final[tuple[tuple[str, str, str], ...]] = (
    ("bn.wrap.sotto", "সত্যি বলি, ", ""),
    ("bn.wrap.ekhon", "এখন ", ""),
    ("bn.wrap.din", "", " কয়েকদিন ধরে"),
    ("bn.wrap.keu", "কাউকে বলিনি, কিন্তু ", ""),
    ("bn.wrap.bujhi", "বুঝতে পারছি না কী করব, ", ""),
    ("bn.wrap.abr", "", " আবার"),
)

_SYNONYMS: Final[dict[str, tuple[tuple[str, str, str], ...]]] = {
    "en": EN_SYNONYMS,
    "hi": HI_SYNONYMS,
    "hi-Latn": LATN_SYNONYMS,
    "bn": BN_SYNONYMS,
}

_WRAPPERS: Final[dict[str, tuple[tuple[str, str, str], ...]]] = {
    "en": EN_WRAPPERS,
    "hi": HI_WRAPPERS,
    "hi-Latn": LATN_WRAPPERS,
    "bn": BN_WRAPPERS,
}

#: Clause-joining words used by :func:`reorder_clauses` per language.
_JOINERS: Final[dict[str, tuple[str, ...]]] = {
    "en": (", but ", ", and ", ", so ", "; "),
    "hi": (", पर ", ", और ", "; "),
    "hi-Latn": (", par ", ", aur ", "; "),
    "bn": (", কিন্তু ", ", এবং ", "; "),
}


def apply_synonyms(
    text: str, lang: str, *, rule_ids: Sequence[str] | None = None
) -> list[Paraphrase]:
    """Every single-rule synonym swap that applies to ``text``.

    Returns one :class:`Paraphrase` per matching rule, so a caller can take the
    first ``limit`` and record exactly which rule produced each row.
    """
    out: list[Paraphrase] = []
    for rule_id, pattern, replacement in _SYNONYMS.get(lang, ()):
        if rule_ids is not None and rule_id not in rule_ids:
            continue
        new_text, count = re.subn(pattern, replacement, text, count=1)
        if count and new_text != text:
            out.append(Paraphrase(text=new_text, strategy="synonym", rule_id=rule_id))
    return out


def apply_wrapper(
    text: str, lang: str, *, rule_ids: Sequence[str] | None = None
) -> list[Paraphrase]:
    """Every register wrapper that fits around ``text``."""
    out: list[Paraphrase] = []
    for rule_id, prefix, suffix in _WRAPPERS.get(lang, ()):
        if rule_ids is not None and rule_id not in rule_ids:
            continue
        if prefix and text[: len(prefix)].casefold() == prefix.casefold():
            continue
        new_text = f"{prefix}{text}{suffix}"
        if new_text != text:
            out.append(Paraphrase(text=new_text, strategy="wrapper", rule_id=rule_id))
    return out


def reorder_clauses(text: str, lang: str) -> list[Paraphrase]:
    """Swap the two halves of a joined sentence ("A, but B" -> "B, but A")."""
    for joiner in _JOINERS.get(lang, ()):
        if joiner in text:
            left, _, right = text.partition(joiner)
            left, right = left.strip(), right.strip()
            if not left or not right:
                continue
            tail = joiner.strip().lstrip(",;").strip()
            separator = f", {tail} " if tail else ", "
            return [
                Paraphrase(
                    text=f"{right}{separator}{left}", strategy="reorder", rule_id=f"{lang}.reorder"
                )
            ]
    return []


class FakeParaphraser:
    """The offline, deterministic paraphraser: no network, no model, no cost.

    It applies the hand-written rules above in a fixed order and returns up to
    ``limit`` distinct rewrites. Two properties the tests pin:

    * **Determinism.** Same text, same language, same limit -> same list, on any
      machine, because nothing here consults a clock, a hash seed or a model.
    * **Cue preservation.** Every rule either substitutes a phrase for a
      near-synonym of the same register or wraps the sentence around the
      original, so the risk cue (or the benign content) survives by
      construction. The augmentation driver still *checks* this
      (:mod:`manovia_ml.training.safety.augment`) because "by construction" is
      a claim, not a proof.
    """

    name: str = "fake-v1"

    def paraphrase(
        self, text: str, *, lang: str, label: str, category: str, limit: int = 4
    ) -> list[Paraphrase]:
        # The interface carries `label` and `category` so the LLM provider can
        # tell the model what it must preserve; the deterministic Fake does not
        # need them, because every rule it applies is cue-preserving by
        # construction (checked downstream, not assumed).
        del label, category
        if limit <= 0 or not text.strip():
            return []
        seen = {text}
        out: list[Paraphrase] = []
        # Order matters for reproducibility: wrappers first (they never touch
        # the cue), then one synonym swap each, then the clause reorder.
        candidates = [
            *apply_wrapper(text, lang),
            *apply_synonyms(text, lang),
            *reorder_clauses(text, lang),
        ]
        for candidate in candidates:
            if len(out) >= limit:
                break
            if candidate.text in seen:
                continue
            seen.add(candidate.text)
            out.append(candidate)
        return out


class BackendLLMParaphraser:
    """The real provider, reached through the backend's LLM chain.

    Constructed only by an explicit ``--provider llm``. The import of
    ``app.services.llm`` happens inside :meth:`paraphrase` so that ``ml/``
    installs and imports without the backend checkout, and so the offline suite
    never touches it.

    The prompt is deliberately narrow — rewrite, keep the meaning, keep the
    language, output one sentence per line — and the reply is parsed defensively:
    anything that is not a plausible one-line rewrite is dropped rather than
    repaired, because a malformed line in a training file is a silent label
    error. Every returned candidate is validated by the same rules the Fake's
    output goes through (cue retention, length, method-word screen).
    """

    name: str = "backend-llm"

    def __init__(self, *, max_chars: int = 400) -> None:
        self._max_chars = max_chars

    def paraphrase(
        self, text: str, *, lang: str, label: str, category: str, limit: int = 4
    ) -> list[Paraphrase]:
        # The label and the category go into the prompt: a paraphraser that does
        # not know the row is labelled IMMINENT has no way to know that "maybe
        # later" is not an acceptable rewrite.
        import asyncio

        try:
            from app.services.llm.chain import build_default_chain
        except Exception:  # pragma: no cover - backend not importable
            return []

        prompt = _PARAPHRASE_PROMPT.format(
            text=text, lang=lang, limit=limit, label=label, category=category
        )
        try:
            chain = build_default_chain()
            result = asyncio.run(chain.complete(prompt, system=None))
        except Exception:  # pragma: no cover - provider failure is not fatal
            return []
        lines = [line.strip(" -\t") for line in (result.text or "").splitlines()]
        out: list[Paraphrase] = []
        for line in lines:
            if len(out) >= limit:
                break
            cleaned = line.strip()
            if not cleaned or cleaned == text or len(cleaned) > self._max_chars:
                continue
            if cleaned.startswith(('"', "'")) and cleaned.endswith(('"', "'")):
                cleaned = cleaned[1:-1].strip()
            if not cleaned:
                continue
            out.append(Paraphrase(text=cleaned, strategy="llm", rule_id=f"llm.{lang}"))
        return out


_PARAPHRASE_PROMPT: Final[str] = (
    "Rewrite the following {lang} sentence {limit} different ways. Keep the same "
    "meaning, the same language and the same length. Keep it to one sentence per "
    "line and output nothing else. The sentence is labelled '{label}' "
    "({category}); every rewrite must stay a correct example of that label.\n\n"
    "Sentence: {text}\n\nRewrites:"
)


def build_paraphraser(name: str) -> Paraphraser:
    """Factory used by the CLI: ``fake`` (default, offline) or ``llm``."""
    if name in ("", "fake", "fake-v1"):
        return FakeParaphraser()
    if name in ("llm", "backend-llm"):
        return BackendLLMParaphraser()
    raise ValueError(f"unknown paraphraser {name!r}; expected 'fake' or 'llm'")
