/**
 * The crisis plan, compared with the server's for 96 combinations.
 *
 * At HIGH and IMMINENT nothing is generated: the reply is a pre-written template
 * rendered with the region's emergency number, beside that region's verified
 * helplines (AGENTS.md safety rules 1, 4 and 6). Private Mode has to show *the same
 * card*, so it is compared rather than eyeballed.
 *
 * `evals/parity/python-crisis-plans.json` holds Python's plan for the cross product
 * of level x about-someone-else x locale x region (4 x 2 x 3 x 4 = 96), written by
 * `evals/parity/export_crisis_plans.py`. Every field is asserted: the policy, both
 * fallback flags, the emergency entry and number, the helpline ids *in display
 * order*, and the rendered message down to each paragraph.
 */

import { describe, expect, it } from "vitest";

import type { CrisisMessage, RiskLevel } from "@/lib/endpoints";

import { CRISIS_PLANS_PATH, readBundle, readText } from "../testSupport/repoArtifacts";
import type { RiskAssessment } from "../rules/ruleEngine";
import type { Escalator } from "./escalation";
import { buildEscalator, isCrisis } from "./escalation";
import {
  FALLBACK_REGION,
  INTERNATIONAL_EMERGENCY_NUMBER,
  HelplineContent,
  LocalisedContent,
  resolveLocale,
} from "./content";

interface PythonPlan {
  id: string;
  level: string;
  about_someone_else_input: boolean;
  locale_requested: string;
  region_requested: string;
  stored_level: string;
  about_someone_else: boolean;
  policy: {
    template_id: string | null;
    actions: string[];
    allow_llm: boolean;
    deterministic_reply: boolean;
    show_helplines: boolean;
    show_emergency_instruction: boolean;
    record_event: boolean;
  };
  locale: string;
  locale_fallback_used: boolean;
  region: string;
  region_fallback_used: boolean;
  emergency_number: string | null;
  emergency_id: string | null;
  resource_ids: string[];
  needs_verification: string[];
  message: (CrisisMessage & { template_id: string; locale: string }) | null;
}

interface PythonCrisisPlans {
  format: string;
  count: number;
  levels: string[];
  locales: string[];
  regions: string[];
  cases: PythonPlan[];
}

const bundle = readBundle();
const escalator: Escalator = buildEscalator(bundle);
const helplines = new HelplineContent(bundle);
const document = JSON.parse(readText(CRISIS_PLANS_PATH)) as PythonCrisisPlans;

/** The same synthetic assessment the Python exporter builds. */
function assessment(level: RiskLevel, aboutSomeoneElse: boolean): RiskAssessment {
  return {
    level,
    matched_categories: ["suicidal_ideation", "intent_plan", "access_to_means"],
    rationale_codes: ["si.want_to_die", "ip.have_a_plan", "am.weapon_in_the_house"],
    context: {
      first_person: !aboutSomeoneElse,
      third_person: aboutSomeoneElse,
      quoted: false,
      fiction_frame: false,
      timeframe: true,
      negated_hits: 0,
      figurative_hits: 0,
      language: null,
      truncated: false,
      variants_searched: 1,
    },
  };
}

function planFor(record: PythonPlan) {
  return escalator.plan(assessment(record.level as RiskLevel, record.about_someone_else_input), {
    region: record.region_requested,
    locale: record.locale_requested,
  });
}

