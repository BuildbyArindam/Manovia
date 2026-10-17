import { api as defaultClient, type ApiClient } from "@/lib/api";

import type {
  CaptionsResponse,
  MeditationRequestPayload,
  MeditationScript,
  ThemesResponse,
} from "./types";

export const MEDITATION_ENDPOINTS = {
  themes: "/meditation/themes",
  scripts: "/meditation/scripts",
  audio: "/meditation/audio",
  captions: "/meditation/audio/captions",
} as const;

/** The reviewed catalogue. Public, like the exercise list. */
export async function fetchThemes(
  client: ApiClient = defaultClient,
): Promise<ThemesResponse> {
  return client.get<ThemesResponse>(MEDITATION_ENDPOINTS.themes, { authenticated: false });
}

/** Compose one script. Deterministic per request + seed. */
export async function createMeditationScript(
  payload: MeditationRequestPayload,
  client: ApiClient = defaultClient,
): Promise<MeditationScript> {
  return client.post<MeditationScript>(MEDITATION_ENDPOINTS.scripts, payload);
}

/**
 * Render the script locally on the server and stream the WAV back.
 *
 * Returns the raw Response so the caller can read bytes and read the cache
 * headers (X-Render-Cache / X-Content-Sha256) without a JSON body.
 */
export async function renderMeditationAudio(
  script: MeditationScript,
  client: ApiClient = defaultClient,
  signal?: AbortSignal,
): Promise<Response> {
  return client.postForStream(
    MEDITATION_ENDPOINTS.audio,
    { script },
    { headers: { Accept: "audio/wav" }, signal },
  );
}

/** The transcript and per-section captions (estimated timing, said so). */
export async function fetchMeditationCaptions(
  script: MeditationScript,
  client: ApiClient = defaultClient,
  signal?: AbortSignal,
): Promise<CaptionsResponse> {
  return client.post<CaptionsResponse>(MEDITATION_ENDPOINTS.captions, { script }, { signal });
}

/**
 * "Make me a 3-minute one for right now": auto theme from the local hour,
 * 180 s, the person's own language, and the context they said they are in.
 */
export function quickRequestForNow(
  context: MeditationRequestPayload["context"],
  language: MeditationRequestPayload["language"],
  now: () => Date = () => new Date(),
): MeditationRequestPayload {
  return {
    theme: "auto",
    duration_seconds: 180,
    voice_speed: 1.0,
    language,
    hour_of_day: now().getHours(),
    context,
    // The quick action never assumes the person is still: closed eyes are
    // only offered when they choose to sit down.
    eyes_open: context !== "seated",
    preferences: [],
  };
}
