import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MockAudioContext } from "@/test/mockAudioContext";
import { renderWithProviders } from "@/test/utils";

import { MeditationView } from "./MeditationView";
import type { Caption, MeditationScript, ThemesResponse } from "./types";

/** The player builds its (detached) media element via the global Audio. */
const createdAudios: HTMLAudioElement[] = [];

const HASH = "a".repeat(64);

function fixtureScript(): MeditationScript {
  return {
    version: 1,
    id: `med_${HASH.slice(0, 8)}`,
    content_hash: HASH,
    theme: "auto",
    resolved_theme: "calm",
    language: "en",
    duration_seconds: 180,
    voice_speed: 1.0,
    context: "seated",
    eyes_open: false,
    preferences: [],
    seed: 1,
    word_count: 168,
    estimated_duration_s: 180,
    sections: [
      {
        id: "sec-0",
        role: "preface",
        text: "This is a self-guided practice, not a session with a practitioner. You can stop at any time.",
        source: "curated",
        pause_before_ms: 0,
        start_s: 0,
        end_s: 6,
      },
      {
        id: "sec-1",
        role: "stop",
        text: "If anything feels wrong, stop. There is no correct way to do this, and stopping is always allowed.",
        source: "curated",
        pause_before_ms: 600,
        start_s: 6,
        end_s: 14,
      },
      {
        id: "sec-2",
        role: "opening",
        text: "You are where you are. That is a fine place to begin.",
        source: "curated",
        pause_before_ms: 800,
        start_s: 14,
        end_s: 20,
      },
      {
        id: "sec-3",
        role: "breath",
        text: "Breathe in through the nose. Let the out-breath go a little slowly.",
        source: "curated",
        pause_before_ms: 800,
        start_s: 20,
        end_s: 28,
      },
      {
        id: "sec-4",
        role: "passage",
        text: "Notice the room as it is. Nothing needs to be fixed, arranged, or improved.",
        source: "curated",
        pause_before_ms: 1200,
        start_s: 28,
        end_s: 38,
      },
      {
        id: "sec-5",
        role: "closing",
        text: "Take one unhurried breath, and then return to your day when you are ready.",
        source: "curated",
        pause_before_ms: 1500,
        start_s: 38,
        end_s: 46,
      },
    ],
    transcript:
      "This is a self-guided practice, not a session with a practitioner. You can stop at any time.",
    safety: {
      safety_preface: true,
      stop_cue: true,
      eyes_open: false,
      driving_warning: false,
      eyes_closed_allowed: true,
    },
  };
}

function fixtureCaptions(script: MeditationScript): Caption[] {
  return script.sections.map((section, index) => ({
    index,
    section_id: section.id,
    start_s: section.start_s,
    end_s: section.end_s,
    text: section.text,
  }));
}

function themesResponse(): ThemesResponse {
  return {
    themes: [
      { id: "calm", label: "Settle down", hint: "A soft landing.", best_time: "evening" },
      { id: "sleep", label: "Wind down for sleep", hint: "Slower still.", best_time: "night" },
    ],
    preference_tags: ["breathing", "quiet", "warmth"],
  };
}

function makeClient(script: MeditationScript) {
  return {
    get: vi.fn(async (path: string) => {
      expect(path).toBe("/meditation/themes");
      return themesResponse();
    }),
    post: vi.fn(async (path: string, body: unknown) => {
      if (path === "/meditation/scripts") {
        return script;
      }
      if (path === "/meditation/audio/captions") {
        expect((body as { script: MeditationScript }).script).toBe(script);
        return {
          content_hash: script.content_hash,
          transcript: script.transcript,
          duration_seconds: script.duration_seconds,
          timing_basis: "estimated",
          provider: "fake",
          cache: "hit",
          captions: fixtureCaptions(script),
        };
      }
      throw new Error(`unexpected POST ${path}`);
    }),
    postForStream: vi.fn(async () => new Response(new Uint8Array([44, 65, 87, 80]), {
      headers: {
        "content-type": "audio/wav",
        "x-content-sha256": script.content_hash,
        "x-render-cache": "hit",
      },
    })),
  } as unknown as Parameters<typeof MeditationView>[0]["client"];
}

