"""Source emotion schemes → Manovia's nine-emotion taxonomy, one documented table.

Manovia speaks nine emotions (``EMOTIONS`` below, copied verbatim from
``backend/app/services/nlp/base.py`` and asserted against it by
``ml/tests/test_emotion_taxonomy.py``). Every corpus Manovia may train on speaks
something else, and a mapping between two emotion schemes is a *decision*: it
throws information away, it invents correspondences that do not exist, and it
cannot be checked by a loss curve. So it lives here, in one table, with a
rationale per row, and the tests in this repository check the properties that
can be checked mechanically:

* every source label of every supported scheme is either mapped or explicitly
  dropped (no silence: a scheme that grows a label fails the suite instead of
  quietly losing it);
* every target is one of the nine;
* the mapping is *many-to-one* but never *one-to-many* in a way that would give
  a single source label a weight above 1.0;
* **``loneliness`` is unreachable from GoEmotions** — that is a fact about the
  corpus, not a bug, and the code says so out loud (``unreachable_targets``).

Two policies are worth stating up front because they shape every number
downstream:

**Weights, not sets.** A row maps to ``{target: weight}`` with
``weight ∈ (0, 1]``. ``annoyance`` is anger-ish (0.6) but not anger;
``disappointment`` is sadness (0.8) but not quite grief. The weight becomes the
soft target for a sigmoid loss and the ``>= threshold`` decision at inference.
Where two source labels point at the same target the **maximum** wins — the same
rule the product's own ``LABEL_MAP`` uses, so a row with ``{sadness, grief}``
does not count as "twice as sad".

**``neutral`` is derived, not copied.** A GoEmotions row labelled ``neutral`` is
a row the raters could not give an emotion to. Manovia's ``neutral`` means "the
analyzer looked and found nothing", which is the *residual* of the same idea: a
row is neutral when no other target reaches ``NEUTRAL_THRESHOLD``. Copying
``neutral`` through as a ninth hard label instead would create rows labelled
both ``neutral`` and ``joy``, which is not a thing the product can act on. The
consequence is documented in the model card: a GoEmotions row whose real emotion
was ``gratitude`` at weight 0.7 becomes ``joy``, not ``neutral``, and a row of
``{confusion}`` (0.4) becomes neutral — that is the intended behaviour at the
``neutral`` boundary, and it is the one place where the weight cut is visible.

Nothing here downloads anything: the schemes are transcribed from the published
label lists, and the tests use hand-written rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "DROP_TARGET",
    "EMONOBA_DROP",
    "EMONOBA_MAP",
    "EMOTION_SOURCES",
    "GOEMOTIONS_LABELS",
    "GOEMOTIONS_MAP",
    "NEUTRAL",
    "NEUTRAL_THRESHOLD",
    "SCHEMES",
    "EmotionScheme",
    "LabelMapping",
    "mapped_targets",
    "source_label_map",
    "target_names",
    "target_weights",
    "to_target_vector",
    "unreachable_targets",
]

#: Manovia's taxonomy, in product order. Kept as a literal (rather than imported
#: from the backend) because ``ml/`` must be installable and testable on its own;
#: ``ml/tests/test_emotion_taxonomy.py`` asserts equality with the backend
#: constant when the backend is importable, so drift is a test failure, not a
#: silent divergence.
EMOTIONS: tuple[str, ...] = (
    "joy",
    "sadness",
    "anger",
    "fear",
    "anxiety",
    "shame",
    "loneliness",
    "calm",
    "neutral",
)

NEUTRAL: str = "neutral"

#: A mapped weight below this does not count as a positive target. It is the
#: line between "this source label implies the emotion" and "this source label
#: is adjacent to the emotion": ``confusion`` (0.4) and ``realization`` (0.3) do
#: not make a row neutral-proof, ``approval`` (0.5) and ``caring`` (0.5) do.
NEUTRAL_THRESHOLD: float = 0.5

#: Sorting helper for "this source label has no target at all".
DROP_TARGET: str = ""


@dataclass(frozen=True)
class LabelMapping:
    """One source label → ``(target, weight)`` pairs, and why."""

    source: str
    targets: tuple[tuple[str, float], ...]
    rationale: str

    @property
    def dropped(self) -> bool:
        """True when the source label maps to nothing (deliberately)."""

        return not self.targets


@dataclass(frozen=True)
class EmotionScheme:
    """A source label scheme and its mapping onto Manovia's taxonomy."""

    name: str
    version: str
    citation: str
    licence: str
    languages: tuple[str, ...]
    mappings: tuple[LabelMapping, ...]

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(item.source for item in self.mappings)

    @property
    def table(self) -> dict[str, tuple[tuple[str, float], ...]]:
        return {item.source: item.targets for item in self.mappings}

    def unmapped_targets(self) -> tuple[str, ...]:
        """Targets this scheme can never produce — the honest gaps."""

        return unreachable_targets(self)


