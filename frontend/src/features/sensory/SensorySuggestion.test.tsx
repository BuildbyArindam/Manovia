import { fireEvent, render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { HIGH_AROUSAL_THRESHOLD, recordCheckIn, suggestionDismissed } from "./arousal";
import { SensoryProvider } from "./SensoryProvider";
import { SensorySuggestion } from "./SensorySuggestion";

function seed(count: number): void {
  for (let i = 0; i < count; i += 1) {
    recordCheckIn({ valence: 1, energy: 5 }, Date.now() - i * 1000);
  }
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("the optional simpler-view offer", () => {
  it("is absent until the threshold is reached", () => {
    seed(HIGH_AROUSAL_THRESHOLD - 1);
    render(
      <SensoryProvider>
        <SensorySuggestion />
      </SensoryProvider>,
    );
    expect(screen.queryByTestId("sensory-suggestion")).not.toBeInTheDocument();
    expect(document.documentElement.hasAttribute("data-overwhelmed")).toBe(false);
  });

  it("offers, never forces: nothing changes until the person chooses", () => {
    seed(HIGH_AROUSAL_THRESHOLD);
    render(
      <SensoryProvider>
        <SensorySuggestion />
      </SensoryProvider>,
    );
    expect(screen.getByTestId("sensory-suggestion")).toBeVisible();
    expect(document.documentElement.hasAttribute("data-overwhelmed")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Use the simpler view" }));
    expect(document.documentElement.getAttribute("data-overwhelmed")).toBe("true");
  });

  it("can be dismissed, and stays dismissed", () => {
    seed(HIGH_AROUSAL_THRESHOLD);
    render(
      <SensoryProvider>
        <SensorySuggestion />
      </SensoryProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByTestId("sensory-suggestion")).not.toBeInTheDocument();
    expect(suggestionDismissed()).toBe(true);
    expect(document.documentElement.hasAttribute("data-overwhelmed")).toBe(false);
  });

  it("has no axe violations", async () => {
    seed(HIGH_AROUSAL_THRESHOLD);
    const { container } = render(
      <SensoryProvider>
        <SensorySuggestion />
      </SensoryProvider>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
