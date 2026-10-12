import { api as defaultClient, type ApiClient } from "@/lib/api";
import { readCachedExercises, writeCachedExercises } from "@/lib/pwa/offlineCache";

import type {
  CompletionPayload,
  ExerciseCompletion,
  ExerciseDefinition,
  ExerciseEmotion,
  ExerciseHistoryResponse,
  ExerciseListResponse,
  Intensity,
  RecommendationResponse,
} from "./types";

export const EXERCISE_HISTORY_KEY = ["exercises", "history"] as const;
export const EXERCISE_ENDPOINTS = {
  list: "/exercises",
  detail: (id: string): string => `/exercises/${encodeURIComponent(id)}`,
  recommendations: "/exercises/recommendations",
  history: "/exercises/history",
  complete: (id: string): string => `/exercises/${encodeURIComponent(id)}/complete`,
} as const;

/** All steps arrive together; running never depends on another fetch. */
export async function fetchExercises(
  client: ApiClient = defaultClient,
): Promise<ExerciseListResponse> {
  try {
    const response = await client.get<ExerciseListResponse>(EXERCISE_ENDPOINTS.list, {
      authenticated: false,
    });
    writeCachedExercises(response);
    return response;
  } catch (error) {
    const cached = readCachedExercises();
    if (cached !== null) {
      return cached;
    }
    throw error;
  }
}

export async function fetchExercise(
  id: string,
  client: ApiClient = defaultClient,
): Promise<ExerciseDefinition> {
  return client.get<ExerciseDefinition>(EXERCISE_ENDPOINTS.detail(id), { authenticated: false });
}

export async function fetchRecommendations(
  emotion: ExerciseEmotion,
  intensity: Intensity,
  client: ApiClient = defaultClient,
): Promise<RecommendationResponse> {
  const params = new URLSearchParams({ emotion, intensity });
  return client.get<RecommendationResponse>(`${EXERCISE_ENDPOINTS.recommendations}?${params}`, {
    authenticated: false,
  });
}

export async function completeExercise(
  exerciseId: string,
  payload: CompletionPayload,
  client: ApiClient = defaultClient,
): Promise<ExerciseCompletion> {
  return client.post<ExerciseCompletion>(EXERCISE_ENDPOINTS.complete(exerciseId), payload);
}

export async function fetchExerciseHistory(
  offset = 0,
  client: ApiClient = defaultClient,
): Promise<ExerciseHistoryResponse> {
  return client.get<ExerciseHistoryResponse>(
    `${EXERCISE_ENDPOINTS.history}?limit=20&offset=${offset}`,
  );
}
