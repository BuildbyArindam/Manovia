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
    The rules engine (AGENTS.md safety rule 1), the Day 9 ML ensemble on top
    of it, and the escalation policy (safety rule 2), returning everything a
    client needs to respond: the level, the matched categories, stable
    rationale codes, the ensemble's metadata, the policy, the pre-written
    localised message and the region's helplines. The ML step runs offline
    against a committed artifact — no external provider is in this path, so
    the answer cannot depend on one being up. When the ML classifier is
    disabled (``SAFETY_ML_ENABLED=false``) or its artifact is missing, the
    endpoint degrades to rules-only.

Privacy, on both: the input text is never echoed, never stored and never logged.
The only things written to a log line are a SHA-256 fingerprint prefix, the
character count and the assessment metadata, which is the convention the rest of
the app already uses (AGENTS.md safety rule 5).

**Audit rows (Day 9).** Whenever the final level is MEDIUM or higher the
endpoint writes a ``safety_events`` row: ids (both nullable here, because the
endpoint is public), the stored four-tier level, and *which detector* earned
the level (``rules`` or ``ml``). No text — the model has no text column and
``RiskAssessment`` cannot carry any. Day 8 shipped this endpoint without that
write to keep a public endpoint free of database writes; Day 9 accepts the
trade because an audit trail of escalations is worth more than the rows an
attacker can add under the global rate limit (120/min/IP by default, all
small metadata rows). The authenticated chat gate will write rows with full
user/session ids when it lands.
"""

from __future__ import annotations

from datetime import date

import structlog
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.api.deps import EscalatorDep, HelplinesDep, MLClassifierDep, RuleEngineDep, SessionDep
from app.content.crisis import CrisisResource
from app.content.i18n import RenderedTemplate
from app.db.repos import SafetyEventRepository
from app.models.enums import SafetyEventSource
from app.services.safety.base import AssessmentContext, text_fingerprint
from app.services.safety.ensemble import SOURCE_RULES, EnsembleDecision
from app.services.safety.escalation import EscalationPlan
from app.services.safety.normalise import DEFAULT_MAX_INPUT_CHARS
from app.services.safety.pipeline import run_ensemble

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


class EnsembleOut(BaseModel):
    """How the rules engine and the ML classifier combined (Day 9).

    Metadata only: levels, calibrated probabilities, and which detector the
    final level is attributed to. ``ml`` is ``None`` when the classifier is
    disabled or degraded, in which case the pipeline ran rules-only.
    """

    #: True when an ML prediction took part in the decision.
    ml_used: bool
    #: The model's argmax level, or ``None`` if it did not run.
    ml_level: str | None = None
    #: The model's top-class calibrated probability, if it ran.
    ml_confidence: float | None = None
    #: P(high) + P(imminent) — the quantity the raise rules read.
    ml_crisis_mass: float | None = None
    #: Which detector earned the final level: ``rules``, ``ml`` or
    #: ``ensemble.uncertain``. Drives the ``safety_events.source`` column.
    source: str
    #: True exactly when the uncertain -> MEDIUM gentle-check-in policy fired.
    uncertain_checkin: bool


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
    #: The rules/ML combination metadata. ``None`` only in the legacy shape;
    #: always populated now.
    ensemble: EnsembleOut
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


def _ensemble_to_out(decision: EnsembleDecision) -> EnsembleOut:
    """Wire shape of the ensemble decision (metadata only)."""
    return EnsembleOut(
        ml_used=decision.ml_used,
        ml_level=decision.ml_level.label if decision.ml_level is not None else None,
        ml_confidence=decision.ml_confidence,
        ml_crisis_mass=decision.ml_high_mass,
        source=decision.source,
        uncertain_checkin=decision.uncertain_checkin,
    )


def _plan_to_out(plan: EscalationPlan, ensemble: EnsembleOut) -> AssessOut:
    """Turn an escalation plan into the wire shape (metadata and copy only)."""
    assessment = plan.assessment
    policy = plan.policy
    return AssessOut(
        level=plan.level_code,
        stored_level=plan.stored_level_code,
        matched_categories=[category.value for category in assessment.matched_categories],
        rationale_codes=list(assessment.rationale_codes),
        context=assessment.context,
        ensemble=ensemble,
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
    request: Request,
    session: SessionDep,
    engine: RuleEngineDep,
    escalator: EscalatorDep,
    classifier: MLClassifierDep,
) -> AssessOut:
    """Assess one message: rules engine + ML ensemble, then the response plan.

    The matchers are CPU-bound and synchronous, so they run in a thread pool
    rather than blocking the event loop. The assessment and the plan are pure
    metadata plus pre-written copy: nothing derived from the input text is
    echoed back. At MEDIUM or above a metadata-only ``safety_events`` row is
    written (level + source, never text).
    """
    settings = request.app.state.settings
    final_assessment, decision = await run_ensemble(
        payload.text,
        payload.language,
        engine,
        classifier,
        min_confidence=settings.safety_ml_min_confidence,
        crisis_mass_floor=settings.safety_ml_crisis_mass_floor,
        suspicion_floor=settings.safety_ml_suspicion_floor,
    )
    plan = escalator.plan(final_assessment, region=payload.region, locale=payload.locale)

    # Metadata-only audit row at MEDIUM+: which tier, and which detector
    # earned it. No user ids exist on this public endpoint; both stay NULL.
    if plan.record_event:
        source = (
            SafetyEventSource.RULES if decision.source == SOURCE_RULES else SafetyEventSource.ML
        )
        await SafetyEventRepository(session).record(
            risk_level=final_assessment.to_stored_level(),
            source=source,
        )
        await session.commit()

    # Fingerprint and length only — never the text (AGENTS.md safety rule 5).
    structlog.get_logger().info(
        "crisis_assessed",
        text_sha=text_fingerprint(payload.text),
        text_length=len(payload.text),
        level=plan.level_code,
        stored_level=plan.stored_level_code,
        categories=[category.value for category in final_assessment.matched_categories],
        rationale_codes=list(final_assessment.rationale_codes),
        third_person=final_assessment.context.third_person,
        about_someone_else=plan.about_someone_else,
        template_id=plan.policy.template_for(about_someone_else=plan.about_someone_else),
        locale=plan.locale,
        region=plan.region,
        allow_llm=plan.allow_llm,
        truncated=final_assessment.context.truncated,
        ensemble_source=decision.source,
        ml_used=decision.ml_used,
        ml_confidence=decision.ml_confidence,
        uncertain_checkin=decision.uncertain_checkin,
    )
    return _plan_to_out(plan, _ensemble_to_out(decision))


__all__ = [
    "MAX_REQUEST_CHARS",
    "AssessIn",
    "AssessOut",
    "CrisisResourcesOut",
    "PolicyOut",
    "router",
]
