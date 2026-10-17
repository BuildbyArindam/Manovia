/** Day 45: meditation surface — the client-side mirror of the backend models.
 *
 * Closed vocabularies match the backend exactly: an unknown value here is a
 * type error, and the backend re-validates (a silent default is how a
 * closed-eye script would reach somebody driving).
 */

export type MeditationLanguage = "en" | "hi" | "bn";
export type MeditationContext = "seated" | "walking" | "driving" | "unknown";
export type MeditationThemeId =
  | "auto"
  | "morning"
  | "focus"
  | "stress-release"
  | "calm"
  | "sleep"
  | "gratitude";
export type ResolvedThemeId = Exclude<MeditationThemeId, "auto">;
export type PreferenceTag =
  | "breathing"
  | "body"
  | "nature"
  | "warmth"
  | "gratitude"
  | "quiet"
  | "rain"
  | "morning"
  | "night"
  | "focus";

/** The closed preference vocabulary (mirrors the backend PREFERENCE_TAGS). */
export const PREFERENCE_TAGS: readonly PreferenceTag[] = [
  "breathing",
  "body",
  "nature",
  "warmth",
  "gratitude",
  "quiet",
  "rain",
  "morning",
  "night",
  "focus",
];

/** Duration bounds in seconds (backend: 120-900). */
export const MIN_DURATION_SECONDS = 120;
export const MAX_DURATION_SECONDS = 900;
/** Voice speed bounds (backend: 0.75-1.25). */
export const MIN_VOICE_SPEED = 0.75;
export const MAX_VOICE_SPEED = 1.25;

export interface MeditationRequestPayload {
  theme: MeditationThemeId;
  duration_seconds: number;
  voice_speed: number;
  language: MeditationLanguage;
  /** The caller's local hour; absent = the server uses its own clock. */
  hour_of_day: number;
  context: MeditationContext;
  eyes_open: boolean;
  preferences: PreferenceTag[];
  seed?: number;
}

export type SectionRole =
  | "preface"
  | "stop"
  | "eyes"
  | "opening"
  | "breath"
  | "passage"
  | "closing";
export type SectionSource = "curated" | "llm";

export interface ScriptSection {
  id: string;
  role: SectionRole;
  text: string;
  source: SectionSource;
  pause_before_ms: number;
  start_s: number;
  end_s: number;
}

export interface ScriptSafety {
  safety_preface: boolean;
  stop_cue: boolean;
  eyes_open: boolean;
  driving_warning: boolean;
  eyes_closed_allowed: boolean;
}

export interface MeditationScript {
  version: 1;
  id: string;
  /** TTS cache key and the library's identity for favourites. */
  content_hash: string;
  theme: MeditationThemeId;
  resolved_theme: ResolvedThemeId;
  language: MeditationLanguage;
  duration_seconds: number;
  voice_speed: number;
  context: MeditationContext;
  eyes_open: boolean;
  preferences: string[];
  seed: number;
  word_count: number;
  estimated_duration_s: number;
  sections: ScriptSection[];
  transcript: string;
  safety: ScriptSafety;
}

export interface ThemeInfo {
  id: ResolvedThemeId;
  label: string;
  hint: string;
  best_time: string;
}

export interface ThemesResponse {
  themes: ThemeInfo[];
  preference_tags: string[];
}

export interface Caption {
  index: number;
  section_id: string;
  start_s: number;
  end_s: number;
  text: string;
}

export interface CaptionsResponse {
  content_hash: string;
  transcript: string;
  duration_seconds: number;
  timing_basis: "estimated";
  provider: string;
  cache: "hit" | "miss";
  captions: Caption[];
}

/** A library entry: the whole script plus what the person chose to pair
 * with it. The server stores nothing; this is the local library. */
export interface LibraryEntry {
  saved_at: string;
  label: string;
  palette_id: string;
  script: MeditationScript;
}
