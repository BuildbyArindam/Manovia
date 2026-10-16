/**
 * Escalation on the device — the port of `backend/app/services/safety/escalation.py`.
 *
 * This is the module that decides what happens *after* a risk level is known, and
 * it is the one AGENTS.md safety rule 1 is about: at HIGH and IMMINENT there is no
 * model call, no generated prose and no room for improvisation. The reply is a
 * pre-written template, the helplines are the verified ones for the region, and
 * the emergency instruction is spelled out. The policy table comes from the bundle
 * (so it is Python's, compiled), and `backend/tests/safety/test_private_bundle.py`
 * asserts `allow_llm` is false at both crisis levels.
 *
 * The plan it produces has the same shape the API returns for `POST
 * /api/v1/crisis/assess`, so the existing `CrisisCard` and chat components render
 * a Private Mode turn exactly as they render a server-assessed one. Nothing here
 * touches the network and nothing here stores the message.
 */

import type {
  AssessmentContext,
  CrisisMessage,
  CrisisResource,
  EscalationAction,
  EscalationPolicy,
  RiskLevel,
} from "@/lib/endpoints";

import type { PrivateBundle, StoredLevel } from "../rules/bundleSchema";
import type { RiskAssessment } from "../rules/ruleEngine";
import {
  HelplineContent,
  INTERNATIONAL_EMERGENCY_NUMBER,
  LocalisedContent,
  resolveLocale,
} from "./content";

/** The four tiers that get written to the database (`base.to_stored_level`). */
export type { StoredLevel } from "../rules/bundleSchema";

/**
 * The crisis text for a client that has no card, mirroring the server's
 * `_plain_text`: title, body, the emergency instruction and the closing line.
 *
 * Helplines are deliberately *not* inlined. They travel as structured resources so
 * the card can render tappable `tel:` links, and they live only in
 * `backend/app/content/helplines.json` (AGENTS.md rule 6) — a string copy of them
 * here would be a second source of truth for a phone number.
 */
export function plainText(message: CrisisMessage): string {
  const parts = [message.title, ...message.body];
  if (message.emergency_instruction !== null && message.emergency_instruction !== "") {
    parts.push(message.emergency_instruction);
  }
  if (message.closing !== null && message.closing !== "") {
    parts.push(message.closing);
  }
  return parts.join("\n\n");
}

/**
 * Shown only if an escalation plan somehow has no message at HIGH or IMMINENT — the
 * same last resort the server keeps, word for word, because a person in crisis must
 * never be the one who discovers a content bug.
 */
export const LAST_RESORT_CRISIS_TEXT =
  "I'm really glad you told me. What you're describing needs a person, not an app. " +
  "If you are in danger, please call your local emergency number now, or contact a " +
  "crisis helpline. You don't have to go through this alone.";

/** True when the level means "a person, now" rather than "a conversation". */
export function isCrisis(level: RiskLevel): boolean {
  return level === "high" || level === "imminent";
}

/** Everything needed to respond to one assessment, with no trace of the input. */
export interface EscalationPlan {
  assessment: RiskAssessment;
  policy: EscalationPolicy;
  /** Rendered, placeholder-free, ready to display. `null` at NONE. */
  message: CrisisMessage | null;
  /** Region helplines in display order. Empty when the policy hides them. */
  resources: CrisisResource[];
  emergency: CrisisResource | null;
  /** The number the message was rendered with; `null` when the region has none. */
  emergencyNumber: string | null;
  locale: string;
  localeFallbackUsed: boolean;
  region: string;
  regionFallbackUsed: boolean;
  /** True when the copy addresses a supporter rather than the person in crisis. */
  aboutSomeoneElse: boolean;
  level: RiskLevel;
  storedLevel: StoredLevel;
  actions: EscalationAction[];
  /** False when the reply must be the template and nothing else. */
  allowLlm: boolean;
  recordEvent: boolean;
}

export interface PlanOptions {
  region?: string | null;
  locale?: string | null;
}

/** `escalation.Escalator`, reading the bundle's copy of the same content. */
export class Escalator {
  readonly helplines: HelplineContent;
  private readonly storedLevels: Record<RiskLevel, StoredLevel>;
  private readonly localisations: Map<string, LocalisedContent>;
  private readonly policies: Record<RiskLevel, EscalationPolicy>;
  /** The supporter copy for each level, kept beside the wire policy. */
  private readonly aboutOthers: Record<RiskLevel, string | null>;

