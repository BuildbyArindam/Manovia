/**
 * Crisis fixtures for the component tests.
 *
 * These mirror the v2 wire shape from `app/content/helplines.json` — the same
 * fields, the same computed `tel_href`/`sms_href`, and the same provenance
 * requirements. Synthetic numbers only (`555…`), so nothing in the test suite can
 * be mistaken for a real helpline.
 */

import type { CrisisMessage, CrisisResource, CrisisResourcesResponse } from "@/lib/endpoints";

export function crisisResource(overrides: Partial<CrisisResource> = {}): CrisisResource {
  const number = overrides.number ?? "5550100";
  const type = overrides.type ?? "call";
  return {
    id: "test-call-line",
    region: "US",
    kind: "crisis_line",
    name: "Test Crisis Line",
    number,
    type,
    text_keyword: null,
    instructions: null,
    hours: "24/7",
    languages: ["en"],
    audience: null,
    description: "A synthetic helpline used by the tests.",
    url: null,
    source_url: "https://example.test/source",
    last_verified: "2026-10-01",
    needs_verification: false,
    verification_note: null,
    priority: 10,
    // The backend derives these; the fixtures must carry the same strings a
    // client would receive, or the tests would be asserting a shape that never
    // reaches the browser.
    tel_href: type === "call" && number !== null ? `tel:${number.replace(/[^\d+]/g, "")}` : null,
    sms_href: type === "text" && number !== null ? `sms:${number.replace(/[^\d+]/g, "")}` : null,
    ...overrides,
  };
}

const TEST_EMERGENCY: CrisisResource = crisisResource({
  id: "test-emergency",
  kind: "emergency",
  name: "Emergency services",
  number: "911",
  priority: 1,
  description: "Call if you or someone with you is in immediate danger.",
});

export { TEST_EMERGENCY };

/** Emergency first, then a call line, a text line and a chat directory entry. */
export const TEST_RESOURCES: readonly CrisisResource[] = [
  TEST_EMERGENCY,
  crisisResource({
    id: "test-call-line",
    name: "Test Crisis Line",
    number: "5550100",
    url: "https://example.test",
    priority: 4,
  }),
  crisisResource({
    id: "test-text-line",
    kind: "text_line",
    name: "Test Text Line",
    number: "55501",
    type: "text",
    text_keyword: "HOME",
    instructions: "Text HOME to 55501.",
    priority: 8,
  }),
  crisisResource({
    id: "test-directory",
    kind: "directory",
    name: "Test Directory",
    number: null,
    type: "web",
    url: "https://example.test/directory",
    priority: 30,
    description: "A synthetic directory of local lines.",
  }),
];

export const TEST_RESPONSE: CrisisResourcesResponse = {
  region: "US",
  requested_region: "US",
  fallback_used: false,
  version: 2,
  last_verified: "2026-10-09",
  source: "A synthetic source for the tests.",
  disclaimer: "Synthetic test data, not a real helpline.",
  known_regions: ["US", "DEFAULT"],
  emergency: TEST_EMERGENCY,
  resources: [...TEST_RESOURCES],
  needs_verification: [],
};

export function crisisMessage(overrides: Partial<CrisisMessage> = {}): CrisisMessage {
  return {
    template_id: "crisis.high",
    locale: "en",
    title: "What you're feeling is real, and you don't have to face it alone",
    body: ["Thank you for telling me.", "Please reach out to one of the helplines below."],
    helpline_intro: "Free, confidential support — call or text:",
    emergency_instruction:
      "If you are in immediate danger, or you have hurt yourself and need medical help, call 911 now.",
    trusted_person: "If you can, tell one person you trust where you are.",
    safety_steps: [
      "Move away from anything you could use to hurt yourself.",
      "Stay where other people are, if that feels safe.",
    ],
    closing: "You matter, and the way this feels can change.",
    disclaimer:
      "Manovia is a self-help companion. It is not therapy, and it is not a crisis service.",
    ...overrides,
  };
}