def _m(source: str, *targets: tuple[str, float], rationale: str) -> LabelMapping:
    return LabelMapping(source=source, targets=targets, rationale=rationale)


#: GoEmotions (Google Research, 2020). 27 emotions + ``neutral``; up to five
#: labels per row; the "filtered" release is the one Manovia's catalogue allows
#: (Apache-2.0, no redistribution). Rationale column: what a Manovia user would
#: have meant.
GOEMOTIONS_MAP: EmotionScheme = EmotionScheme(
    name="GoEmotions",
    version="filtered (Google Research, 2021 release)",
    citation="Demszky et al. 2020, arXiv:2005.00547",
    licence="Apache-2.0",
    languages=("en",),
    mappings=(
        # --- the four labels that map straight across -----------------------
        _m("joy", ("joy", 1.0), rationale="exact match"),
        _m("sadness", ("sadness", 1.0), rationale="exact match"),
        _m("anger", ("anger", 1.0), rationale="exact match"),
        _m("fear", ("fear", 1.0), rationale="exact match"),
        # --- GoEmotions labels with a Manovia near-neighbour ----------------
        _m("nervousness", ("anxiety", 1.0), rationale="nervousness is Manovia's anxiety class"),
        _m("embarrassment", ("shame", 1.0), rationale="self-conscious exposure is shame"),
        _m(
            "remorse",
            ("shame", 0.9),
            rationale="regret about one's own act; guilt is shame-adjacent",
        ),
        _m("grief", ("sadness", 1.0), rationale="grief is sadness at full weight"),
        _m("relief", ("calm", 0.9), rationale="the only GoEmotions label near calm"),
        _m("amusement", ("joy", 1.0), rationale="mirth is joy"),
        _m("excitement", ("joy", 0.9), rationale="activated positive affect"),
        _m("love", ("joy", 0.8), rationale="warmth; Manovia has no love class"),
        _m("optimism", ("joy", 0.6), rationale="positive anticipation"),
        _m("pride", ("joy", 0.7), rationale="positive self-appraisal"),
        _m("gratitude", ("joy", 0.7), rationale="thankfulness reads positive"),
        _m("admiration", ("joy", 0.6), rationale="warm approval"),
        _m(
            "desire",
            ("joy", 0.5),
            rationale="wanting something pleasant; weakest joy link that still counts",
        ),
        _m("approval", ("joy", 0.5), rationale="mild positive; at the neutral cut"),
        _m("caring", ("joy", 0.5), rationale="warmth toward someone; at the neutral cut"),
        _m("disappointment", ("sadness", 0.8), rationale="unmet expectation, sadness-flavoured"),
        _m("disapproval", ("anger", 0.7), rationale="negative judgement of another"),
        _m(
            "disgust",
            ("anger", 0.8),
            rationale="disgust is anger's visceral sibling (same call as the backend LABEL_MAP)",
        ),
        _m("annoyance", ("anger", 0.6), rationale="mild irritation, not full anger"),
        # --- and the labels that stay behind --------------------------------
        _m(
            "confusion",
            rationale="no Manovia class; a 0.4 neutral target was rejected",
        ),
        _m("curiosity", rationale="an interest marker, not an emotion Manovia acts on"),
        _m("realization", rationale="epistemic, not affective"),
        _m("surprise", rationale="valence-free; mapping it would invent a direction"),
        _m(
            "neutral",
            rationale="derived: a row is neutral only if no other target reaches NEUTRAL_THRESHOLD",
        ),
    ),
)

#: EmoNoBa (Bangla social-media emotion, CC BY 4.0). The catalogue's condition
#: for this corpus is explicit — *map only onto the overlapping labels, dropping
#: ``love`` and ``surprise`` rather than guessing*. Four of the six labels
#: overlap Manovia's taxonomy directly; the other two are dropped here for that
#: reason and the drop is recorded in the run's data summary.
EMONOBA_DROP: tuple[str, ...] = ("love", "surprise")

EMONOBA_MAP: EmotionScheme = EmotionScheme(
    name="EmoNoBa",
    version="AACL-IJCNLP 2022 release",
    citation="Islam et al. 2022, aclanthology.org/2022.aacl-short.17",
    licence="CC BY 4.0",
    languages=("bn",),
    mappings=(
        _m("anger", ("anger", 1.0), rationale="exact match"),
        _m("fear", ("fear", 1.0), rationale="exact match"),
        _m("joy", ("joy", 1.0), rationale="exact match"),
        _m("sadness", ("sadness", 1.0), rationale="exact match"),
        _m("love", rationale="catalogue condition: dropped rather than guessed"),
        _m("surprise", rationale="catalogue condition: dropped rather than guessed"),
    ),
)

