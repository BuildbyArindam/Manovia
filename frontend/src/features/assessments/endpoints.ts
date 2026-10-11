/**
 * The screening endpoints.
 *
 * Every function takes the client as an optional last argument, which is how the
 * tests inject a client backed by a fake `fetch` (the house pattern since Day 5).
 * All four routes require a session and the two personal-data consents; the
 * definition route is public because the questionnaire itself is published
 * material and contains nothing about the reader.
 */

import { api as defaultClient, type ApiClient } from "@/lib/api";

import type {
  AssessmentDefinition,
  AssessmentInstrument,
  AssessmentResultView,
  HistoryResponse,
  InstrumentSummary,
  SubmitResponse,
} from "./types";

export const ASSESSMENT_ENDPOINTS = {
  list: "/assessments",
  definition: (instrument: AssessmentInstrument): string => `/assessments/${instrument}`,
  submit: (instrument: AssessmentInstrument): string => `/assessments/${instrument}/submit`,
  history: (instrument: AssessmentInstrument): string => `/assessments/${instrument}/history`,
  result: (instrument: AssessmentInstrument, id: string): string =>
    `/assessments/${instrument}/history/${id}`,
} as const;

/** The instruments this deployment ships. Public. */
export async function fetchInstruments(
  client: ApiClient = defaultClient,
): Promise<InstrumentSummary[]> {
  return client.get<InstrumentSummary[]>(ASSESSMENT_ENDPOINTS.list, { authenticated: false });
}

/** One instrument: official wording, options, bands and provenance. Public. */
export async function fetchAssessmentDefinition(
  instrument: AssessmentInstrument,
  client: ApiClient = defaultClient,
): Promise<AssessmentDefinition> {
  return client.get<AssessmentDefinition>(ASSESSMENT_ENDPOINTS.definition(instrument), {
    authenticated: false,
  });
}

/**
 * Score and store one completed questionnaire.
 *
 * `answers` must contain every item exactly once; the server rejects a partial
 * set rather than scoring the questions that were filled in.
 */
export async function submitAssessment(
  instrument: AssessmentInstrument,
  answers: Record<string, number>,
  options: { locale?: string; client?: ApiClient } = {},
): Promise<SubmitResponse> {
  const client = options.client ?? defaultClient;
  const body: { answers: Record<string, number>; locale?: string } = { answers };
  if (options.locale !== undefined) {
    body.locale = options.locale;
  }
  return client.post<SubmitResponse>(ASSESSMENT_ENDPOINTS.submit(instrument), body);
}

/** The caller's own runs, newest first. */
export async function fetchAssessmentHistory(
  instrument: AssessmentInstrument,
  options: { limit?: number; client?: ApiClient } = {},
): Promise<HistoryResponse> {
  const client = options.client ?? defaultClient;
  const query = options.limit === undefined ? "" : `?limit=${options.limit}`;
  return client.get<HistoryResponse>(`${ASSESSMENT_ENDPOINTS.history(instrument)}${query}`);
}

/** One stored run, for the printable summary of an older result. */
export async function fetchAssessmentResult(
  instrument: AssessmentInstrument,
  resultId: string,
  client: ApiClient = defaultClient,
): Promise<AssessmentResultView> {
  return client.get<AssessmentResultView>(ASSESSMENT_ENDPOINTS.result(instrument, resultId));
}
