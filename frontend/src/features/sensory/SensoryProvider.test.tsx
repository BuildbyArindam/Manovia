import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";

import { stubMatchMedia } from "@/test/matchMedia";

import { SensoryProvider, useSensory } from "./SensoryProvider";
import { FONT_SCALE, OVERWHELMED_STORAGE_KEY, SENSORY_STORAGE_KEY } from "./types";

function Probe(): ReactElement {
  const { profile, motion, sound, density, overwhelmed, setOption, setOverwhelmed } = useSensory();
  return (
    <div>
      <p data-testid="state">
        {`${profile.motion}/${motion};${sound};${density};${profile.fontSize};${
          overwhelmed ? "on" : "off"
        }`}
      </p>
      <button
        type="button"
        onClick={() => {
          setOption("motion", "none");
        }}
      >
        no motion
      </button>
      <button
        type="button"
        onClick={() => {
          setOption("sound", "off");
        }}
      >
        no sound
      </button>
      <button
        type="button"
        onClick={() => {
          setOption("density", "simple");
        }}
      >
        simple
      </button>
      <button
        type="button"
        onClick={() => {
          setOption("colours", "high-contrast");
        }}
      >
        contrast
      </button>
      <button
        type="button"
        onClick={() => {
          setOption("fontSize", "larger");
        }}
      >
        larger
      </button>
      <button
        type="button"
        onClick={() => {
          setOverwhelmed(!overwhelmed);
        }}
      >
        toggle overwhelm
      </button>
    </div>
  );
}

function html(): HTMLElement {
  return document.documentElement;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.sessionStorage.clear();
  for (const attribute of [
    "data-sensory-motion",
    "data-sensory-motion-choice",
    "data-sensory-sound",
    "data-sensory-density",
    "data-sensory-colours",
    "data-sensory-font",
    "data-overwhelmed",
  ]) {
    html().removeAttribute(attribute);
  }
  html().style.removeProperty("--manovia-font-scale");
});

afterEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("the sensory provider", () => {
  it("applies the defaults everywhere", () => {
    render(
      <SensoryProvider>
        <Probe />
      </SensoryProvider>,
    );
    expect(screen.getByTestId("state")).toHaveTextContent("normal/normal;low;standard;normal;off");
    expect(html().getAttribute("data-sensory-motion")).toBe("normal");
    expect(html().getAttribute("data-sensory-motion-choice")).toBe("normal");
    expect(html().getAttribute("data-sensory-sound")).toBe("low");
    expect(html().getAttribute("data-sensory-density")).toBe("standard");
    expect(html().getAttribute("data-sensory-colours")).toBe("calm");
    expect(html().getAttribute("data-overwhelmed")).toBeNull();
    expect(html().style.getPropertyValue("--manovia-font-scale")).toBe(String(FONT_SCALE.normal));
  });

  it("applies and remembers every choice", () => {
    render(
      <SensoryProvider>
        <Probe />
      </SensoryProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "no motion" }));
    fireEvent.click(screen.getByRole("button", { name: "no sound" }));
    fireEvent.click(screen.getByRole("button", { name: "simple" }));
    fireEvent.click(screen.getByRole("button", { name: "contrast" }));
    fireEvent.click(screen.getByRole("button", { name: "larger" }));

    expect(html().getAttribute("data-sensory-motion")).toBe("none");
    expect(html().getAttribute("data-sensory-sound")).toBe("off");
    expect(html().getAttribute("data-sensory-density")).toBe("simple");
    expect(html().getAttribute("data-sensory-colours")).toBe("high-contrast");
    expect(html().style.getPropertyValue("--manovia-font-scale")).toBe(String(FONT_SCALE.larger));
    expect(window.localStorage.getItem(SENSORY_STORAGE_KEY)).toContain('"fontSize":"larger"');
  });

  it("never overrides the operating system's reduced-motion request", () => {
    stubMatchMedia(true);
    render(
      <SensoryProvider>
        <Probe />
      </SensoryProvider>,
    );
    expect(html().getAttribute("data-sensory-motion")).toBe("none");
    // The stored choice is still visible to Settings, honestly.
    expect(html().getAttribute("data-sensory-motion-choice")).toBe("normal");
    expect(screen.getByTestId("state")).toHaveTextContent("normal/none");
  });

  it("forces the simplest settings while overwhelmed, and restores them after", () => {
    render(
      <SensoryProvider>
        <Probe />
      </SensoryProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "toggle overwhelm" }));

    expect(html().getAttribute("data-overwhelmed")).toBe("true");
    expect(html().getAttribute("data-sensory-motion")).toBe("none");
    expect(html().getAttribute("data-sensory-sound")).toBe("off");
    expect(html().getAttribute("data-sensory-density")).toBe("simple");
    expect(window.sessionStorage.getItem(OVERWHELMED_STORAGE_KEY)).toBe("1");
    // The stored profile is untouched.
    expect(screen.getByTestId("state")).toHaveTextContent("normal/none;off;simple;normal;on");

    fireEvent.click(screen.getByRole("button", { name: "toggle overwhelm" }));
    expect(html().hasAttribute("data-overwhelmed")).toBe(false);
    expect(html().getAttribute("data-sensory-motion")).toBe("normal");
    expect(html().getAttribute("data-sensory-sound")).toBe("low");
    expect(html().getAttribute("data-sensory-density")).toBe("standard");
  });

  it("keeps overwhelmed mode for the tab, not forever", () => {
    const first = render(
      <SensoryProvider>
        <Probe />
      </SensoryProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "toggle overwhelm" }));
    first.unmount();

    render(
      <SensoryProvider>
        <Probe />
      </SensoryProvider>,
    );
    expect(screen.getByTestId("state")).toHaveTextContent("on");
  });

  it("repairs a corrupt stored profile and still paints", () => {
    window.localStorage.setItem(SENSORY_STORAGE_KEY, "{bad json");
    render(
      <SensoryProvider>
        <Probe />
      </SensoryProvider>,
    );
    expect(screen.getByTestId("state")).toHaveTextContent("normal/normal;low;standard;normal;off");
  });

  it("does not throw when used outside a provider", () => {
    render(<Probe />);
    expect(screen.getByTestId("state")).toHaveTextContent("normal/normal;low;standard;normal;off");
    act(() => {
      screen.getByRole("button", { name: "no motion" }).click();
    });
    expect(html().getAttribute("data-sensory-motion")).toBeNull();
  });
});