describe("crisis plan parity with Python", () => {
  it("reads the whole matrix", () => {
    expect(document.format).toBe("manovia-parity-crisis-plans/1");
    expect(document.cases).toHaveLength(document.count);
    expect(document.count).toBe(
      document.levels.length * 2 * document.locales.length * document.regions.length,
    );
  });

  it.each(document.cases)("plans $id identically", (record) => {
    const plan = planFor(record);
    expect({
      storedLevel: plan.storedLevel,
      aboutSomeoneElse: plan.aboutSomeoneElse,
      locale: plan.locale,
      localeFallbackUsed: plan.localeFallbackUsed,
      region: plan.region,
      regionFallbackUsed: plan.regionFallbackUsed,
      emergencyNumber: plan.emergencyNumber,
      emergencyId: plan.emergency?.id ?? null,
      resourceIds: plan.resources.map((resource) => resource.id),
      needsVerification: plan.resources
        .filter((resource) => resource.needs_verification)
        .map((resource) => resource.id),
      policy: {
        template_id: plan.policy.template_id,
        actions: plan.policy.actions,
        allow_llm: plan.policy.allow_llm,
        deterministic_reply: plan.policy.deterministic_reply,
        show_helplines: plan.policy.show_helplines,
        show_emergency_instruction: plan.policy.show_emergency_instruction,
        record_event: plan.policy.record_event,
      },
    }).toEqual({
      storedLevel: record.stored_level,
      aboutSomeoneElse: record.about_someone_else,
      locale: record.locale,
      localeFallbackUsed: record.locale_fallback_used,
      region: record.region,
      regionFallbackUsed: record.region_fallback_used,
      emergencyNumber: record.emergency_number,
      emergencyId: record.emergency_id,
      resourceIds: record.resource_ids,
      needsVerification: record.needs_verification,
      policy: record.policy,
    });
  });

  it.each(document.cases)("renders the same crisis message for $id", (record) => {
    const plan = planFor(record);
    expect(plan.message).toEqual(record.message);
  });
});

describe("the escalation policy the browser runs on", () => {
  it("never lets a model write the reply at HIGH or IMMINENT", () => {
    for (const level of ["high", "imminent"] as const) {
      const policy = escalator.policyFor(level);
      expect(policy.allow_llm).toBe(false);
      expect(policy.deterministic_reply).toBe(true);
      expect(policy.actions).toContain("block_llm");
      expect(policy.show_helplines).toBe(true);
      expect(policy.show_emergency_instruction).toBe(true);
      expect(isCrisis(level)).toBe(true);
      expect(escalator.storedLevelFor(level)).toBe("crisis");
    }
    expect(escalator.policyFor("none").allow_llm).toBe(true);
    expect(isCrisis("medium")).toBe(false);
    // The stored tiers are Day-3 names, not the detection labels: LOW is stored as
    // CAUTION and MEDIUM as ELEVATED (ADR 0008 §4). The bundle carries the mapping
    // and the Python drift test pins it against `RiskLevel.to_stored()`.
    expect(bundle.engine.stored_levels).toEqual({
      none: "none",
      low: "caution",
      medium: "elevated",
      high: "crisis",
      imminent: "crisis",
    });
  });

  it("uses the supporter template only when the speaker is absent", () => {
    // "my husband threatens to kill me" is about somebody else *and* about the
    // speaker; the speaker is the one who needs the crisis card.
    const speaker = escalator.plan(assessment("high", false), { region: "IN" });
    expect(speaker.aboutSomeoneElse).toBe(false);
    expect(speaker.message?.template_id).toBe("crisis.high");

    const supporter = escalator.plan(assessment("high", true), { region: "IN" });
    expect(supporter.aboutSomeoneElse).toBe(true);
    expect(supporter.message?.template_id).toBe("crisis.about_someone_else");

    // Third person with the speaker present is not a supporter case.
    const mixed = escalator.plan(
      {
        ...assessment("high", true),
        context: { ...assessment("high", true).context, first_person: true },
      },
      { region: "IN" },
    );
    expect(mixed.aboutSomeoneElse).toBe(false);
    expect(mixed.message?.template_id).toBe("crisis.high");
  });

  it("renders no message at NONE and no helplines where the policy hides them", () => {
    const none = escalator.plan(assessment("none", false), { region: "IN" });
    expect(none.message).toBeNull();
    expect(none.resources).toEqual([]);
    expect(none.policy.show_helplines).toBe(false);
  });
});

