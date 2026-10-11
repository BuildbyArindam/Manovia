/**
 * The hooks the screening page runs on: catalogue, definition, history, submit.
 *
 * Housekeeping lives here so the components stay presentational. A submitted
 * result invalidates the history (and the cadence that comes with it), so the
 * trend on screen can never be a render behind what was just written; the
 * definition is cached for the session because it is published content that only
 * changes when the deployment does.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type ApiClient } from "@/lib/api";

import {
  fetchAssessmentDefinition,
  fetchAssessmentHistory,
  fetchAssessmentResult,
  fetchInstruments,
  submitAssessment,
} from "./endpoints";
import type {
  AssessmentDefinition,
  AssessmentInstrument,
  AssessmentResultView,
  HistoryResponse,
  InstrumentSummary,
  SubmitResponse,
} from "./types";

export const assessmentKeys = {
  instruments: ["assessments", "instruments"] as const,
  definition: (instrument: AssessmentInstrument) =>
    ["assessments", "definition", instrument] as const,
  history: (instrument: AssessmentInstrument) => ["assessments", "history", instrument] as const,
  result: (instrument: AssessmentInstrument, id: string) =>
    ["assessments", "result", instrument, id] as const,
};

export function useInstruments(client: ApiClient = api) {
  return useQuery<InstrumentSummary[], Error>({
    queryKey: assessmentKeys.instruments,
    queryFn: () => fetchInstruments(client),
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useAssessmentDefinition(
  instrument: AssessmentInstrument | null,
  client: ApiClient = api,
) {
  return useQuery<AssessmentDefinition, Error>({
    queryKey: assessmentKeys.definition(instrument ?? "phq9"),
    queryFn: () => fetchAssessmentDefinition(instrument as AssessmentInstrument, client),
    enabled: instrument !== null,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

export function useAssessmentHistory(
  instrument: AssessmentInstrument | null,
  client: ApiClient = api,
) {
  return useQuery<HistoryResponse, Error>({
    queryKey: assessmentKeys.history(instrument ?? "phq9"),
    queryFn: () => fetchAssessmentHistory(instrument as AssessmentInstrument, { client }),
    enabled: instrument !== null,
  });
}

export function useAssessmentResult(
  instrument: AssessmentInstrument | null,
  resultId: string | null,
  client: ApiClient = api,
) {
  return useQuery<AssessmentResultView, Error>({
    queryKey: assessmentKeys.result(instrument ?? "phq9", resultId ?? "none"),
    queryFn: () =>
      fetchAssessmentResult(instrument as AssessmentInstrument, resultId as string, client),
    enabled: instrument !== null && resultId !== null,
  });
}

export function useSubmitAssessment(
  instrument: AssessmentInstrument | null,
  client: ApiClient = api,
) {
  const queryClient = useQueryClient();
  return useMutation<SubmitResponse, Error, { answers: Record<string, number>; locale?: string }>({
    mutationFn: ({ answers, locale }) =>
      submitAssessment(instrument as AssessmentInstrument, answers, { locale, client }),
    onSuccess: async () => {
      if (instrument !== null) {
        await queryClient.invalidateQueries({ queryKey: assessmentKeys.history(instrument) });
      }
    },
  });
}
