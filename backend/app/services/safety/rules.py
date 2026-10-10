"""The safety rules engine: text in, :class:`RiskAssessment` out.

This is the module AGENTS.md safety rule 1 depends on — it runs *before* any LLM
call, it is deterministic, and it never improvises. Design notes and the threat
model are in ``docs/safety-design.md``; this docstring covers the mechanics.

**Conservative by construction.** Every ambiguity here is resolved towards
"escalate":

* A hit is dropped only on *positive* evidence — a negation attached to it, a
  benign frame or an inanimate subject next to it, quotation marks around it.
  Suspicion is never enough.
* If a rule fires anywhere in the message, in any spelling variant, once, it
  counts. Extra occurrences only ever add evidence.
* A third-person or quoted message is *reshaped*, never silenced: the cap is
  HIGH, and IMMINENT survives only for an acute medical emergency, where
  somebody — whoever the sentence is about — needs an ambulance.
* Negated ideation is still an assessment of LOW, not NONE. "I don't want to
  die" is something a person says to a mental-health companion; the reply should
  notice it without handing over a crisis card.

**Privacy.** The engine reads text and returns metadata: a level, categories,
stable pattern ids and counters. It stores nothing, writes nothing and logs
nothing — no database, no cache, no file handle in this module. The
:class:`~app.services.safety.base.RiskAssessment` validator rejects any rationale
code that looks like prose, and ``tests/safety/test_no_raw_text.py`` asserts the
input never appears in an assessment, a response or a log line.

**No method information.** ``access_to_means`` records that a message mentioned
having something available — a risk factor that changes urgency. Neither the
patterns nor any output of this module describes how anything is done, and the
escalation copy never repeats a means word (WHO safe-messaging guidance).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from app.services.safety.base import (
    AssessmentContext,
    RiskAssessment,
    RiskCategory,
    RiskLevel,
    empty_assessment,
)
from app.services.safety.normalise import (
    DEFAULT_MAX_INPUT_CHARS,
    Clause,
    ClauseIndex,
    NormalisedMessage,
    has_first_person,
    index_clauses,
    is_negated,
    looks_figurative,
    normalise,
    split_quotes,
)
from app.services.safety.patterns import PatternSet, RulePattern, load_patterns

#: Rationale codes the engine adds itself (as opposed to pattern ids).
CODE_NEGATED: Final = "ctx.negated"
CODE_FIGURATIVE: Final = "ctx.figurative"
CODE_THIRD_PERSON: Final = "ctx.third_person"
CODE_QUOTED: Final = "ctx.quoted"
CODE_FICTION: Final = "ctx.fiction_frame"
CODE_TIMEFRAME: Final = "ctx.timeframe"
CODE_EMPTY_INPUT: Final = "input.empty"
CODE_TRUNCATED: Final = "input.truncated"


@dataclass(frozen=True)
class MessageFlags:
    """The pragmatic judgements the engine makes about one message.

    Computed once per assessment. Keeping them together is what stops the level,
    the third-person cap and the reported context from disagreeing: they all read
    the same object.
    """

    #: The speaker is in the sentence ("i", "me", "main", "ami") — after
    #: possessive-relative phrases have been blanked out.
    first_person: bool
    #: The message is about somebody else, quoted, or framed as fiction.
    third_person: bool
    #: The risky wording exists only inside quotation marks.
    quoted_only: bool
    #: A fiction/report frame is present ("in a movie", "for my novel").
    fiction_frame: bool
    #: A time marker is present ("tonight", "aaj raat").
    timeframe: bool


@dataclass(frozen=True)
class Occurrence:
    """One place where one rule matched one projection of the message.

    The clause and both filter verdicts are captured *at match time*, while the
    offsets are still meaningful for that projection. Nothing downstream has to
    reconstruct where in the text this was — and nothing here stores what the
    text said.
    """

    rule: RulePattern
    clause: Clause
    start: int
    end: int
    #: A negation directly attached to this hit cancels it.
    negated: bool
    #: Positive evidence that this hit is everyday exaggeration.
    figurative: bool


class RuleEngine:
    """Deterministic risk assessment over the curated pattern set.

    Stateless and thread-safe: build one (:func:`build_engine` caches a default)
    and call :meth:`assess` as often as needed. Nothing is mutated and nothing is
    persisted, so the same text always produces the same assessment — which is
    what makes the table-driven suite in ``tests/safety`` meaningful.
    """

    def __init__(
        self,
        patterns: PatternSet | None = None,
        *,
        max_chars: int = DEFAULT_MAX_INPUT_CHARS,
    ) -> None:
        self._patterns = patterns if patterns is not None else load_patterns()
        if max_chars < 1:
            raise ValueError("max_chars must be at least 1")
        self._max_chars = max_chars
        self._self_negating = _self_negating_rules(self._patterns)

    @property
    def patterns(self) -> PatternSet:
        """The compiled pattern set this engine runs."""
        return self._patterns

    @property
    def max_chars(self) -> int:
        """The input cap; longer messages are cut and the assessment says so."""
        return self._max_chars

    def describe(self) -> dict[str, Any]:
        """Counts and provenance — no text, no pattern values."""
        described = self._patterns.describe()
        described["max_chars"] = self._max_chars
        return described

    # ------------------------------------------------------------------ #
    # The assessment                                                      #
    # ------------------------------------------------------------------ #

    def assess(self, text: str, *, language: str | None = None) -> RiskAssessment:
        """Assess one message.

        ``language`` is an optional caller hint (a BCP-47 tag). It is recorded on
        the assessment for reporting, but never used to route: every pattern runs
        against every message, so a mis-detected language cannot hide a risk.
        """
        message = normalise(text, self._patterns.context, max_chars=self._max_chars)

        if not message.primary:
            return empty_assessment(
                rationale=CODE_TRUNCATED if message.truncated else CODE_EMPTY_INPUT
            )

        occurrences = self._match(message)
        accepted, negated_count, figurative_count = self._filter(occurrences)
        # Every pragmatic judgement is made once, here, so the level, the cap and
        # the reported context cannot disagree with each other.
        flags = self._flags(text, message, occurrences=occurrences, accepted=accepted)

        categories = tuple(
            sorted({occurrence.rule.category for occurrence in accepted}, key=lambda c: c.value)
        )
        level = self._level_for(
            accepted=accepted,
            categories=categories,
            negated_count=negated_count,
            third_person=flags.third_person,
            timeframe=flags.timeframe,
        )
        level = self._cap_for_third_person(level, categories, flags.third_person)

        rationale: list[str] = sorted({occurrence.rule.id for occurrence in accepted})
        if negated_count:
            rationale.append(CODE_NEGATED)
        if figurative_count:
            rationale.append(CODE_FIGURATIVE)
        if flags.third_person:
            rationale.append(CODE_THIRD_PERSON)
        if flags.quoted_only:
            rationale.append(CODE_QUOTED)
        if flags.fiction_frame:
            rationale.append(CODE_FICTION)
        if flags.timeframe:
            rationale.append(CODE_TIMEFRAME)
        if message.truncated:
            rationale.append(CODE_TRUNCATED)

        return RiskAssessment(
            level=level,
            matched_categories=categories,
            rationale_codes=tuple(rationale),
            context=AssessmentContext(
                first_person=flags.first_person,
                third_person=flags.third_person,
                quoted=flags.quoted_only,
                fiction_frame=flags.fiction_frame,
                timeframe=flags.timeframe,
                negated_hits=negated_count,
                figurative_hits=figurative_count,
                language=_clean_language(language),
                truncated=message.truncated,
                variants_searched=len(message.variants),
            ),
        )

    # ------------------------------------------------------------------ #
    # Matching                                                            #
    # ------------------------------------------------------------------ #

    def _occurrence(
        self, rule: RulePattern, index: ClauseIndex, start: int, end: int
    ) -> Occurrence:
        """Judge one match while its offsets are still valid for its projection."""
        context = self._patterns.context
        clause = index.clause_at(start)
        return Occurrence(
            rule=rule,
            clause=clause,
            start=start,
            end=end,
            negated=rule.negation_sensitive
            and is_negated(
                clause,
                start,
                end,
                context,
                language=rule.language,
                negates_itself=rule.id in self._self_negating,
            ),
            figurative=rule.figurative_prone
            and looks_figurative(clause, start, context, subject_evidence=rule.figurative_subject),
        )

    def _match(self, message: NormalisedMessage) -> list[Occurrence]:
        """Run every rule against every projection of the message.

        The pragmatics-bearing projections (``variants``, the clause index) are run
        first. ``squashed`` and ``collapsed`` exist purely to defeat spacing and
        letter-stretching tricks, and in them the words are welded together — a
        negation cue is not a token any more, so it cannot be found. Rather than
        let that make evasion easier than honest typing, a rule that was negated in
        the real text stays negated in the trick projection. The projections can
        add a hit; they cannot cancel one.
        """
        occurrences: list[Occurrence] = []
        rules: tuple[RulePattern, ...] = self._patterns.rules

        for variant in message.variants:
            index = message.index_for(variant)
            for rule in rules:
                for match in rule.pattern.finditer(variant):
                    occurrences.append(self._occurrence(rule, index, match.start(), match.end()))

        # What the honest projections concluded, applied to the trick projections.
        negated_rules = frozenset(
            occurrence.rule.id for occurrence in occurrences if occurrence.negated
        )
        figurative_rules = frozenset(
            occurrence.rule.id for occurrence in occurrences if occurrence.figurative
        )

        # Spacing- and letter-trick projections. Each is a different string, so
        # clause offsets from the primary are meaningless here: the projection is
        # split into its own clauses. Rules carry a purpose-built compiled pattern
        # for each projection, because the honest pattern (which expects word
        # boundaries and whitespace) cannot match welded text.
        #
        # A rule already judged negated or figurative in the honest text is skipped
        # here: in "idonotwanttobedead" the word "not" is no longer a token, so
        # negation is invisible. Without this, obfuscating a sentence would make it
        # *harder* to dismiss than typing it plainly — an evasion path no honest
        # typist should be penalised for not taking. The projections can add a hit
        # that the honest text hid; they can never cancel one.
        for projection, attribute in (
            (message.squashed, "squashed"),
            (message.collapsed, "collapsed"),
        ):
            if not projection:
                continue
            index = index_clauses(projection)
            for rule in rules:
                if rule.id in negated_rules or rule.id in figurative_rules:
                    continue
                compiled = getattr(rule, attribute)
                if compiled is None:
                    continue
                for match in compiled.finditer(projection):
                    occurrences.append(self._occurrence(rule, index, match.start(), match.end()))
        return occurrences

    def _filter(self, occurrences: list[Occurrence]) -> tuple[list[Occurrence], int, int]:
        """Drop negated hits, and figurative ones with positive benign evidence.

        Suppression is per *occurrence*, never per message: an inanimate subject
        explains away the "killing me" in one clause and says nothing about the
        rest of it. "This exam is killing me, I want to die" keeps its second
        half.
        """
        accepted: list[Occurrence] = []
        seen: set[str] = set()
        negated: set[str] = set()
        figurative: set[str] = set()

        for occurrence in occurrences:
            if occurrence.negated:
                negated.add(occurrence.rule.id)
                continue
            if occurrence.figurative:
                figurative.add(occurrence.rule.id)
                continue
            if occurrence.rule.id in seen:
                continue
            seen.add(occurrence.rule.id)
            accepted.append(occurrence)

        return accepted, len(negated), len(figurative)

    def _is_quoted_only(
        self,
        text: str,
        message: NormalisedMessage,
        occurrences: list[Occurrence],
    ) -> bool:
        """True when the risky wording exists *only* inside quotation marks.

        Re-runs the matcher over the text with the quoted spans removed. If
        nothing risky survives, the speaker was quoting somebody — a friend, a
        film, a headline — rather than describing themselves. This costs a second
        pass, so it is only paid when the message contains quotes *and* something
        worth explaining.
        """
        risky = {
            occurrence.rule.id
            for occurrence in occurrences
            if not occurrence.negated and occurrence.rule.level >= RiskLevel.MEDIUM
        }
        if not risky:
            return False
        remainder, quoted = split_quotes(text)
        if not quoted.strip():
            return False
        remainder_message = normalise(remainder, self._patterns.context, max_chars=self._max_chars)
        if remainder_message.primary == message.primary:
            return False
        matched_outside = {
            occurrence.rule.id
            for occurrence in self._match(remainder_message)
            if not occurrence.negated
        }
        return risky.isdisjoint(matched_outside)

    def _is_third_person(self, message: NormalisedMessage, accepted: list[Occurrence]) -> bool:
        """True when the message reads as being about somebody else.

        Two signals, in decreasing order of trust: a subject+verb frame from the
        data ("she said", "my friend wants", "usne kaha"), or a person marker
        *inside the clause of an accepted hit* where that clause has no
        first-person subject. The second is deliberately narrow: "I want to die,
        my mum doesn't care" is about the speaker, and merely mentioning a
        relative must not downgrade it.

        Fiction frames and quotation are handled by the caller — they are
        third-person for the cap, but reported separately so escalation can tell
        a writer asking about a character from somebody worried about a friend.
        """
        context = self._patterns.context
        if any(pattern.search(message.primary) for pattern in context.third_person_patterns):
            return True

        clauses = {message.index.clause_at(occurrence.start).text for occurrence in accepted}
        for clause_text in clauses:
            if has_first_person(clause_text, context):
                continue
            if context.third_person_marker_pattern.search(clause_text):
                return True
        return False

    # ------------------------------------------------------------------ #
    # Levels                                                              #
    # ------------------------------------------------------------------ #

    def _flags(
        self,
        text: str,
        message: NormalisedMessage,
        *,
        occurrences: list[Occurrence],
        accepted: list[Occurrence],
    ) -> MessageFlags:
        """Every pragmatic judgement about this message, made exactly once."""
        context = self._patterns.context
        primary = message.primary
        fiction_frame = context.fiction_frames_pattern.search(primary) is not None
        quoted_only = self._is_quoted_only(text, message, occurrences)

        return MessageFlags(
            first_person=has_first_person(primary, context),
            third_person=(quoted_only or fiction_frame or self._is_third_person(message, accepted)),
            quoted_only=quoted_only,
            fiction_frame=fiction_frame,
            timeframe=context.timeframe_pattern.search(primary) is not None,
        )

    def _level_for(
        self,
        *,
        accepted: list[Occurrence],
        categories: tuple[RiskCategory, ...],
        negated_count: int,
        third_person: bool,
        timeframe: bool,
    ) -> RiskLevel:
        """Turn accepted hits into a level, before the third-person cap."""
        if not accepted:
            # Negated ideation is not nothing: it is a person telling a
            # mental-health companion about death. LOW earns a warm check-in, not
            # a crisis card.
            return RiskLevel.LOW if negated_count else RiskLevel.NONE

        # The pattern data carries the level, including the rules that say an
        # overdose has already been taken. The engine does not second-guess them
        # — it only raises the ceiling when several signals line up.
        level = max(occurrence.rule.level for occurrence in accepted)

        # A plan plus a time, or a plan plus access, is what separates "I have
        # been thinking about this" from "this is happening".
        if (
            RiskCategory.INTENT_PLAN in categories
            and level >= RiskLevel.HIGH
            and not third_person
            and (timeframe or RiskCategory.ACCESS_TO_MEANS in categories)
        ):
            return RiskLevel.IMMINENT
        return level

    def _cap_for_third_person(
        self, level: RiskLevel, categories: tuple[RiskCategory, ...], third_person: bool
    ) -> RiskLevel:
        """Cap a message about somebody else at HIGH — except a medical emergency.

        "My friend says he has a plan for tonight" is a crisis, but it is not the
        *speaker's* imminent attempt, and the reply that helps is "get them to a
        person, or call emergency services". An overdose that has already happened
        keeps IMMINENT: an ambulance is needed regardless of who the sentence is
        about.
        """
        if not third_person or level != RiskLevel.IMMINENT:
            return level
        if RiskCategory.ACUTE_MEDICAL in categories:
            return RiskLevel.IMMINENT
        return RiskLevel.HIGH


def _self_negating_rules(patterns: PatternSet) -> frozenset[str]:
    """Ids of rules whose own pattern requires a negation cue.

    Some Indic rules are written *as* the negated phrase, because that phrase is
    itself the risk: Hindi "jeena nahi chahta" ("I do not want to live") and
    Bengali "bachte chai na" describe ideation, not a refusal of it. Reading the
    cue inside such a span as a cancellation would invert the rule's meaning, so
    those rules are exempt from the inside-the-hit negation check.

    Read from the ``negation_baked`` flag the data declares, never inferred from
    the pattern source. Inferring it cannot work: a pattern may carry an
    *optional* cue — ``marna (nahi)? chahta`` — where the cue genuinely does
    cancel, and both forms look identical as text.
    """
    return frozenset(rule.id for rule in patterns.rules if rule.negation_baked)


def _clean_language(language: str | None) -> str | None:
    """Normalise an optional caller language hint (or drop it)."""
    if not language:
        return None
    cleaned = language.strip().lower()
    return cleaned or None


_ENGINE: RuleEngine | None = None


def build_engine(patterns: PatternSet | None = None) -> RuleEngine:
    """Return the shared engine, or a fresh one over ``patterns`` for tests.

    The shared instance is built on first use and never rebuilt: patterns are
    shipped content, so there is nothing to reload at runtime.
    """
    global _ENGINE
    if patterns is not None:
        return RuleEngine(patterns)
    if _ENGINE is None:
        _ENGINE = RuleEngine()
    return _ENGINE


__all__ = [
    "CODE_EMPTY_INPUT",
    "CODE_FICTION",
    "CODE_FIGURATIVE",
    "CODE_NEGATED",
    "CODE_QUOTED",
    "CODE_THIRD_PERSON",
    "CODE_TIMEFRAME",
    "CODE_TRUNCATED",
    "MessageFlags",
    "Occurrence",
    "RuleEngine",
    "build_engine",
]
