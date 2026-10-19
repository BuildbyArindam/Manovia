/**
 * The calm space UI, its fallback and its comfort prompts (Day 46).
 *
 * The browser here cannot do WebXR, WebGL or audio, which is the point: the
 * fallback is the path this sandbox can exercise end to end. The XR path is
 * covered against a fake session in `xr.test.ts`.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { stubMatchMedia } from "@/test/matchMedia";

import { BREAK_AFTER_MS, CalmSpace } from "./CalmSpace";
import { drawCalmFrame } from "./render2d";
import { SensoryProvider } from "@/features/sensory/SensoryProvider";
import { SENSORY_STORAGE_KEY } from "@/features/sensory/types";

vi.mock("./render2d", async (importOriginal) => {
  const actual = (await importOriginal()) as { drawCalmFrame: unknown };
  return { ...actual, drawCalmFrame: vi.fn() };
});

const draw = vi.mocked(drawCalmFrame);

function stubCanvas(): void {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => ({}) as never);
}

function renderCalm(): ReturnType<typeof render> {
  return render(
    <SensoryProvider>
      <MemoryRouter>
        <CalmSpace />
      </MemoryRouter>
    </SensoryProvider>,
  );
}

function removeXR(): void {
  Reflect.deleteProperty(navigator, "xr");
}

beforeEach(() => {
  draw.mockClear();
  window.localStorage.clear();
  window.sessionStorage.clear();
  stubCanvas();
  removeXR();
});

afterEach(() => {
  vi.useRealTimers();
  window.localStorage.clear();
  window.sessionStorage.clear();
  removeXR();
});

describe("the calm space without WebXR", () => {
  it("says it is the 2D space and offers no VR button", async () => {
    renderCalm();
    expect(await screen.findByTestId("calm-xr-fallback")).toHaveTextContent(
      "VR isn't available in this browser",
    );
    expect(screen.queryByTestId("calm-enter-vr")).not.toBeInTheDocument();
    expect(screen.getByTestId("calm-canvas")).toBeInTheDocument();
  });

  it("offers VR only when the browser answers that immersive-vr is supported", async () => {
    Object.defineProperty(navigator, "xr", {
      configurable: true,
      value: { isSessionSupported: async () => true, requestSession: async () => ({}) },
    });
    renderCalm();
    expect(await screen.findByTestId("calm-enter-vr")).toBeVisible();
    expect(screen.queryByTestId("calm-xr-fallback")).not.toBeInTheDocument();
  });

  it("does not offer VR when detection rejects", async () => {
    Object.defineProperty(navigator, "xr", {
      configurable: true,
      value: {
        isSessionSupported: async () => {
          throw new Error("no secure context");
        },
        requestSession: async () => ({}),
      },
    });
    renderCalm();
    expect(await screen.findByTestId("calm-xr-fallback")).toBeVisible();
    expect(screen.queryByTestId("calm-enter-vr")).not.toBeInTheDocument();
  });
});

describe("comfort and sensory settings", () => {
  it("draws one static frame and no orb under prefers-reduced-motion", async () => {
    stubMatchMedia(true);
    renderCalm();
    await screen.findByTestId("calm-xr-fallback");
    expect(screen.getByTestId("calm-static-note")).toBeVisible();
    expect(draw).toHaveBeenCalled();
    for (const call of draw.mock.calls) {
      expect(call[1].animateOrb).toBe(false);
    }
  });

  it("keeps the sound control disabled when the profile says sound is off", async () => {
    window.localStorage.setItem(SENSORY_STORAGE_KEY, JSON.stringify({ version: 1, sound: "off" }));
    renderCalm();
    await screen.findByTestId("calm-xr-fallback");
    expect(screen.getByTestId("calm-sound")).toBeDisabled();
    expect(screen.getByTestId("calm-sound-off-note")).toBeVisible();
  });

  it("shows the VR safety note before any entry point", async () => {
    renderCalm();
    await screen.findByTestId("calm-xr-fallback");
    const section = screen.getByTestId("calm-vr-section");
    expect(section).toHaveTextContent("not recommended for everyone");
    expect(section).toHaveTextContent("pregnant");
    expect(section).toHaveTextContent("epilepsy");
    expect(section).toHaveTextContent("age limit");
    expect(section).toHaveTextContent("One controller press leaves VR");
    expect(section).toHaveTextContent("not a treatment");
  });

  it("starts, pauses and resumes", async () => {
    renderCalm();
    await screen.findByTestId("calm-xr-fallback");
    expect(screen.getByTestId("calm-phase")).toHaveTextContent("Breathe at your own pace");
    fireEvent.click(screen.getByTestId("calm-start"));
    expect(screen.getByTestId("calm-pause")).toBeVisible();
    fireEvent.click(screen.getByTestId("calm-pause"));
    expect(screen.getByTestId("calm-resume")).toBeVisible();
    fireEvent.click(screen.getByTestId("calm-resume"));
    expect(screen.getByTestId("calm-pause")).toBeVisible();
  });

  it("reminds after ten minutes, and only pauses if asked", async () => {
    vi.useFakeTimers();
    stubMatchMedia(true);
    let clock = 0;
    vi.spyOn(performance, "now").mockImplementation(() => clock);
    renderCalm();
    fireEvent.click(screen.getByTestId("calm-start"));

    clock += BREAK_AFTER_MS;
    act(() => {
      vi.advanceTimersByTime(BREAK_AFTER_MS);
    });

    const prompt = screen.getByTestId("calm-break-prompt");
    expect(prompt).toBeVisible();
    expect(prompt).toHaveTextContent("ten minutes");
    // Still running: the prompt does not end anything.
    expect(screen.getByTestId("calm-pause")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Take a break" }));
    expect(screen.queryByTestId("calm-break-prompt")).not.toBeInTheDocument();
    expect(screen.getByTestId("calm-resume")).toBeVisible();
    vi.useRealTimers();
  });

  it("has no axe violations", async () => {
    const { container } = renderCalm();
    await screen.findByTestId("calm-xr-fallback");
    expect(await axe(container)).toHaveNoViolations();
  });
});