  constructor(bundle: PrivateBundle) {
    this.helplines = new HelplineContent(bundle);
    this.storedLevels = bundle.engine.stored_levels;
    this.localisations = new Map(
      Object.entries(bundle.crisis.locales).map(([locale, content]) => [
        locale,
        new LocalisedContent(content),
      ]),
    );
    if (!this.localisations.has(bundle.crisis.fallback_locale)) {
      throw new Error(
        `the bundle's fallback locale ${bundle.crisis.fallback_locale} does not ship with it`,
      );
    }
    this.policies = Object.fromEntries(
      Object.entries(bundle.crisis.policy).map(([level, policy]) => [
        level as RiskLevel,
        {
          template_id: policy.template_id,
          actions: policy.actions as EscalationAction[],
          allow_llm: policy.allow_llm,
          deterministic_reply: policy.deterministic_reply,
          show_helplines: policy.show_helplines,
          show_emergency_instruction: policy.show_emergency_instruction,
          record_event: policy.record_event,
        } satisfies EscalationPolicy,
      ]),
    ) as Record<RiskLevel, EscalationPolicy>;
    this.aboutOthers = Object.fromEntries(
      Object.entries(bundle.crisis.policy).map(([level, policy]) => [
        level as RiskLevel,
        policy.about_others_template_id,
      ]),
    ) as Record<RiskLevel, string | null>;
  }

  /** `RiskLevel.to_stored()`: the four-tier label this level is stored under. */
  storedLevelFor(level: RiskLevel): StoredLevel {
    return this.storedLevels[level];
  }

  availableLocales(): string[] {
    return [...this.localisations.keys()].sort();
  }

  /** `policy_for`: the static policy for a level. */
  policyFor(level: RiskLevel): EscalationPolicy {
    const policy = this.policies[level];
    if (policy === undefined) {
      throw new Error(`no escalation policy for level ${level}`);
    }
    return policy;
  }

  /** `EscalationPolicy.template_for`: the supporter copy when the message is about somebody else. */
  templateFor(level: RiskLevel, aboutSomeoneElse: boolean): string | null {
    const aboutOthers = this.aboutOthers[level];
    if (aboutSomeoneElse && typeof aboutOthers === "string") {
      return aboutOthers;
    }
    return this.policyFor(level).template_id;
  }

  /**
   * `Escalator.plan`: build the response for one assessment.
   *
   * `region` and `locale` are hints, both resolved with a fallback: an unknown
   * region is served `DEFAULT` and an unknown locale English.
   */
  plan(assessment: RiskAssessment, options: PlanOptions = {}): EscalationPlan {
    const level = assessment.level;
    const policy = this.policyFor(level);
    const [locale, localeFallback] = resolveLocale(options.locale, this.localisations.keys());
    const content = this.localisations.get(locale) as LocalisedContent;
    const [region, regionFallback] = this.helplines.resolveRegion(options.region);

    const emergency = this.helplines.emergencyFor(region);
    const renderedWith =
      emergency !== null && emergency.number !== null && emergency.number !== ""
        ? emergency.number
        : INTERNATIONAL_EMERGENCY_NUMBER;

    const aboutSomeoneElse = this.isAboutSomeoneElse(assessment.context);
    const templateId = this.templateFor(level, aboutSomeoneElse);

    let message: CrisisMessage | null = null;
    if (templateId !== null) {
      const values: Record<string, string> = {};
      for (const name of content.placeholders(templateId)) {
        values[name] = renderedWith;
      }
      message = content.render(templateId, values);
    }

    return {
      assessment,
      policy,
      message,
      resources: policy.show_helplines ? this.helplines.resourcesFor(region) : [],
      emergency,
      // Python reports the number only when the region actually has one; the
      // message is rendered with 112 either way, so a person is never left without
      // a number to call.
      emergencyNumber: emergency !== null ? renderedWith : null,
      locale,
      localeFallbackUsed: localeFallback,
      region,
      regionFallbackUsed: regionFallback,
      aboutSomeoneElse,
      level,
      storedLevel: this.storedLevelFor(level),
      actions: [...policy.actions],
      allowLlm: policy.allow_llm,
      recordEvent: policy.record_event,
    };
  }

  /**
   * `_about_someone_else`: true when the crisis copy should address a supporter.
   *
   * Third person alone is not enough — "my husband threatens to kill me" is about
   * somebody else *and* about the speaker, and the speaker is the one who needs the
   * crisis card. So the supporter template is used only when the speaker is absent
   * from the sentence, or when the wording was quoted or framed as fiction: the two
   * cases where the risky words are plainly not the reader's own.
   */
  isAboutSomeoneElse(context: AssessmentContext): boolean {
    if (!context.third_person) {
      return false;
    }
    return context.fiction_frame || context.quoted || !context.first_person;
  }
}

let cached: Escalator | null = null;
let cachedFor: PrivateBundle | null = null;

/** The shared escalator for one bundle (`escalation.build_escalator`). */
export function buildEscalator(bundle: PrivateBundle): Escalator {
  if (cached === null || cachedFor !== bundle) {
    cached = new Escalator(bundle);
    cachedFor = bundle;
  }
  return cached;
}
