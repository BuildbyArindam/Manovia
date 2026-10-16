/**
 * Crisis content on the device — the port of `backend/app/content/crisis.py` and
 * `backend/app/content/i18n.py`.
 *
 * Two rules from AGENTS.md shape this file. Safety rule 6: helpline data lives in
 * exactly one place, so the browser copy is *derived* from `helplines.json` by the
 * bundle compiler and this module only reads it — it never hard-codes a number.
 * Safety rule 4: a crisis message is pre-written and substituted, never composed,
 * so `render` refuses a template that would still contain a placeholder.
 *
 * The fallbacks are the point of the module: an unknown region is served
 * `DEFAULT` rather than rejected, an unknown locale falls back to English, and a
 * region with no emergency number of its own gets 112. Somebody in trouble who
 * typed the wrong country code still gets a number to call.
 */

import type { CrisisMessage, CrisisResource } from "@/lib/endpoints";

import type {
  CrisisTemplate,
  HelplineResource,
  LocalisedCrisisContent,
  PrivateBundle,
} from "../rules/bundleSchema";

/** `crisis.FALLBACK_REGION`. */
export const FALLBACK_REGION = "DEFAULT";
/** `escalation.INTERNATIONAL_EMERGENCY_NUMBER`. */
export const INTERNATIONAL_EMERGENCY_NUMBER = "112";

/** Raised when content and caller disagree — a crisis message is not improvised. */
export class CrisisContentError extends Error {
  constructor(message: string) {
    super(`crisis content: ${message}`);
    this.name = "CrisisContentError";
  }
}

/** `i18n._PLACEHOLDER_RE`: `{name}` and nothing else. */
const PLACEHOLDER = /\{([a-z_][a-z0-9_]*)\}/g;

function toCrisisResource(resource: HelplineResource): CrisisResource {
  return {
    id: resource.id,
    region: resource.region,
    kind: resource.kind,
    name: resource.name,
    number: resource.number,
    type: resource.type,
    text_keyword: resource.text_keyword,
    instructions: resource.instructions,
    hours: resource.hours,
    languages: [...resource.languages],
    audience: resource.audience,
    description: resource.description,
    url: resource.url,
    source_url: resource.source_url,
    last_verified: resource.last_verified,
    needs_verification: resource.needs_verification,
    verification_note: resource.verification_note,
    priority: resource.priority,
    tel_href: resource.tel_href,
    sms_href: resource.sms_href,
  };
}

/** `crisis.HelplineContent`, over the bundle's copy of `helplines.json`. */
export class HelplineContent {
  readonly version: number;
  readonly lastVerified: string;
  readonly source: string;
  readonly disclaimer: string;
  private readonly resources: HelplineResource[];
  private readonly regions: Set<string>;

  constructor(bundle: PrivateBundle) {
    const helplines = bundle.crisis.helplines;
    this.version = helplines.version;
    this.lastVerified = helplines.last_verified;
    this.source = helplines.source;
    this.disclaimer = helplines.disclaimer;
    this.resources = helplines.resources;
    this.regions = new Set(helplines.regions);
    if (this.regions.size === 0 || !this.regions.has(FALLBACK_REGION)) {
      throw new CrisisContentError("the bundle has no DEFAULT region, so it cannot fall back");
    }
  }

  knownRegions(): string[] {
    return [...this.regions];
  }

  /**
   * `resolve_region`: map a hint onto a served region.
   *
   * Returns `[region, fallbackUsed]`. Unknown or missing is served `DEFAULT`
   * rather than rejected.
   */
  resolveRegion(region?: string | null): [string, boolean] {
    const candidate = (region ?? "").trim().toUpperCase();
    if (this.regions.has(candidate)) {
      return [candidate, false];
    }
    return [FALLBACK_REGION, true];
  }

  /**
   * `resources_for`: one region's entries, sorted by priority then id.
   *
   * `DEFAULT` entries are appended to every region — the global directories are
   * useful everywhere and a thin region must never look empty. The `DEFAULT`
   * emergency number is dropped when the region has its own: a card showing 112
   * above 911 in the US is worse than one showing 911 alone.
   */
  resourcesFor(region?: string | null): CrisisResource[] {
    const [resolved] = this.resolveRegion(region);
    const selected = this.resources.filter((item) => item.region === resolved);
    if (resolved !== FALLBACK_REGION) {
      const hasEmergency = selected.some((item) => item.kind === "emergency");
      for (const item of this.resources) {
        if (item.region === FALLBACK_REGION && !(hasEmergency && item.kind === "emergency")) {
          selected.push(item);
        }
      }
    }
    // `priority` is what puts the emergency number first: it is data, set in
    // helplines.json, not a rule invented here. Id breaks ties, so the order is
    // stable and identical to Python's `sorted(key=(priority, id))`.
    return selected
      .sort(
        (left, right) =>
          left.priority - right.priority || (left.id < right.id ? -1 : left.id > right.id ? 1 : 0),
      )
      .map(toCrisisResource);
  }

  /** `emergency_for`: this region's emergency number, else `DEFAULT`'s, else none. */
  emergencyFor(region?: string | null): CrisisResource | null {
    const [resolved] = this.resolveRegion(region);
    for (const candidate of [resolved, FALLBACK_REGION]) {
      for (const item of this.resources) {
        if (item.region === candidate && item.kind === "emergency" && item.number) {
          return toCrisisResource(item);
        }
      }
    }
    return null;
  }

