/**
 * Fake transports for the screening tests.
 *
 * The endpoints take a client as their last argument, so a test can drive the
 * real page — real state, real keyboard handling, real query invalidation —
 * without a network or a token store. The HTTP layer itself is covered by the
 * backend tests and by the Playwright spec, which runs against a real server.
 */

import type { ApiClient } from "@/lib/api";

import type {
  AssessmentCrisis,
  AssessmentDefinition,
  AssessmentResultView,
  BandSummary,
  HistoryEntry,
  HistoryResponse,
  InstrumentSummary,
  SubmitResponse,
} from "@/features/assessments/types";

const PHQ9_ITEMS = [
  { id: "phq9_1", position: 1, text: "Little interest or pleasure in doing things" },
  { id: "phq9_2", position: 2, text: "Feeling down, depressed, or hopeless" },
  { id: "phq9_3", position: 3, text: "Trouble falling or staying asleep, or sleeping too much" },
  { id: "phq9_4", position: 4, text: "Feeling tired or having little energy" },
  { id: "phq9_5", position: 5, text: "Poor appetite or overeating" },
  {
    id: "phq9_6",
    position: 6,
    text: "Feeling bad about yourself — or that you are a failure or have let yourself or your family down",
  },
  {
    id: "phq9_7",
    position: 7,
    text: "Trouble concentrating on things, such as reading the newspaper or watching television",
  },
  {
    id: "phq9_8",
    position: 8,
    text: "Moving or speaking so slowly that other people could have noticed",
  },
  {
    id: "phq9_9",
    position: 9,
    text: "Thoughts that you would be better off dead or of hurting yourself in some way",
  },
];

export const PHQ9_BANDS: BandSummary[] = [
  { id: "minimal", label: "Minimal", min: 0, max: 4 },
  { id: "mild", label: "Mild", min: 5, max: 9 },
  { id: "moderate", label: "Moderate", min: 10, max: 14 },
  { id: "moderately_severe", label: "Moderately severe", min: 15, max: 19 },
  { id: "severe", label: "Severe", min: 20, max: 27 },
];

export function instrumentSummary(overrides: Partial<InstrumentSummary> = {}): InstrumentSummary {
  return {
    instrument: "phq9",
    name: "Patient Health Questionnaire-9",
    short_name: "PHQ-9",
    measures: "depressive symptoms over the last two weeks",
    intro:
      "Over the last 2 weeks, how often have you been bothered by any of the following problems?",
    item_count: 9,
    timeframe_label: "the last 2 weeks",
    total_max: 27,
    ...overrides,
  };
}

export function assessmentDefinition(
  overrides: Partial<AssessmentDefinition> = {},
): AssessmentDefinition {
  return {
    instrument: "phq9",
    name: "Patient Health Questionnaire-9",
    short_name: "PHQ-9",
    measures: "depressive symptoms over the last two weeks",
    intro:
      "Over the last 2 weeks, how often have you been bothered by any of the following problems?",
    timeframe_days: 14,
    timeframe_label: "the last 2 weeks",
    response_options: [
      { value: 0, label: "Not at all", detail: "0 days in the last two weeks" },
      { value: 1, label: "Several days", detail: "roughly 1-6 days in the last two weeks" },
      {
        value: 2,
        label: "More than half the days",
        detail: "roughly 8-13 days in the last two weeks",
      },
      { value: 3, label: "Nearly every day", detail: "roughly 14 days in the last two weeks" },
    ],
    items: PHQ9_ITEMS,
    bands: PHQ9_BANDS,
    total_min: 0,
    total_max: 27,
    screening_not_diagnosis:
      "This is a screening result, not a diagnosis. Only a qualified professional can diagnose depression, and they would do it by talking with you rather than from a questionnaire alone.",
    how_to_read:
      "A screening score describes how the last two weeks have been for you. It is not a mark, a grade, or a permanent label.",
    safety: {
      item_id: "phq9_9",
      rule: "answer_greater_than_zero",
      level: "high",
      explanation:
        "One of the questions asks about thoughts that you would be better off dead or of hurting yourself. You did not answer “Not at all”, so we are showing support and helplines before your score.",
      encouragement:
        "Thoughts like this are more common than most people admit, and they are a sign to reach out rather than a sign that something cannot be helped.",
      paired_with_score:
        "Your total score is shown below as well, so you have the full picture. These thoughts matter on their own, separately from the number.",
    },
    cadence_notice:
      "You checked in sooner than the two weeks we usually suggest. That is completely fine, and your answers are saved — but scores move slowly, so a comparison after a few days says very little about how you are doing. This is a suggestion, not a limit: you can take the questionnaire whenever you want to.",
    recommended_interval_days: 14,
    provenance: {
      instrument_id: "phq9",
      developers:
        "Developed by Drs. Robert L. Spitzer, Janet B.W. Williams, Kurt Kroenke and colleagues, with an educational grant from Pfizer Inc.",
      licence_statement: "No permission required to reproduce, translate, display or distribute.",
      item_wording_source_url:
        "https://www.phqscreeners.com/images/sites/g/files/g10060481/f/201412/PHQ-9_English.pdf",
      item_wording_verified_on: "2026-10-11",
      availability_note:
        "The official English PHQ-9 sheet is published free of charge and states that no permission is required to reproduce, translate, display or distribute it.",
    },
    ...overrides,
  };
}

