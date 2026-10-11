import { ApiClient, MemoryTokenStore } from "@/lib/api";
import type {
  CompletionPayload,
  ExerciseCompletion,
  ExerciseDefinition,
  ExerciseHistoryResponse,
  ExerciseListResponse,
  RecommendationResponse,
} from "@/features/exercises/types";

/** Short fixtures make boundary tests readable; real YAML is exercised by API/e2e tests. */
export function breathingExercise(overrides: Partial<ExerciseDefinition> = {}): ExerciseDefinition {
  return {
    id: "box-breathing",
    title: { en: "Box breathing", hi: null, bn: null },
    instructions_language: "en",
    description: "A gentle, optional rhythm.",
    duration_seconds: 12,
    steps: [
      {
        id: "inhale",
        duration_seconds: 4,
        instruction: "Breathe in gently; ordinary-sized breaths are fine.",
        cue: "inhale",
        choices: [],
      },
      {
        id: "hold",
        duration_seconds: 4,
        instruction: "Pause only if comfortable; otherwise breathe normally.",
        cue: "hold-in",
        choices: [],
      },
      {
        id: "exhale",
        duration_seconds: 4,
        instruction: "Breathe out gently without forcing the count.",
        cue: "exhale",
        choices: [],
      },
    ],
    contraindications: ["If breath-holding makes you dizzy, breathe normally and stop."],
    notes: ["You may shorten counts or skip holds."],
    tags: ["breathing", "anxiety"],
    evidence_note: "This short exercise is not a treatment or a promise of relief.",
    ...overrides,
  };
}

export function plannerExercise(): ExerciseDefinition {
  return breathingExercise({
    id: "small-action-planner",
    title: { en: "One small action planner", hi: null, bn: null },
    tags: ["planning"],
    duration_seconds: 4,
    steps: [
      {
        id: "choose",
        duration_seconds: 2,
        instruction: "Pick one manageable action.",
        cue: "instruction",
        choices: [
          { id: "water", label: "Fill a glass of water" },
          { id: "rest", label: "Choose rest" },
        ],
      },
      {
        id: "when",
        duration_seconds: 2,
        instruction: "Choose a possible time, without making a commitment.",
        cue: "instruction",
        choices: [],
      },
    ],
  });
}

export function completion(overrides: Partial<ExerciseCompletion> = {}): ExerciseCompletion {
  return {
    id: "completion-1",
    exercise_id: "box-breathing",
    title: "Box breathing",
    definition_sha256: "f".repeat(64),
    duration_seconds: 12,
    pre_rating: 2,
    post_rating: 4,
    change: 2,
    summary:
      "You reported feeling 2 points higher afterwards (2 to 4 of 5). This is an observation, not proof that the exercise caused a change.",
    created_at: "2026-10-11T12:00:00Z",
    ...overrides,
  };
}

interface RecordedCall {
  path: string;
  method: string;
  body: unknown;
}

export interface ExercisesFake {
  client: ApiClient;
  calls: RecordedCall[];
  sent: CompletionPayload[];
  history: ExerciseCompletion[];
  offline: boolean;
  failComplete: Error | null;
  failCatalog: Error | null;
  failHistory: Error | null;
}

export function makeExercisesFake(
  options: { exercises?: ExerciseDefinition[]; history?: ExerciseCompletion[] } = {},
): ExercisesFake {
  const exercises = options.exercises ?? [breathingExercise(), plannerExercise()];
  const fake: ExercisesFake = {
    client: new ApiClient(),
    calls: [],
    sent: [],
    history: options.history ?? [],
    offline: false,
    failComplete: null,
    failCatalog: null,
    failHistory: null,
  };
  fake.client = new ApiClient({
    tokens: new MemoryTokenStore(),
    fetchImpl: async (path, init) => {
      const url = new URL(path, "http://fixture.test");
      const method = init?.method ?? "GET";
      const body: unknown = init?.body ? JSON.parse(String(init.body)) : null;
      fake.calls.push({ path: url.pathname + url.search, method, body });
      if (method === "POST") {
        fake.sent.push(body as CompletionPayload);
      }
      if (fake.offline) {
        throw new TypeError("fixture: offline");
      }
      let response: unknown;
      if (url.pathname === "/api/v1/exercises") {
        if (fake.failCatalog !== null) {
          throw fake.failCatalog;
        }
        response = { exercises, count: exercises.length } satisfies ExerciseListResponse;
      } else if (url.pathname.endsWith("/history")) {
        if (fake.failHistory !== null) {
          throw fake.failHistory;
        }
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const items = fake.history.slice(offset, offset + 20);
        response = {
          items,
          count: items.length,
          total: fake.history.length,
          limit: 20,
          offset,
          has_more: offset + items.length < fake.history.length,
        } satisfies ExerciseHistoryResponse;
      } else if (url.pathname.endsWith("/recommendations")) {
        const emotion = url.searchParams.get("emotion") ?? "neutral";
        const intensity = url.searchParams.get("intensity") ?? "moderate";
        response = {
          emotion,
          intensity,
          recommendations: [
            {
              exercise_id: "box-breathing",
              why: `You selected ${intensity} ${emotion}. Counting offers a steady pace; holds are optional.`,
            },
          ],
        } as RecommendationResponse;
      } else if (url.pathname.endsWith("/complete")) {
        if (fake.failComplete !== null) {
          throw fake.failComplete;
        }
        const payload = body as CompletionPayload;
        const existing = fake.history.find((entry) => entry.id === payload.completion_id);
        const id = decodeURIComponent(url.pathname.split("/").at(-2) ?? "");
        const exercise = exercises.find((entry) => entry.id === id);
        const saved =
          existing ??
          completion({
            id: payload.completion_id,
            exercise_id: id,
            title: exercise?.title.en ?? id,
            duration_seconds: exercise?.duration_seconds ?? 12,
            pre_rating: payload.pre_rating,
            post_rating: payload.post_rating,
            change:
              payload.pre_rating === null || payload.post_rating === null
                ? null
                : payload.post_rating - payload.pre_rating,
          });
        if (existing === undefined) {
          fake.history.unshift(saved);
        }
        response = saved;
      } else {
        throw new Error(`unexpected fixture request ${method} ${path}`);
      }
      return new Response(JSON.stringify(response), {
        status: method === "POST" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  return fake;
}
