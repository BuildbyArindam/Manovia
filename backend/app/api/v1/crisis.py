"""Crisis resources and rule-only risk assessment.

Public by design. Someone in trouble has not signed in and must never be asked
to: an authentication wall in front of a helpline list — or in front of the thing
that tells you whether you need one — is a safety bug, not a security feature.
Both endpoints sit behind the same global rate limiter as everything else under
``/api/`` (``app.core.middleware``), which is enough to stop a flood without
asking a frightened person for credentials.

Two endpoints:

``GET /crisis/resources?region=IN``
    The helpline list for a region, in display order, with that region's
    emergency number called out. Without ``region`` the whole shipped file is
    returned, which is what the "Need help now?" modal shows. An unknown region
    is served ``DEFAULT`` with ``fallback_used: true`` — never a 404, never an
    empty list.

``POST /crisis/assess``
    The rules engine (AGENTS.md safety rule 1) plus the escalation policy
    (safety rule 2), returning everything a client needs to respond: the level,
    the matched categories, stable rationale codes, the policy, the pre-written
    localised message and the region's helplines. **Rule-only** — no model call,
    so the answer does not depend on a provider being up.

Privacy, on both: the input text is never echoed, never stored and never logged.
The only things written to a log line are a SHA-256 fingerprint prefix, the
character count and the assessment metadata, which is the convention the rest of
the app already uses (AGENTS.md safety rule 5).

This endpoint does **not** write a ``safety_events`` row. It is public and
unauthenticated, so writing rows from it would hand an attacker a free way to
fill a table. ``policy.record_event`` tells the authenticated chat path (which
runs the same engine before every model call) that an audit row is due; that
write lands with the chat gate.
"""

from __future__ import annotations

from datetime import date

import structlog
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.api.deps import EscalatorDep, HelplinesDep, RuleEngineDep
from app.content.crisis import CrisisResource
from app.content.i18n import RenderedTemplate
from app.services.safety.base import AssessmentContext, text_fingerprint
from app.services.safety.escalation import EscalationPlan
from app.services.safety.normalise import DEFAULT_MAX_INPUT_CHARS

router = APIRouter(prefix="/crisis", tags=["crisis"])

#: Two engine windows: the engine assesses the first ``DEFAULT_MAX_INPUT_CHARS``
#: and reports ``input.truncated``, so a long entry is still answered rather than
#: rejected. The request cap exists to keep a megabyte paste off a CPU-bound
#: matcher, and is deliberately generous enough that nobody hits it by accident.
MAX_REQUEST_CHARS: int = DEFAULT_MAX_INPUT_CHARS * 2

RegionQuery = Query(
    default=None,
    min_length=2,
    max_length=16,
    description="ISO-style region code (IN, US, GB, AU, CA). Unknown codes fall back to DEFAULT.",
    examples=["IN"],
)


class CrisisResourcesOut(BaseModel):
    """The helpline list for one region, plus the file's provenance."""

    #: The region actually served (never ``None`` when one was requested).
    region: str | None = None
    #: Echoed back so a client can tell "you asked for X, we served Y".
    requested_region: str | None = None
    #: True when the requested region is unknown and ``DEFAULT`` was served.
    fallback_used: bool = False
    version: int
    last_verified: date
    source: str
    disclaimer: str
    known_regions: list[str]
    #: The emergency number for this region, called out so a client can put it
    #: first and make it a ``tel:`` link even when the list is collapsed.
    emergency: CrisisResource | None = None
    resources: list[CrisisResource]
    #: Ids whose ``needs_verification`` is true — surfaced so a UI can label them
    #: rather than present a stale number as certain.
    needs_verification: list[str] = Field(default_factory=list)