export function resultView(overrides: Partial<AssessmentResultView> = {}): AssessmentResultView {
  return {
    id: "result-1",
    instrument: "phq9",
    created_at: "2026-10-11T09:00:00Z",
    answers: PHQ9_ITEMS.map((item) => ({ item_id: item.id, value: 0 })),
    total: 1,
    max_total: 27,
    band: "minimal",
    band_label: "Minimal",
    band_min: 0,
    band_max: 4,
    band_explanation:
      "Your answers point to few or no symptoms of depression in the last two weeks. Hard days are part of every life, and this result describes a fortnight, not who you are.",
    what_helps: [
      "Keep doing what already works for you: sleep and wake at roughly the same time.",
      "If you have been under unusual strain, keep an eye on it.",
    ],
    when_to_seek_help:
      "If low mood, sleep problems, or loss of interest last longer than a couple of weeks, it is worth mentioning to a doctor or counsellor.",
    screening_not_diagnosis:
      "This is a screening result, not a diagnosis. Only a qualified professional can diagnose depression, and they would do it by talking with you rather than from a questionnaire alone.",
    how_to_read:
      "A screening score describes how the last two weeks have been for you. It is not a mark, a grade, or a permanent label.",
    ...overrides,
  };
}

export function historyEntry(overrides: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    id: "result-1",
    created_at: "2026-10-11T09:00:00Z",
    total: 1,
    max_total: 27,
    band: "minimal",
    band_label: "Minimal",
    ...overrides,
  };
}

export function historyResponse(entries: HistoryEntry[]): HistoryResponse {
  return {
    instrument: "phq9",
    short_name: "PHQ-9",
    total_max: 27,
    bands: PHQ9_BANDS,
    entries,
    count: entries.length,
    limit: 20,
    offset: 0,
    cadence: {
      recommended_interval_days: 14,
      is_first: entries.length === 0,
      days_since_previous: entries.length === 0 ? null : 0,
      due: entries.length === 0,
      notice:
        entries.length === 0
          ? "This is your first saved result."
          : "You checked in sooner than the two weeks we usually suggest. That is completely fine, and your answers are saved — but scores move slowly, so a comparison after a few days says very little about how you are doing. This is a suggestion, not a limit: you can take the questionnaire whenever you want to.",
    },
  };
}

