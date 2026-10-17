import { describe, expect, it, vi } from "vitest";

import {
  createMeditationScript,
  fetchMeditationCaptions,
  fetchThemes,
  quickRequestForNow,
  renderMeditationAudio,
} from "./endpoints";
import type { MeditationRequestPayload, MeditationScript } from "./types";

function scriptWith(hash: string): MeditationScript {
  return {
    version: 1,
    id: "med_test",
    content_hash: hash,
    theme: "auto",
    resolved_theme: "calm",
    language: "en",
    duration_seconds: 180,
    voice_speed: 1.0,
    context: "seated",
    eyes_open: false,
    preferences: [],
    seed: 7,
    word_count: 240,
    estimated_duration_s: 180,
    sections: [],
    transcript: "You can stop at any time.",
    safety: {
      safety_preface: true,
      stop_cue: true,
      eyes_open: false,
      driving_warning: false,
      eyes_closed_allowed: true,
    },
  };
}

function makeClient(impls: {
  get?: (path: string) => unknown;
  post?: (path: string, body: unknown) => unknown;
  postForStream?: (path: string, body: unknown) => Response;
}) {
  return {
    get: vi.fn(impls.get ?? (async () => ({}))),
    post: vi.fn(impls.post ?? (async () => ({}))),
    postForStream: vi.fn(impls.postForStream ?? (async () => new Response())),
  };
}

describe("meditation endpoints", () => {
  it("themes is a public GET", async () => {
    const client = makeClient({
      get: async (path: string) => {
        expect(path).toBe("/meditation/themes");
        return { themes: [], preference_tags: [] };
      },
    });
    await expect(fetchThemes(client as never)).resolves.toEqual({
      themes: [],
      preference_tags: [],
    });
    expect(client.get).toHaveBeenCalledWith(
      "/meditation/themes",
      expect.objectContaining({ authenticated: false }),
    );
  });

  it("scripts is an authenticated POST with the closed request shape", async () => {
    const payload: MeditationRequestPayload = {
      theme: "auto",
      duration_seconds: 180,
      voice_speed: 1.0,
      language: "en",
      hour_of_day: 9,
      context: "seated",
      eyes_open: false,
      preferences: [],
    };
    const client = makeClient({
      post: async (path: string, body: unknown) => {
        expect(path).toBe("/meditation/scripts");
        expect(body).toEqual(payload);
        return scriptWith("f".repeat(64));
      },
    });
    const script = await createMeditationScript(payload, client as never);
    expect(script.id).toBe("med_test");
  });

  it("audio renders via the streaming POST and exposes cache headers", async () => {
    const script = scriptWith("1".repeat(64));
    const response = new Response(new Uint8Array([44, 65, 87, 80]), {
      headers: {
        "content-type": "audio/wav",
        "x-content-sha256": script.content_hash,
        "x-render-cache": "hit",
      },
    });
    const client = makeClient({
      postForStream: (path: string, body: unknown) => {
        expect(path).toBe("/meditation/audio");
        expect((body as { script: MeditationScript }).script).toBe(script);
        return response;
      },
    });
    const out = await renderMeditationAudio(script, client as never);
    expect(out.headers.get("x-render-cache")).toBe("hit");
    expect(out.headers.get("x-content-sha256")).toBe(script.content_hash);
    expect(client.postForStream).toHaveBeenCalledWith(
      "/meditation/audio",
      { script },
      expect.objectContaining({ headers: { Accept: "audio/wav" } }),
    );
  });

  it("captions POST the same script and come back estimated", async () => {
    const script = scriptWith("2".repeat(64));
    const client = makeClient({
      post: async (path: string, body: unknown) => {
        expect(path).toBe("/meditation/audio/captions");
        expect((body as { script: MeditationScript }).script).toBe(script);
        return {
          content_hash: script.content_hash,
          transcript: script.transcript,
          duration_seconds: 180,
          timing_basis: "estimated",
          provider: "fake",
          cache: "hit",
          captions: [],
        };
      },
    });
    const out = await fetchMeditationCaptions(script, client as never);
    expect(out.timing_basis).toBe("estimated");
  });
});

describe("quickRequestForNow", () => {
  it("is a 3-minute auto-theme request at the caller's hour", () => {
    const fixed = () => new Date(2026, 9, 17, 9, 5);
    const request = quickRequestForNow("seated", "en", fixed);
    expect(request).toEqual({
      theme: "auto",
      duration_seconds: 180,
      voice_speed: 1.0,
      language: "en",
      hour_of_day: 9,
      context: "seated",
      eyes_open: false,
      preferences: [],
    });
  });

  it("never assumes stillness: walking or driving defaults to eyes open", () => {
    const driving = quickRequestForNow("driving", "bn", () => new Date());
    expect(driving.eyes_open).toBe(true);
    expect(driving.language).toBe("bn");
    const walking = quickRequestForNow("walking", "hi", () => new Date());
    expect(walking.eyes_open).toBe(true);
  });
});
