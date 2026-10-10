"""Map a risk level onto a response policy and a pre-written message.

This is AGENTS.md safety rule 2: at high risk the reply is deterministic. The
rules engine decides *how serious* a message is; this module decides *what the
product does about it* — and the answer is a lookup, not a generation. Nothing
here reads the user's text (it never sees it), so nothing here can be talked into
improvising, and the exact words somebody in crisis reads are reviewable in
``app/content/i18n/*.json``.

Three properties worth stating, because they are the point of the module:

* **HIGH and IMMINENT block the LLM.** ``allow_llm`` is False, so the chat path
  has no reply of its own to produce. A model that is down, slow, hallucinating
  or jailbroken cannot change the response to a suicide disclosure.
* **The message never contains method information.** It acknowledges the person,
  points at a helpline and a trusted person, says what to do in immediate danger,
  and lists concrete method-free safety steps (WHO and AFSP safe-messaging
  guidance). ``tests/safety/test_safe_messaging.py`` asserts that against every
  template in every locale.
* **The reader matters as much as the level.** "My friend says she wants to die"
  is a crisis, but the person holding the phone is a supporter, and telling them
  to "move away from anything you could use to hurt yourself" is useless at best.
  Those messages get ``crisis.about_someone_else`` instead. The level, the
  helplines and the urgency are unchanged — only the addressee is right.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.content.crisis import CrisisResource, HelplineContent, load_helplines
from app.content.i18n import (
    LocalisedContent,
    RenderedTemplate,
    load_all_localisations,
    resolve_locale,
)
from app.services.safety.base import RiskAssessment, RiskLevel

#: Substituted only if a region somehow has no emergency number of its own. The
#: helpline loader requires every declared region to carry one, so this is a last
#: resort that still gives a person in danger something dialable from a mobile.
INTERNATIONAL_EMERGENCY_NUMBER: Final = "112"

#: Template ids, spelled out so a rename is one edit and one test failure.
TEMPLATE_CRISIS_HIGH: Final = "crisis.high"
TEMPLATE_CRISIS_IMMINENT: Final = "crisis.imminent"
TEMPLATE_ABOUT_SOMEONE_ELSE: Final = "crisis.about_someone_else"
TEMPLATE_CHECK_IN_MEDIUM: Final = "check_in.medium"
TEMPLATE_CHECK_IN_LOW: Final = "check_in.low"


class EscalationAction(StrEnum):
    """What the rest of the product may do with an assessment.

    A closed vocabulary on purpose: the chat service, the API and the audit log
    all switch on these strings, so a new behaviour is a visible change here and
    a test, not an ``if`` buried in a route handler.
    """

    #: Answer normally.
    NORMAL_REPLY = "normal_reply"
    #: Ask how they are doing rather than moving on.
    SUPPORTIVE_CHECK_IN = "supportive_check_in"
    #: Offer a helpline as an option, without implying they must use one.
    ENCOURAGE_HELPLINE = "encourage_helpline"
    #: Show the pre-written crisis message and nothing else.
    SHOW_CRISIS_MESSAGE = "show_crisis_message"
    #: The message carries a "do this now" instruction with the emergency number.
    SHOW_EMERGENCY_INSTRUCTION = "show_emergency_instruction"
    #: Attach the region's helpline list to the response.
    SHOW_HELPLINES = "show_helplines"
    #: Do not call the LLM for this turn.
    BLOCK_LLM = "block_llm"
    #: Write a metadata-only ``safety_events`` row.
    RECORD_SAFETY_EVENT = "record_safety_event"


class EscalationPolicy(BaseModel):
    """The static contract for one risk level."""

    model_config = ConfigDict(frozen=True)

    level: RiskLevel
    #: Template shown to the person the message is about.
    template_id: str | None = None
    #: Template shown when the message is about somebody else.
    about_others_template_id: str | None = None
    actions: tuple[EscalationAction, ...] = ()
    #: False at HIGH and IMMINENT: no model call happens for this turn.
    allow_llm: bool = True
    #: True when the reply is the template and nothing else.
    deterministic_reply: bool = False
    show_helplines: bool = False
    show_emergency_instruction: bool = False
    #: Whether this level is worth an audit row (metadata only).
    record_event: bool = False

    def template_for(self, *, about_someone_else: bool) -> str | None:
        """Pick the template for the person actually reading it."""
        if about_someone_else and self.about_others_template_id:
            return self.about_others_template_id
        return self.template_id


#: The whole policy table. Five rows, no branches, no per-user variation.
_POLICIES: Final[Mapping[RiskLevel, EscalationPolicy]] = {
    RiskLevel.NONE: EscalationPolicy(
        level=RiskLevel.NONE,
        actions=(EscalationAction.NORMAL_REPLY,),
    ),
    RiskLevel.LOW: EscalationPolicy(
        level=RiskLevel.LOW,
        template_id=TEMPLATE_CHECK_IN_LOW,
        actions=(EscalationAction.NORMAL_REPLY, EscalationAction.SUPPORTIVE_CHECK_IN),
    ),
    RiskLevel.MEDIUM: EscalationPolicy(
        level=RiskLevel.MEDIUM,
        template_id=TEMPLATE_CHECK_IN_MEDIUM,
        actions=(
            EscalationAction.NORMAL_REPLY,
            EscalationAction.SUPPORTIVE_CHECK_IN,
            EscalationAction.ENCOURAGE_HELPLINE,
            EscalationAction.SHOW_HELPLINES,
            EscalationAction.RECORD_SAFETY_EVENT,
        ),
        show_helplines=True,
        record_event=True,
    ),
    RiskLevel.HIGH: EscalationPolicy(
        level=RiskLevel.HIGH,
        template_id=TEMPLATE_CRISIS_HIGH,
        about_others_template_id=TEMPLATE_ABOUT_SOMEONE_ELSE,
        actions=(
            EscalationAction.BLOCK_LLM,
            EscalationAction.SHOW_CRISIS_MESSAGE,
            EscalationAction.SHOW_EMERGENCY_INSTRUCTION,
            EscalationAction.SHOW_HELPLINES,
            EscalationAction.RECORD_SAFETY_EVENT,
        ),
        allow_llm=False,
        deterministic_reply=True,
        show_helplines=True,
        show_emergency_instruction=True,
        record_event=True,
    ),
    RiskLevel.IMMINENT: EscalationPolicy(
        level=RiskLevel.IMMINENT,
        template_id=TEMPLATE_CRISIS_IMMINENT,
        about_others_template_id=TEMPLATE_ABOUT_SOMEONE_ELSE,
        actions=(
            EscalationAction.BLOCK_LLM,
            EscalationAction.SHOW_CRISIS_MESSAGE,
            EscalationAction.SHOW_EMERGENCY_INSTRUCTION,
            EscalationAction.SHOW_HELPLINES,
            EscalationAction.RECORD_SAFETY_EVENT,
        ),
        allow_llm=False,
        deterministic_reply=True,
        show_helplines=True,
        show_emergency_instruction=True,
        record_event=True,
    ),
}


class EscalationPlan(BaseModel):
    """Everything a caller needs to respond, with no trace of the input text.

    This is what the chat service and ``POST /api/v1/crisis/assess`` act on. It
    carries the assessment (levels, categories, pattern ids), the policy, the
    rendered message and the region's helplines — all of it safe to log, because
    none of it is what the person wrote.
    """

    model_config = ConfigDict(frozen=True)

    assessment: RiskAssessment
    policy: EscalationPolicy
    #: Rendered, placeholder-free, ready to display. ``None`` at NONE.
    message: RenderedTemplate | None = None
    #: Region helplines, in display order. Empty when the policy hides them.
    resources: tuple[CrisisResource, ...] = Field(default=())
    emergency: CrisisResource | None = None
    emergency_number: str | None = None
    locale: str = "en"
    locale_fallback_used: bool = False
    region: str = "DEFAULT"
    region_fallback_used: bool = False
    #: True when the reply is addressed to a supporter rather than to the person
    #: in crisis.
    about_someone_else: bool = False

    @property
    def level(self) -> RiskLevel:
        """The level this plan responds to."""
        return self.assessment.level

    @property
    def level_code(self) -> str:
        """The wire form of the level: ``none``…``imminent``."""
        return self.assessment.level.label

    @property
    def stored_level_code(self) -> str:
        """The four-tier database form (``high`` and ``imminent`` → ``crisis``)."""
        return self.assessment.to_stored_level().name.lower()

    @property
    def actions(self) -> tuple[EscalationAction, ...]:
        """Convenience pass-through to the policy."""
        return self.policy.actions

    @property
    def allow_llm(self) -> bool:
        """False when the reply must be the template and nothing else."""
        return self.policy.allow_llm

    @property
    def record_event(self) -> bool:
        """Whether the caller should write a metadata-only ``safety_events`` row."""
        return self.policy.record_event


class Escalator:
    """Level + region + locale → :class:`EscalationPlan`.

    Stateless and thread-safe. Both content sources are injected so a test (or a
    future non-JSON source) can swap them; with no arguments the shipped files are
    used.
    """

    def __init__(
        self,
        helplines: HelplineContent | None = None,
        localisations: Mapping[str, LocalisedContent] | None = None,
    ) -> None:
        self._helplines = helplines if helplines is not None else load_helplines()
        self._localisations = (
            dict(localisations) if localisations is not None else dict(load_all_localisations())
        )

    @property
    def helplines(self) -> HelplineContent:
        """The helpline set this escalator draws on."""
        return self._helplines

    @property
    def locales(self) -> tuple[str, ...]:
        """Locales this escalator can render."""
        return tuple(sorted(self._localisations))

    def policy_for(self, level: RiskLevel) -> EscalationPolicy:
        """The static policy for a level."""
        return _POLICIES[level]

    def plan(
        self,
        assessment: RiskAssessment,
        *,
        region: str | None = None,
        locale: str | None = None,
    ) -> EscalationPlan:
        """Build the response plan for one assessment.

        ``region`` and ``locale`` are hints, both resolved with a fallback: an
        unknown region is served ``DEFAULT`` and an unknown locale English. A
        person in trouble who gets the wrong country code still gets a number.
        """
        policy = self.policy_for(assessment.level)
        resolved_locale, locale_fallback = resolve_locale(locale)
        content = self._localisations[resolved_locale]
        resolved_region, region_fallback = self._helplines.resolve_region(region)

        emergency = self._helplines.emergency_for(resolved_region)
        emergency_number = (
            emergency.number if emergency and emergency.number else INTERNATIONAL_EMERGENCY_NUMBER
        )

        about_someone_else = self._about_someone_else(assessment)
        template_id = policy.template_for(about_someone_else=about_someone_else)

        message: RenderedTemplate | None = None
        if template_id is not None:
            values = {name: emergency_number for name in sorted(content.placeholders(template_id))}
            message = content.render(template_id, values)

        resources = (
            tuple(self._helplines.resources_for(resolved_region)) if policy.show_helplines else ()
        )

        return EscalationPlan(
            assessment=assessment,
            policy=policy,
            message=message,
            resources=resources,
            emergency=emergency,
            emergency_number=emergency_number if emergency else None,
            locale=resolved_locale,
            locale_fallback_used=locale_fallback,
            region=resolved_region,
            region_fallback_used=region_fallback,
            about_someone_else=about_someone_else,
        )

    def _about_someone_else(self, assessment: RiskAssessment) -> bool:
        """True when the crisis copy should address a supporter, not the person.

        Third person alone is not enough: "my husband threatens to kill me" is
        about somebody else *and* about the speaker, and the speaker is the one
        who needs the crisis card. So the supporter template is used only when the
        speaker is absent from the sentence, or when the wording was quoted or
        framed as fiction — the two cases where the risky words are plainly not
        the reader's own.
        """
        context = assessment.context
        if not context.third_person:
            return False
        return context.fiction_frame or context.quoted or not context.first_person


_ESCALATOR: Escalator | None = None


def build_escalator(
    helplines: HelplineContent | None = None,
    localisations: Mapping[str, LocalisedContent] | None = None,
) -> Escalator:
    """Return the shared escalator, or a fresh one when content is injected."""
    global _ESCALATOR
    if helplines is not None or localisations is not None:
        return Escalator(helplines, localisations=localisations)
    if _ESCALATOR is None:
        _ESCALATOR = Escalator()
    return _ESCALATOR


__all__ = [
    "INTERNATIONAL_EMERGENCY_NUMBER",
    "TEMPLATE_ABOUT_SOMEONE_ELSE",
    "TEMPLATE_CHECK_IN_LOW",
    "TEMPLATE_CHECK_IN_MEDIUM",
    "TEMPLATE_CRISIS_HIGH",
    "TEMPLATE_CRISIS_IMMINENT",
    "EscalationAction",
    "EscalationPlan",
    "EscalationPolicy",
    "Escalator",
    "build_escalator",
]
