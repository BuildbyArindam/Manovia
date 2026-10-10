"""Escalation: the mapping from a risk level to a response, and its guarantees.

The rules engine decides *how worried to be*. This module decides *what to do
about it*, and the tests here hold it to the promises the product makes:

* HIGH and IMMINENT are deterministic — a pre-written message, no model call.
* Every crisis level shows the region's helplines and an emergency number.
* The copy never describes a method, in any of the three languages.
* An unknown region or locale still produces a usable answer, with the fallback
  recorded rather than silently applied.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import pytest

from app.content.crisis import FALLBACK_REGION
from app.content.i18n import CrisisTemplate, LocalisedContent, load_all_localisations
from app.services.safety.base import RiskAssessment, RiskCategory, RiskLevel
from app.services.safety.escalation import (
    INTERNATIONAL_EMERGENCY_NUMBER,
    TEMPLATE_ABOUT_SOMEONE_ELSE,
    TEMPLATE_CHECK_IN_LOW,
    TEMPLATE_CHECK_IN_MEDIUM,
    TEMPLATE_CRISIS_HIGH,
    TEMPLATE_CRISIS_IMMINENT,
    EscalationAction,
    Escalator,
    build_escalator,
)

#: Words that would mean the copy is describing or hinting at a method.
#: Matched on word boundaries: a substring screen flags "can *chang*e" and
#: "be*gun*", which is how a first attempt at this list produced two false
#: positives before it caught anything real.
METHOD_WORDS: tuple[str, ...] = (
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
)

CRISIS_LEVELS = (RiskLevel.HIGH, RiskLevel.IMMINENT)


def _assessment(
    level: RiskLevel,
    *,
    categories: tuple[RiskCategory, ...] = (),
    third_person: bool = False,
    first_person: bool = True,
    quoted: bool = False,
    fiction_frame: bool = False,
) -> RiskAssessment:
    """An assessment built by hand, so a test does not depend on the patterns."""
    from app.services.safety.base import AssessmentContext

    return RiskAssessment(
        level=level,
        matched_categories=categories,
        context=AssessmentContext(
            third_person=third_person,
            first_person=first_person,
            quoted=quoted,
            fiction_frame=fiction_frame,
        ),
    )


@pytest.fixture(scope="module")
def all_localisations() -> Mapping[str, LocalisedContent]:
    """All three shipped locale files, validated at load time."""
    return load_all_localisations()


# --------------------------------------------------------------------------- #
# The policy table                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("level", "template", "allow_llm", "deterministic", "helplines", "emergency", "event"),
    [
        (RiskLevel.NONE, None, True, False, False, False, False),
        (RiskLevel.LOW, TEMPLATE_CHECK_IN_LOW, True, False, False, False, False),
        (RiskLevel.MEDIUM, TEMPLATE_CHECK_IN_MEDIUM, True, False, True, False, True),
        (RiskLevel.HIGH, TEMPLATE_CRISIS_HIGH, False, True, True, True, True),
        (RiskLevel.IMMINENT, TEMPLATE_CRISIS_IMMINENT, False, True, True, True, True),
    ],
)
def test_the_policy_table_is_the_whole_policy(
    escalator: Escalator,
    level: RiskLevel,
    template: str | None,
    allow_llm: bool,
    deterministic: bool,
    helplines: bool,
    emergency: bool,
    event: bool,
) -> None:
    """One row per level, asserted field by field.

    This is the table the product is built on. If a row changes, the change shows
    up here as a diff a reviewer has to read, not as a behaviour nobody asked for.
    """
    policy = escalator.policy_for(level)

    assert policy.level is level
    assert policy.template_id == template
    assert policy.allow_llm is allow_llm
    assert policy.deterministic_reply is deterministic
    assert policy.show_helplines is helplines
    assert policy.show_emergency_instruction is emergency
    assert policy.record_event is event


def test_no_level_is_missing_from_the_table(escalator: Escalator) -> None:
    for level in RiskLevel:
        assert escalator.policy_for(level).level is level


@pytest.mark.parametrize("level", CRISIS_LEVELS)
def test_a_crisis_level_never_calls_the_model(escalator: Escalator, level: RiskLevel) -> None:
    """The AGENTS.md non-negotiable: high risk gets a deterministic reply."""
    policy = escalator.policy_for(level)

    assert policy.allow_llm is False
    assert policy.deterministic_reply is True
    assert EscalationAction.BLOCK_LLM in policy.actions
    assert EscalationAction.SHOW_CRISIS_MESSAGE in policy.actions
    assert EscalationAction.SHOW_HELPLINES in policy.actions
    assert EscalationAction.SHOW_EMERGENCY_INSTRUCTION in policy.actions
    assert EscalationAction.RECORD_SAFETY_EVENT in policy.actions


def test_medium_and_below_still_allow_a_model_reply(escalator: Escalator) -> None:
    """Blocking the LLM below HIGH would turn every sad message into a card."""
    for level in (RiskLevel.NONE, RiskLevel.LOW, RiskLevel.MEDIUM):
        assert escalator.policy_for(level).allow_llm is True
        assert EscalationAction.BLOCK_LLM not in escalator.policy_for(level).actions


def test_none_shows_nothing_and_records_nothing(escalator: Escalator) -> None:
    policy = escalator.policy_for(RiskLevel.NONE)

    assert policy.template_id is None
    assert policy.actions == (EscalationAction.NORMAL_REPLY,)
    assert policy.record_event is False


# --------------------------------------------------------------------------- #
# Plans                                                                       #
# --------------------------------------------------------------------------- #


def test_a_plan_at_none_carries_no_message_and_no_helplines(escalator: Escalator) -> None:
    plan = escalator.plan(_assessment(RiskLevel.NONE), region="IN")

    assert plan.message is None
    assert plan.resources == ()
    assert plan.allow_llm is True
    assert plan.record_event is False


@pytest.mark.parametrize("level", CRISIS_LEVELS)
@pytest.mark.parametrize("region", ["IN", "US", "GB", "AU", "CA"])
def test_a_crisis_plan_carries_the_region_helplines_and_its_emergency_number(
    escalator: Escalator, level: RiskLevel, region: str
) -> None:
    plan = escalator.plan(_assessment(level), region=region)

    assert plan.region == region
    assert plan.region_fallback_used is False
    assert plan.resources, region
    # A region gets its own entries plus the global DEFAULT directories, which are
    # useful everywhere and keep a thin region from looking empty. Nothing else may
    # leak in: a US card must never show a number that belongs to India.
    assert all(resource.region in (region, FALLBACK_REGION) for resource in plan.resources), [
        resource.id for resource in plan.resources
    ]
    assert any(resource.region == region for resource in plan.resources)
    assert plan.emergency is not None
    assert plan.emergency.region == region
    assert plan.emergency_number
    assert plan.emergency_number == plan.emergency.number
    # The region's own emergency number wins; DEFAULT's 112 must not be listed
    # above it, which would be worse than not showing a global number at all.
    emergencies = [r for r in plan.resources if r.kind == "emergency"]
    assert [r.number for r in emergencies] == [plan.emergency_number]


@pytest.mark.parametrize("level", CRISIS_LEVELS)
def test_the_rendered_message_has_no_placeholders_left(
    escalator: Escalator, level: RiskLevel
) -> None:
    """A person in crisis must never be shown ``{emergency_number}``."""
    plan = escalator.plan(_assessment(level), region="IN", locale="en")
    assert plan.message is not None

    rendered = "\n".join(
        [
            plan.message.title,
            *plan.message.body,
            plan.message.helpline_intro or "",
            plan.message.emergency_instruction or "",
            plan.message.trusted_person or "",
            *plan.message.safety_steps,
            plan.message.closing or "",
            plan.message.disclaimer or "",
        ]
    )
    assert "{" not in rendered and "}" not in rendered
    instruction = plan.message.emergency_instruction
    assert instruction
    assert plan.emergency_number
    assert plan.emergency_number in instruction


def test_the_emergency_instruction_names_the_local_number(escalator: Escalator) -> None:
    """112 in India, 911 in the US: the instruction must follow the region."""
    india = escalator.plan(_assessment(RiskLevel.IMMINENT), region="IN")
    america = escalator.plan(_assessment(RiskLevel.IMMINENT), region="US")

    assert india.emergency_number == "112"
    assert america.emergency_number == "911"
    assert india.message is not None and america.message is not None
    assert "112" in (india.message.emergency_instruction or "")
    assert "911" in (america.message.emergency_instruction or "")


def test_an_unknown_region_falls_back_and_says_so(escalator: Escalator) -> None:
    plan = escalator.plan(_assessment(RiskLevel.HIGH), region="ZZ")

    assert plan.region == FALLBACK_REGION
    assert plan.region_fallback_used is True
    assert plan.resources
    # A person with an unrecognised country code still gets a number to call.
    assert plan.emergency_number == INTERNATIONAL_EMERGENCY_NUMBER


def test_no_region_at_all_still_produces_a_usable_plan(escalator: Escalator) -> None:
    plan = escalator.plan(_assessment(RiskLevel.HIGH))

    assert plan.region == FALLBACK_REGION
    assert plan.emergency_number == INTERNATIONAL_EMERGENCY_NUMBER
    assert plan.resources


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_every_locale_gets_its_own_crisis_copy(escalator: Escalator, locale: str) -> None:
    plan = escalator.plan(_assessment(RiskLevel.HIGH), region="IN", locale=locale)

    assert plan.locale == locale
    assert plan.locale_fallback_used is False
    assert plan.message is not None
    assert plan.message.locale == locale
    assert plan.message.template_id == TEMPLATE_CRISIS_HIGH
    assert plan.message.title.strip()


def test_an_unknown_locale_falls_back_to_english_and_says_so(escalator: Escalator) -> None:
    plan = escalator.plan(_assessment(RiskLevel.HIGH), region="IN", locale="xx")

    assert plan.locale == "en"
    assert plan.locale_fallback_used is True
    assert plan.message is not None


def test_a_regional_locale_tag_resolves_to_its_language(escalator: Escalator) -> None:
    plan = escalator.plan(_assessment(RiskLevel.HIGH), region="IN", locale="hi-IN")

    assert plan.locale == "hi"
    assert plan.locale_fallback_used is False


# --------------------------------------------------------------------------- #
# Who the message is addressed to                                             #
# --------------------------------------------------------------------------- #


def test_a_supporter_gets_the_supporter_template(escalator: Escalator) -> None:
    """Third person, speaker absent: the reader is not the person in crisis."""
    plan = escalator.plan(
        _assessment(RiskLevel.HIGH, third_person=True, first_person=False), region="GB"
    )

    assert plan.about_someone_else is True
    assert plan.message is not None
    assert plan.message.template_id == TEMPLATE_ABOUT_SOMEONE_ELSE


def test_a_quoted_crisis_is_addressed_to_the_supporter(escalator: Escalator) -> None:
    plan = escalator.plan(_assessment(RiskLevel.HIGH, third_person=True, quoted=True), region="GB")

    assert plan.about_someone_else is True
    assert plan.message is not None
    assert plan.message.template_id == TEMPLATE_ABOUT_SOMEONE_ELSE


def test_a_fiction_frame_is_addressed_to_the_writer(escalator: Escalator) -> None:
    plan = escalator.plan(
        _assessment(RiskLevel.HIGH, third_person=True, fiction_frame=True), region="US"
    )

    assert plan.about_someone_else is True
    assert plan.message is not None
    assert plan.message.template_id == TEMPLATE_ABOUT_SOMEONE_ELSE


def test_a_victim_is_not_treated_as_a_bystander(escalator: Escalator) -> None:
    """ "My husband threatens to kill me" is about somebody else AND about the
    speaker. The speaker is the one who needs the crisis card, so the
    first-person template must win.
    """
    plan = escalator.plan(
        _assessment(
            RiskLevel.HIGH,
            categories=(RiskCategory.ABUSE_DISCLOSURE,),
            third_person=True,
            first_person=True,
        ),
        region="IN",
    )

    assert plan.about_someone_else is False
    assert plan.message is not None
    assert plan.message.template_id == TEMPLATE_CRISIS_HIGH


def test_a_first_person_crisis_is_never_redirected(escalator: Escalator) -> None:
    plan = escalator.plan(
        _assessment(RiskLevel.IMMINENT, categories=(RiskCategory.INTENT_PLAN,)), region="IN"
    )

    assert plan.about_someone_else is False
    assert plan.message is not None
    assert plan.message.template_id == TEMPLATE_CRISIS_IMMINENT


# --------------------------------------------------------------------------- #
# Level bookkeeping                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("level", "code", "stored"),
    [
        (RiskLevel.NONE, "none", "none"),
        (RiskLevel.LOW, "low", "caution"),
        (RiskLevel.MEDIUM, "medium", "elevated"),
        (RiskLevel.HIGH, "high", "crisis"),
        (RiskLevel.IMMINENT, "imminent", "crisis"),
    ],
)
def test_the_wire_and_database_forms_of_a_level(
    escalator: Escalator, level: RiskLevel, code: str, stored: str
) -> None:
    """The five-level vocabulary collapses to the four the database stores.

    ``safety_events`` predates this module and has a CHECK constraint; widening it
    would mean a migration for no gain, so the mapping lives here and is tested.
    Only IMMINENT folds into another tier — into ``crisis``, alongside HIGH, which
    is the tier that already triggers the deterministic reply.
    """
    plan = escalator.plan(_assessment(level), region="IN")

    assert plan.level is level
    assert plan.level_code == code
    assert plan.stored_level_code == stored


def test_the_plan_exposes_the_policy_actions(escalator: Escalator) -> None:
    plan = escalator.plan(_assessment(RiskLevel.MEDIUM), region="IN")

    assert plan.actions == plan.policy.actions
    assert EscalationAction.ENCOURAGE_HELPLINE in plan.actions
    assert EscalationAction.SHOW_CRISIS_MESSAGE not in plan.actions


# --------------------------------------------------------------------------- #
# Safe messaging: the guarantee that matters most                             #
# --------------------------------------------------------------------------- #


def _template_text(template: CrisisTemplate) -> str:
    return "\n".join(
        [
            template.title,
            *template.body,
            template.helpline_intro or "",
            template.emergency_instruction or "",
            template.trusted_person or "",
            *template.safety_steps,
            template.closing or "",
            template.disclaimer or "",
        ]
    )


def test_no_template_in_any_language_names_a_method(
    all_localisations: Mapping[str, LocalisedContent],
) -> None:
    """WHO safe-messaging guidance: point to help, never to a means.

    A word-boundary match, because "can change" and "begun" both contain the
    letters of method words and a substring screen cries wolf on them.
    """
    pattern = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(word) for word in METHOD_WORDS) + r")(?!\w)",
        re.IGNORECASE,
    )
    offenders: list[str] = []
    for locale, content in all_localisations.items():
        for template_id, template in content.templates.items():
            for match in pattern.finditer(_template_text(template)):
                offenders.append(f"{locale}/{template_id}: {match.group(0)!r}")

    assert not offenders, "method wording in crisis copy: " + "; ".join(offenders)


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_crisis_copy_does_everything_the_brief_requires(
    all_localisations: Mapping[str, LocalisedContent], locale: str
) -> None:
    """Acknowledge the person, point to a helpline, name what to do in danger."""
    content = all_localisations[locale]
    for template_id in (TEMPLATE_CRISIS_HIGH, TEMPLATE_CRISIS_IMMINENT):
        template = content.template(template_id)
        text = _template_text(template)

        assert template.title.strip(), template_id
        assert template.body, template_id
        assert template.emergency_instruction, template_id
        assert template.helpline_intro, template_id
        assert template.safety_steps, template_id
        # The emergency instruction must carry the local number, and every
        # placeholder anywhere in the template must be one this locale whitelists.
        # Counting braces is the wrong test: {emergency_number} legitimately
        # appears in more than one field.
        assert "{emergency_number}" in template.emergency_instruction
        used = set(re.findall(r"\{([^{}]*)\}", text))
        assert used <= set(content.placeholder_whitelist), f"{locale}/{template_id}: {used}"
        # An unbalanced brace would render as literal punctuation to a person in
        # crisis, so the two counts have to agree.
        assert text.count("{") == text.count("}") == len(re.findall(r"\{[^{}]*\}", text))


@pytest.mark.parametrize("locale", ["en", "hi", "bn"])
def test_crisis_copy_states_it_is_not_a_crisis_service(
    all_localisations: Mapping[str, LocalisedContent], locale: str
) -> None:
    """AGENTS.md: never claim to be a therapist, a human, or an emergency service."""
    content = all_localisations[locale]
    templates = (TEMPLATE_CRISIS_HIGH, TEMPLATE_CRISIS_IMMINENT, TEMPLATE_ABOUT_SOMEONE_ELSE)
    for template_id in templates:
        template = content.template(template_id)
        assert template.disclaimer, f"{locale}/{template_id} has no disclaimer"
        assert len(template.disclaimer.strip()) >= 20


def test_the_english_imminent_copy_is_more_urgent_than_the_high_copy(
    all_localisations: Mapping[str, LocalisedContent],
) -> None:
    """Imminent means a plan, a timeframe or a means: the wording has to say now."""
    content = all_localisations["en"]
    high = content.template(TEMPLATE_CRISIS_HIGH)
    imminent = content.template(TEMPLATE_CRISIS_IMMINENT)

    assert imminent.title != high.title
    immediate = re.compile(r"(?<!\w)(?:now|right now|immediately|straight away)(?!\w)", re.I)
    assert immediate.search(_template_text(imminent)), "imminent copy says nothing about acting now"


def test_an_escalator_can_be_built_with_injected_content() -> None:
    """The content sources are injected, so a test never depends on the files."""
    escalator = build_escalator()

    assert isinstance(escalator, Escalator)
    assert escalator.locales
    assert "en" in escalator.locales