describe("helpline and locale fallbacks", () => {
  it("serves DEFAULT for an unknown region and says so", () => {
    expect(helplines.resolveRegion("IN")).toEqual(["IN", false]);
    expect(helplines.resolveRegion(" in ")).toEqual(["IN", false]);
    expect(helplines.resolveRegion("ZZ")).toEqual([FALLBACK_REGION, true]);
    expect(helplines.resolveRegion(null)).toEqual([FALLBACK_REGION, true]);
  });

  it("gives every region an emergency number to call", () => {
    for (const region of [...helplines.knownRegions(), "ZZ", ""]) {
      const emergency = helplines.emergencyFor(region);
      expect(emergency, region).not.toBeNull();
      expect(emergency?.number, region).toBeTruthy();
      expect(emergency?.tel_href, region).toMatch(/^tel:\+?\d+$/);
    }
  });

  it("appends the global directories but drops a duplicate emergency number", () => {
    const india = helplines.resourcesFor("IN");
    const defaults = helplines.resourcesFor(FALLBACK_REGION);
    const indiaIds = new Set(india.map((resource) => resource.id));
    for (const resource of defaults) {
      if (resource.kind === "directory") {
        expect(indiaIds.has(resource.id), `${resource.id} should also serve IN`).toBe(true);
      }
    }
    const emergencies = india.filter((resource) => resource.kind === "emergency");
    expect(emergencies).toHaveLength(1);
    expect(emergencies[0]?.region).toBe("IN");
  });

  it("sorts by priority then id, so the emergency number is first", () => {
    for (const region of helplines.knownRegions()) {
      const resources = helplines.resourcesFor(region);
      expect(resources[0]?.kind, region).toBe("emergency");
      const keys = resources.map((resource) => [resource.priority, resource.id]);
      const sorted = [...keys].sort(
        (left, right) =>
          (left[0] as number) - (right[0] as number) ||
          ((left[1] as string) < (right[1] as string) ? -1 : 1),
      );
      expect(keys, region).toEqual(sorted);
    }
  });

  it("falls back to English for an unknown locale, and says so", () => {
    expect(resolveLocale("hi-IN", ["en", "hi", "bn"])).toEqual(["hi", false]);
    expect(resolveLocale("en", ["en", "hi", "bn"])).toEqual(["en", false]);
    expect(resolveLocale("fr", ["en", "hi", "bn"])).toEqual(["en", true]);
    expect(resolveLocale(null, ["en", "hi", "bn"])).toEqual(["en", true]);
  });

  it("renders 112 when a region has no emergency number of its own", () => {
    expect(INTERNATIONAL_EMERGENCY_NUMBER).toBe(bundle.crisis.international_emergency_number);
    const plan = escalator.plan(assessment("imminent", false), { region: "ZZ" });
    expect(plan.message?.emergency_instruction).toContain(plan.emergencyNumber ?? "");
    expect(plan.message?.body.join(" ")).toContain(plan.emergencyNumber ?? "");
  });

  it("leaves no placeholder in a rendered message", () => {
    for (const record of document.cases) {
      const message = planFor(record).message;
      if (message === null) {
        continue;
      }
      const text = [
        message.title,
        ...message.body,
        ...message.safety_steps,
        message.helpline_intro,
        message.emergency_instruction,
        message.trusted_person,
        message.closing,
        message.disclaimer,
      ]
        .filter((part): part is string => typeof part === "string")
        .join("\n");
      expect(text, record.id).not.toMatch(/\{[a-z_][a-z0-9_]*\}/);
    }
  });

  it("refuses to render a template with a value it did not ask for", () => {
    const english = bundle.crisis.locales.en;
    if (english === undefined) {
      throw new Error("the bundle ships no English locale");
    }
    const content = new LocalisedContent(english);
    expect(() => content.render("crisis.high", { emergency_number: "112", days: "3" })).toThrow(
      /unused values/,
    );
    expect(() => content.render("check_in.low", { emergency_number: "112" })).toThrow(
      /unused values/,
    );
    expect(() => content.render("crisis.high", {})).toThrow(/needs values for/);
    expect(() => content.render("crisis.missing", {})).toThrow(/no template/);
    expect(content.placeholders("crisis.high")).toEqual(["emergency_number"]);
    expect(content.placeholders("check_in.low")).toEqual([]);
  });
});
