export type StepCue = "instruction" | "inhale" | "hold-in" | "exhale" | "hold-out";
export type Intensity = "low" | "moderate" | "high";
export type ExerciseEmotion =
  "joy" | "sadness" | "anger" | "fear" | "anxiety" | "shame" | "loneliness" | "calm" | "neutral";

export interface ExerciseStep {
  id: string;
  duration_seconds: number;
  instruction: string;
  cue: StepCue;
  choices: { id: string; label: string }[];
}

export interface ExerciseDefinition {
  id: string;
  title: { en: string; hi: string | null; bn: string | null };
  instructions_language: "en";
  description: string;
  duration_seconds: number;
  steps: ExerciseStep[];
  contraindications: string[];
  notes: string[];
  tags: string[];
  evidence_note: string;
}

export interface ExerciseListResponse {
  exercises: ExerciseDefinition[];
  count: number;
}

export interface RecommendationResponse {
  emotion: ExerciseEmotion;
  intensity: Intensity;
  recommendations: { exercise_id: string; why: string }[];
}

export interface CompletionPayload {
  completion_id: string;
  pre_rating: number | null;
  post_rating: number | null;
}

export interface ExerciseCompletion {
  id: string;
  exercise_id: string;
  title: string;
  definition_sha256: string;
  duration_seconds: number;
  pre_rating: number | null;
  post_rating: number | null;
  change: number | null;
  summary: string;
  created_at: string;
}

export interface ExerciseHistoryResponse {
  items: ExerciseCompletion[];
  count: number;
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}