beforeEach(() => {
  createdAudios.length = 0;
  // jsdom's built-in `new Audio()` works, but we track the created elements
  // so a test can fire timeupdate on the player's element. jsdom forbids
  // `new HTMLAudioElement()`, hence createElement.
  vi.stubGlobal("Audio", function (this: unknown, src?: string) {
    const el = document.createElement("audio");
    if (src !== undefined) {
      el.setAttribute("src", src);
    }
    createdAudios.push(el);
    return el;
  });
  // The voice player uses a real HTMLMediaElement in the browser; jsdom has
  // no media pipeline, so play/pause resolve without throwing.
  // jsdom's media pipeline is inert (and `paused` is getter-only), so the
  // element's play/pause resolve without touching state; the player's own
  // logic is covered by audioPlayer.test.ts with a controllable fake.
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined);
  // The soundscape uses the Web Audio API; jsdom has no AudioContext, so the
  // engine builds its graph on the mocked one instead.
  vi.stubGlobal("AudioContext", MockAudioContext);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:meditation-test"),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MeditationView", () => {
  it("composes a script for right now, shows the safety preface and stop cue, plays, and stops instantly", async () => {
    const script = fixtureScript();
    renderWithProviders(<MeditationView client={makeClient(script)} />);
    const user = userEvent.setup();

    // The catalogue loads and the quick action is ready.
    await screen.findByRole("button", { name: "Make me a 3-minute one for right now" });
    const themeSelect = screen.getByLabelText("What is this for?");
    expect(themeSelect).toHaveValue("auto");
    // A real theme from the catalogue is selectable.
    await user.selectOptions(themeSelect, "calm");

    await user.click(screen.getByRole("button", { name: "Make me a 3-minute one for right now" }));

    // The composed script appears, section by section.
    const preface = await screen.findByTestId("section-preface");
    expect(preface).toHaveTextContent(/self-guided practice/);
    expect(preface).toHaveTextContent(/stop at any time/);
    expect(screen.getByTestId("section-stop")).toHaveTextContent(/stopping is always allowed/);

    // The safety badges surface the same guarantees.
    expect(screen.getByText("You can stop at any time")).toBeVisible();
    expect(screen.getByText("Self-guided — not a session with a practitioner")).toBeVisible();

    // Play with the soundscape: the render streams, the voice starts, the
    // instant stop control replaces the play controls.
    await user.click(screen.getByTestId("play-soundscape"));
    const stopNow = await screen.findByTestId("stop-now");
    expect(stopNow).toHaveTextContent("Stop now");

    // The live caption line tracks the transcript (polite live region).
    const live = screen.getByTestId("live-caption");
    expect(live).toHaveAttribute("aria-live", "polite");

    // A timeupdate advances the caption and the clock.
    const audio = createdAudios.at(-1);
    if (audio === undefined) {
      throw new Error("the player created no media element");
    }
    act(() => {
      audio.currentTime = 30;
      audio.dispatchEvent(new Event("timeupdate"));
    });
    expect(live).toHaveTextContent("Notice the room as it is.");
    expect(screen.getByTestId("meditation-controls")).toHaveTextContent("0:30");

    // Instant stop: the play controls come back, the transcript stays.
    await user.click(stopNow);
    await waitFor(() => expect(screen.getByTestId("play-soundscape")).toBeVisible());
    expect(screen.getByTestId("section-preface")).toBeVisible();
  });

  it("voice-only playback does not start the soundscape engine", async () => {
    const script = fixtureScript();
    renderWithProviders(<MeditationView client={makeClient(script)} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Make me a 3-minute one for right now" }));
    await screen.findByTestId("section-preface");

    const playVoice = screen.getByTestId("play-voice");
    await user.click(playVoice);
    await screen.findByTestId("stop-now");
    // The stop control works in voice-only mode too.
    await user.click(screen.getByTestId("stop-now"));
    await waitFor(() => expect(screen.getByTestId("play-voice")).toBeVisible());
  });

  it("saves a favourite to the local library and lists it", async () => {
    const script = fixtureScript();
    renderWithProviders(<MeditationView client={makeClient(script)} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Make me a 3-minute one for right now" }));
    await screen.findByTestId("section-preface");

    await user.type(screen.getByLabelText("Name for this one (optional)"), "Morning calm");
    await user.click(screen.getByRole("button", { name: "Save to library" }));

    const item = await screen.findByText("Morning calm", { selector: "li p" });
    expect(item).toBeVisible();
    // It persisted to localStorage (the local library survives reloads).
    const stored = JSON.parse(window.localStorage.getItem("manovia.meditations.library.v1") ?? "null");
    expect(stored?.entries?.[0]?.label).toBe("Morning calm");
    expect(stored?.entries?.[0]?.script?.content_hash).toBe(HASH);

    // And it can be removed again.
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() =>
      expect(screen.getByText(/Nothing saved yet/)).toBeVisible(),
    );
  });

  it("never offers closed eyes while driving or walking", async () => {
    renderWithProviders(<MeditationView client={makeClient(fixtureScript())} />);
    const user = userEvent.setup();

    const eyes = screen.getByLabelText("I would like the eyes-open version");
    await user.click(eyes);
    expect(eyes).toBeChecked();

    const context = screen.getByLabelText("Where are you right now?");
    await user.selectOptions(context, "driving");
    // Closed eyes are not an option on the move: the box disables.
    expect(eyes).toBeDisabled();
    expect(eyes).not.toBeChecked();
  });

  it("has no axe violations", async () => {
    const { container } = renderWithProviders(
      <MeditationView client={makeClient(fixtureScript())} />,
    );
    await screen.findByRole("button", { name: "Make me a 3-minute one for right now" });
    expect(await axe(container)).toHaveNoViolations();
  });
});
