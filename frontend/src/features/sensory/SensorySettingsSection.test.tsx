import { fireEvent, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { renderWithProviders } from "@/test/utils";

import { SensorySettingsSection } from "./SensorySettingsSection";
import { DEFAULT_SENSORY_PROFILE, SENSORY_STORAGE_KEY } from "./types";

function html(): HTMLElement {
  return document.documentElement;
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("Settings → Sensory profile", () => {
  it("shows every choice with a plain-language hint", () => {
    renderWithProviders(<SensorySettingsSection />);
    for (const name of [
      /^As designed/,
      /^Low/,
      /^None/,
      /^Off/,
      /^Standard/,
      /^Simple/,
      /^Calm/,
      /^High contrast/,
      /^Normal/,
      /^Large/,
      /^Larger/,
    ]) {
      expect(screen.getAllByRole("radio", { name }).length).toBeGreaterThan(0);
    }
    expect(screen.getByTestId("sensory-effective")).toHaveTextContent(
      "motion normal, sound low, detail standard",
    );
  });

  it("toggles each option and applies it immediately", () => {
    renderWithProviders(<SensorySettingsSection />);

    fireEvent.click(screen.getByRole("radio", { name: /^None/ }));
    expect(html().getAttribute("data-sensory-motion")).toBe("none");

    fireEvent.click(screen.getByRole("radio", { name: /^Off/ }));
    expect(html().getAttribute("data-sensory-sound")).toBe("off");

    fireEvent.click(screen.getByRole("radio", { name: /^Simple/ }));
    expect(html().getAttribute("data-sensory-density")).toBe("simple");

    fireEvent.click(screen.getByRole("radio", { name: /High contrast/ }));
    expect(html().getAttribute("data-sensory-colours")).toBe("high-contrast");

    fireEvent.click(screen.getByRole("radio", { name: /Larger/ }));
    expect(html().style.getPropertyValue("--manovia-font-scale")).toBe("1.25");

    expect(window.localStorage.getItem(SENSORY_STORAGE_KEY)).toContain('"motion":"none"');
    expect(screen.getByTestId("sensory-effective")).toHaveTextContent(
      "motion none, sound off, detail simple",
    );
  });

  it("resets to defaults in one step", () => {
    renderWithProviders(<SensorySettingsSection />);
    fireEvent.click(screen.getByRole("radio", { name: /^None/ }));
    fireEvent.click(screen.getByRole("radio", { name: /Larger/ }));
    fireEvent.click(screen.getByRole("button", { name: "Reset to defaults" }));

    expect(html().getAttribute("data-sensory-motion")).toBe(DEFAULT_SENSORY_PROFILE.motion);
    expect(html().getAttribute("data-sensory-font")).toBe(DEFAULT_SENSORY_PROFILE.fontSize);
    expect(screen.getByRole("radio", { name: /As designed/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Normal/ })).toBeChecked();
  });

  it("has no axe violations", async () => {
    const { container } = renderWithProviders(<SensorySettingsSection />);
    expect(await axe(container)).toHaveNoViolations();
  });
});