class AssessIn(BaseModel):
    """One message to assess. Text is read and forgotten inside this request."""

    text: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)
    region: str | None = Field(default=None, max_length=16)
    #: UI language for the pre-written reply (``en``, ``hi``, ``bn``; BCP-47 tags
    #: are accepted and reduced to their language subtag).
    locale: str | None = Field(default=None, max_length=16)
    #: Optional language hint recorded on the assessment. Never used to route:
    #: every pattern runs against every message.
    language: str | None = Field(default=None, max_length=16)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Whitespace is not a message, and must not be assessed as one."""
        if not value.strip():
            raise ValueError("text must contain something other than whitespace")
        return value


class PolicyOut(BaseModel):
    """What the product must do with this assessment."""

    template_id: str | None
    actions: list[str]
    #: False at HIGH and IMMINENT: no model call happens for this turn.
    allow_llm: bool
    deterministic_reply: bool
    show_helplines: bool
    show_emergency_instruction: bool
    #: True when the caller (the chat path) must write a metadata-only audit row.
    record_event: bool


class AssessOut(BaseModel):
    """The assessment, the policy and the words to show — never the input."""

    #: ``none``, ``low``, ``medium``, ``high``, ``imminent``.
    level: str
    #: The four-tier value a database row would store (``high`` and ``imminent``
    #: both map to ``crisis``).
    stored_level: str
    matched_categories: list[str]
    #: Stable pattern ids and context codes. Never fragments of the message.
    rationale_codes: list[str]
    context: AssessmentContext
    policy: PolicyOut
    #: True when the reply is addressed to a supporter ("my friend says…"), so a
    #: client can adjust how it frames the card.
    about_someone_else: bool
    locale: str
    locale_fallback_used: bool
    region: str
    region_fallback_used: bool
    #: The pre-written, already-substituted message. ``None`` at NONE.
    crisis: RenderedTemplate | None = None
    emergency: CrisisResource | None = None
    resources: list[CrisisResource] = Field(default_factory=list)


@router.get("/resources", response_model=CrisisResourcesOut)
async def crisis_resources(
    content: HelplinesDep,
    region: str | None = RegionQuery,
) -> CrisisResourcesOut:
    """Helplines for one region, in the order the client should show them.

    No authentication, no consent gate, no account required.
    """
    if region is None:
        resources = sorted(content.resources, key=lambda item: (item.priority, item.id))
        return CrisisResourcesOut(
            region=None,
            requested_region=None,
            fallback_used=False,
            version=content.version,
            last_verified=content.last_verified,
            source=content.source,
            disclaimer=content.disclaimer,
            known_regions=list(content.known_regions()),
            emergency=None,
            resources=resources,
            needs_verification=[item.id for item in content.unverified()],
        )

    resolved, fallback_used = content.resolve_region(region)
    resources = content.resources_for(resolved)
    return CrisisResourcesOut(
        region=resolved,
        requested_region=region.strip().upper(),
        fallback_used=fallback_used,
        version=content.version,
        last_verified=content.last_verified,
        source=content.source,
        disclaimer=content.disclaimer,
        known_regions=list(content.known_regions()),
        emergency=content.emergency_for(resolved),
        resources=resources,
        needs_verification=[item.id for item in resources if item.needs_verification],
    )


def _plan_to_out(plan: EscalationPlan) -> AssessOut:
    """Turn an escalation plan into the wire shape (metadata and copy only)."""
    assessment = plan.assessment
    policy = plan.policy
    return AssessOut(
        level=plan.level_code,
        stored_level=plan.stored_level_code,
        matched_categories=[category.value for category in assessment.matched_categories],
        rationale_codes=list(assessment.rationale_codes),
        context=assessment.context,
        policy=PolicyOut(
            template_id=policy.template_for(about_someone_else=plan.about_someone_else),
            actions=[action.value for action in policy.actions],
            allow_llm=policy.allow_llm,
            deterministic_reply=policy.deterministic_reply,
            show_helplines=policy.show_helplines,
            show_emergency_instruction=policy.show_emergency_instruction,
            record_event=policy.record_event,
        ),
        about_someone_else=plan.about_someone_else,
        locale=plan.locale,
        locale_fallback_used=plan.locale_fallback_used,
        region=plan.region,
        region_fallback_used=plan.region_fallback_used,
        crisis=plan.message,
        emergency=plan.emergency,
        resources=list(plan.resources),
    )


@router.post("/assess", response_model=AssessOut)
async def crisis_assess(
    payload: AssessIn,
    engine: RuleEngineDep,
    escalator: EscalatorDep,
) -> AssessOut:
    """Assess one message with the rules engine and return the response plan.

    The matcher is CPU-bound and synchronous, so it runs in a thread pool rather
    than blocking the event loop. The assessment and the plan are pure metadata
    plus pre-written copy: nothing derived from the input text is echoed back.
    """
    assessment = await run_in_threadpool(engine.assess, payload.text, language=payload.language)
    plan = escalator.plan(assessment, region=payload.region, locale=payload.locale)

    # Fingerprint and length only — never the text (AGENTS.md safety rule 5).
    structlog.get_logger().info(
        "crisis_assessed",
        text_sha=text_fingerprint(payload.text),
        text_length=len(payload.text),
        level=plan.level_code,
        stored_level=plan.stored_level_code,
        categories=[category.value for category in assessment.matched_categories],
        rationale_codes=list(assessment.rationale_codes),
        third_person=assessment.context.third_person,
        about_someone_else=plan.about_someone_else,
        template_id=plan.policy.template_for(about_someone_else=plan.about_someone_else),
        locale=plan.locale,
        region=plan.region,
        allow_llm=plan.allow_llm,
        truncated=assessment.context.truncated,
    )
    return _plan_to_out(plan)


__all__ = [
    "MAX_REQUEST_CHARS",
    "AssessIn",
    "AssessOut",
    "CrisisResourcesOut",
    "PolicyOut",
    "router",
]