#: Every scheme this package can convert. Names are used in config/data files.
SCHEMES: dict[str, EmotionScheme] = {
    "goemotions": GOEMOTIONS_MAP,
    "emonoba": EMONOBA_MAP,
}

#: The 27 GoEmotions labels plus ``neutral``, in table order. Named so the
#: tests and reports can say "the source had 28 labels" without re-listing them.
GOEMOTIONS_LABELS: tuple[str, ...] = GOEMOTIONS_MAP.labels

#: Recognised values for a row's ``source`` field in a converted JSONL file.
EMOTION_SOURCES: tuple[str, ...] = tuple(SCHEMES)


def target_names() -> tuple[str, ...]:
    """The nine labels, in product order."""

    return EMOTIONS


def source_label_map(name: str) -> dict[str, tuple[tuple[str, float], ...]]:
    """``{source label: ((target, weight), ...)}`` for a scheme name."""

    try:
        scheme = SCHEMES[name]
    except KeyError:  # pragma: no cover - guarded by callers/tests
        raise KeyError(f"unknown emotion scheme {name!r}; known: {sorted(SCHEMES)}") from None
    return scheme.table


def mapped_targets(name: str) -> tuple[str, ...]:
    """Every target reachable from a scheme, sorted."""

    scheme = SCHEMES[name]
    reachable = {target for item in scheme.mappings for target, _ in item.targets}
    return tuple(sorted(reachable))


def unreachable_targets(scheme: EmotionScheme) -> tuple[str, ...]:
    """Targets a scheme's *labels* can never produce — the training gap, in code.

    ``neutral`` is excluded because it is derived rather than mapped for every
    scheme (a row is neutral when nothing else clears ``NEUTRAL_THRESHOLD``), so
    it is reachable from any row. What this returns is the list a model card
    should print: the emotions the corpus has no supervision for at all.
    """

    reachable = {target for item in scheme.mappings for target, _ in item.targets}
    return tuple(label for label in EMOTIONS if label != NEUTRAL and label not in reachable)


def target_weights(
    source_labels: Iterable[str],
    *,
    scheme: str,
    min_weight: float = 0.0,
) -> dict[str, float]:
    """Fold a row's source labels into ``{target: weight}`` (max per target).

    Unknown source labels are ignored: a corpus that grows a label is caught by
    the scheme test, and at run time an unrecognised row should not take the
    training job down with it. ``min_weight`` drops weak links before the
    neutral decision (the default keeps everything so the caller decides).
    """

    table = source_label_map(scheme)
    weights: dict[str, float] = {}
    for raw in source_labels:
        label = str(raw).strip().casefold()
        for target, weight in table.get(label, ()):
            if weight < min_weight:
                continue
            weights[target] = max(weights.get(target, 0.0), weight)
    return weights


def to_target_vector(
    source_labels: Iterable[str],
    *,
    scheme: str,
    labels: Sequence[str] = EMOTIONS,
    neutral_threshold: float = NEUTRAL_THRESHOLD,
) -> tuple[float, ...]:
    """The soft target vector for one row, in ``labels`` order.

    ``neutral`` is set to 1.0 when no other label reaches ``neutral_threshold``,
    and to 0.0 otherwise — the derived-neutral policy from the module docstring.
    """

    weights = target_weights(source_labels, scheme=scheme)
    positive = {label: value for label, value in weights.items() if value >= neutral_threshold}
    vector: list[float] = []
    for label in labels:
        if label == NEUTRAL:
            vector.append(0.0 if positive else 1.0)
        else:
            vector.append(round(weights.get(label, 0.0), 6))
    return tuple(vector)


def drop_report(
    rows: Sequence[Mapping[str, object]],
    *,
    scheme: str,
    label_field: str = "labels",
) -> dict[str, int]:
    """How many source rows lose which labels when converted with ``scheme``.

    Reported per run (see ``training/emotion/data.py``), because "we trained on
    43k rows" without "and 9,100 of them lost their only label" is the kind of
    number that makes a model look better-fed than it was.
    """

    counters: dict[str, int] = {}
    for row in rows:
        raw = row.get(label_field, [])
        labels = [raw] if isinstance(raw, str) else list(raw) if isinstance(raw, Iterable) else []
        kept = target_weights(labels, scheme=scheme)
        if not kept:
            counters["rows_without_a_target"] = counters.get("rows_without_a_target", 0) + 1
        table = source_label_map(scheme)
        for label in labels:
            if not table.get(str(label).strip().casefold()):
                key = f"dropped:{str(label).strip().casefold()}"
                counters[key] = counters.get(key, 0) + 1
    return dict(sorted(counters.items()))