export function crisisPayload(overrides: Partial<AssessmentCrisis> = {}): AssessmentCrisis {
  return {
    item_id: "phq9_9",
    level: "high",
    stored_level: "crisis",
    source: "self_report",
    matched_categories: ["suicidal_ideation"],
    rationale_codes: ["assessment.phq9_item9"],
    locale: "en",
    region: "DEFAULT",
    explanation:
      "One of the questions asks about thoughts that you would be better off dead or of hurting yourself. You did not answer “Not at all”, so we are showing support and helplines before your score — whatever the rest of your answers add up to.",
    encouragement:
      "Thoughts like this are more common than most people admit, and they are a sign to reach out rather than a sign that something cannot be helped.",
    paired_with_score:
      "Your total score is shown below as well, so you have the full picture. These thoughts matter on their own, separately from the number.",
    message: {
      template_id: "crisis.high",
      locale: "en",
      title: "What you're feeling is real, and you don't have to face it alone",
      body: [
        "Thank you for telling me. Putting this into words takes courage, and I'm glad you did.",
      ],
      helpline_intro: "Free, confidential support — call or text:",
      emergency_instruction:
        "If you are in immediate danger, or you have hurt yourself and need medical help, call 112 now.",
      trusted_person: "If you can, tell one person you trust where you are.",
      safety_steps: ["Move away from anything you could use to hurt yourself."],
      closing: "You matter, and the way this feels can change.",
      disclaimer: "Manovia is a self-help companion. It is not therapy, and not a crisis service.",
    },
    emergency: {
      id: "default-emergency",
      region: "DEFAULT",
      kind: "emergency",
      name: "Emergency services",
      number: "112",
      type: "call",
      text_keyword: null,
      instructions: null,
      hours: "24 hours",
      languages: ["en"],
      audience: null,
      description: "The international emergency number.",
      url: null,
      source_url: "https://example.test/emergency",
      last_verified: "2026-10-09",
      needs_verification: false,
      verification_note: null,
      priority: 0,
      tel_href: "tel:112",
      sms_href: null,
    },
    resources: [
      {
        id: "in-aasra",
        region: "IN",
        kind: "crisis_line",
        name: "AASRA",
        number: "+91 98204 66726",
        type: "call",
        text_keyword: null,
        instructions: null,
        hours: "24 hours",
        languages: ["en", "hi"],
        audience: null,
        description: "A helpline for anyone in distress.",
        url: null,
        source_url: "https://example.test/aasra",
        last_verified: "2026-10-09",
        needs_verification: false,
        verification_note: null,
        priority: 10,
        tel_href: "tel:+919820466726",
        sms_href: null,
      },
    ],
    result_saved: true,
    ...overrides,
  };
}

export interface AssessmentFake {
  client: ApiClient;
  /** Every answer set the page sent, in order. */
  sent: Array<Record<string, number>>;
  /** The history the fake serves, mutated by submissions. */
  entries: HistoryEntry[];
  failSubmit: Error | null;
}

/**
 * Build a client that answers the screening endpoints.
 *
 * `history` grows on submit, so a test can assert the trend the person sees
 * reflects what they just did — the invalidation path, not just the response.
 */
export function makeAssessmentFake(
  options: {
    instruments?: InstrumentSummary[];
    definition?: AssessmentDefinition;
    history?: HistoryEntry[];
    crisis?: AssessmentCrisis | null;
    submitted?: AssessmentResultView;
    failSubmit?: Error;
  } = {},
): AssessmentFake {
  const fake: AssessmentFake = {
    client: {} as ApiClient,
    sent: [],
    entries: options.history ?? [],
    failSubmit: options.failSubmit ?? null,
  };
  const definition = options.definition ?? assessmentDefinition();

  const client = {
    get: async (path: string): Promise<unknown> => {
      if (path === "/assessments") {
        return options.instruments ?? [instrumentSummary()];
      }
      if (path.startsWith("/assessments/") && path.endsWith("/history")) {
        return historyResponse([...fake.entries]);
      }
      if (path.includes("/history/")) {
        const id = path.split("/history/")[1] ?? "";
        const entry = fake.entries.find((candidate) => candidate.id === id);
        if (entry === undefined) {
          throw new Error(`unknown result ${id}`);
        }
        return resultView({ id: entry.id, created_at: entry.created_at, total: entry.total });
      }
      if (path === "/assessments/phq9") {
        return definition;
      }
      throw new Error(`unexpected GET ${path}`);
    },
    post: async (path: string, body?: unknown): Promise<unknown> => {
      if (!path.endsWith("/submit")) {
        throw new Error(`unexpected POST ${path}`);
      }
      if (fake.failSubmit !== null) {
        throw fake.failSubmit;
      }
      const answers = (body as { answers: Record<string, number> }).answers;
      fake.sent.push(answers);
      const result = options.submitted ?? resultView();
      const crisis = options.crisis === undefined ? null : options.crisis;
      fake.entries.unshift(
        historyEntry({
          id: result.id,
          created_at: result.created_at,
          total: result.total,
          max_total: result.max_total,
          band: result.band,
          band_label: result.band_label,
        }),
      );
      const response: SubmitResponse = {
        result,
        crisis,
        cadence: {
          recommended_interval_days: 14,
          is_first: fake.entries.length === 1,
          days_since_previous: null,
          due: true,
          notice: "Two weeks or more have passed since your last check-in.",
        },
      };
      return response;
    },
  };

  fake.client = client as unknown as ApiClient;
  return fake;
}