  /** `unverified`: ids whose fields rest on a secondary or conflicting source. */
  unverified(): string[] {
    return this.resources.filter((item) => item.needs_verification).map((item) => item.id);
  }
}

/** `i18n.LocalisedContent`: one locale's templates, rendered under a whitelist. */
export class LocalisedContent {
  readonly locale: string;
  readonly placeholderWhitelist: string[];
  private readonly templates: Record<string, CrisisTemplate>;

  constructor(content: LocalisedCrisisContent) {
    this.locale = content.locale;
    this.placeholderWhitelist = [...content.placeholder_whitelist];
    this.templates = content.templates;
  }

  template(templateId: string): CrisisTemplate {
    const template = this.templates[templateId];
    if (template === undefined) {
      throw new CrisisContentError(`${this.locale} has no template ${templateId}`);
    }
    return template;
  }

  hasTemplate(templateId: string): boolean {
    return this.templates[templateId] !== undefined;
  }

  /** Every human-readable string in a template, in a stable order. */
  private strings(templateId: string): string[] {
    const template = this.template(templateId);
    const collected = [template.title, ...template.body];
    for (const value of [
      template.helpline_intro,
      template.emergency_instruction,
      template.trusted_person,
      template.closing,
      template.disclaimer,
    ]) {
      if (value) {
        collected.push(value);
      }
    }
    return [...collected, ...template.safety_steps];
  }

  /** `placeholders`: the ones this template actually uses. */
  placeholders(templateId: string): string[] {
    const used = new Set<string>();
    for (const text of this.strings(templateId)) {
      for (const match of text.matchAll(PLACEHOLDER)) {
        used.add(match[1] as string);
      }
    }
    return [...used].sort();
  }

  /**
   * `render`: substitute whitelisted placeholders, and refuse anything else.
   *
   * `values` must cover every placeholder the template uses and may contain
   * nothing else — extra keys would mean the template and the caller disagreeing
   * about what is allowed to appear in a crisis message. The result is checked for
   * surviving placeholders, because a crisis card that says `{emergency_number}`
   * is worse than no card.
   */
  render(templateId: string, values: Record<string, string>): CrisisMessage {
    const template = this.template(templateId);
    const allowed = new Set(this.placeholderWhitelist);
    const used = new Set(this.placeholders(templateId));

    const unknown = [...used].filter((name) => !allowed.has(name)).sort();
    if (unknown.length > 0) {
      throw new CrisisContentError(
        `${this.locale}/${templateId} uses unlisted placeholders ${unknown.join(", ")}`,
      );
    }
    const provided = new Set(
      Object.entries(values)
        .filter(([, value]) => value)
        .map(([key]) => key),
    );
    const missing = [...used].filter((name) => !provided.has(name)).sort();
    if (missing.length > 0) {
      throw new CrisisContentError(
        `${this.locale}/${templateId} needs values for ${missing.join(", ")}`,
      );
    }
    const extra = Object.keys(values)
      .filter((name) => !used.has(name))
      .sort();
    if (extra.length > 0) {
      throw new CrisisContentError(
        `${this.locale}/${templateId} was given unused values ${extra.join(", ")}`,
      );
    }

    const fill = (text: string): string =>
      text.replace(PLACEHOLDER, (_match, name: string) => values[name] ?? "");
    const message: CrisisMessage = {
      template_id: templateId,
      locale: this.locale,
      title: fill(template.title),
      body: template.body.map(fill),
      helpline_intro: template.helpline_intro === null ? null : fill(template.helpline_intro),
      emergency_instruction:
        template.emergency_instruction === null ? null : fill(template.emergency_instruction),
      trusted_person: template.trusted_person === null ? null : fill(template.trusted_person),
      safety_steps: template.safety_steps.map(fill),
      closing: template.closing === null ? null : fill(template.closing),
      disclaimer: template.disclaimer === null ? null : fill(template.disclaimer),
    };

    for (const text of [
      message.title,
      ...message.body,
      ...message.safety_steps,
      message.helpline_intro,
      message.emergency_instruction,
      message.trusted_person,
      message.closing,
      message.disclaimer,
    ]) {
      if (typeof text === "string" && PLACEHOLDER.test(text)) {
        throw new CrisisContentError(`${this.locale}/${templateId} still contains a placeholder`);
      }
      PLACEHOLDER.lastIndex = 0;
    }
    return message;
  }

  /** `format_count`: a whole number in this locale's own digits. */
  formatCount(value: number, numerals: readonly string[]): string {
    if (!Number.isInteger(value) || value < 0) {
      throw new CrisisContentError("counts are whole and never negative");
    }
    return String(value)
      .split("")
      .map((digit) => numerals[Number(digit)] ?? digit)
      .join("");
  }
}

/** `i18n.resolve_locale`: strip any region, fall back to English. */
export function resolveLocale(
  locale: string | null | undefined,
  available: Iterable<string>,
): [string, boolean] {
  const known = new Set(available);
  const candidate = (locale ?? "").split("-", 1)[0]?.trim().toLowerCase() ?? "";
  if (known.has(candidate)) {
    return [candidate, false];
  }
  return ["en", candidate !== "en"];
}
