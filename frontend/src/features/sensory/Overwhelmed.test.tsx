/**
 * "I'm overwhelmed" (Day 46).
 *
 * The requirement is specific: one tap, charts and badges hidden, only
 * breathing, grounding, chat and help left. These tests assert the removal, not
 * a visual class — a chart that is merely transparent is still a chart.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { axe } from "jest-axe";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppShell } from "@/components/AppShell";
import { renderWithProviders } from "@/test/utils";
import { stubMatchMedia } from "@/test/matchMedia";

import { OverwhelmedBar } from "./OverwhelmedBar";
import { OverwhelmedButton } from "./OverwhelmedButton";
import { SensoryProvider } from "./SensoryProvider";
import { SensoryBadge, SensoryChart, SensorySecondary } from "./SensoryChrome";

function Harness(): ReactElement {
  return (
    <SensoryProvider>
      <MemoryRouter>
        <OverwhelmedButton />
        <OverwhelmedBar />
        <SensoryChart>
          <p data-testid="a-chart">a chart</p>
        </SensoryChart>
        <SensoryBadge>
          <span data-testid="a-badge">a badge</span>
        </SensoryBadge>
        <SensorySecondary>
          <p data-testid="secondary-copy">a hint</p>
        </SensorySecondary>
        <main data-testid="page-body">the page stays</main>
      </MemoryRouter>
    </SensoryProvider>
  );
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("overwhelmed mode", () => {
  it("is one tap, and lists exactly the four things that remain", () => {
    render(<Harness />);
    expect(screen.queryByTestId("overwhelmed-bar")).not.toBeInTheDocument();
    expect(screen.getByTestId("a-chart")).toBeVisible();

    fireEvent.click(screen.getByTestId("overwhelmed-toggle"));

    expect(screen.getByTestId("overwhelmed-bar")).toBeVisible();
    expect(screen.getByRole("link", { name: /Breathe/ })).toHaveAttribute("href", "/calm");
    expect(screen.getByRole("link", { name: /Ground/ })).toHaveAttribute("href", "/exercises");
    expect(screen.getByRole("link", { name: /Talk/ })).toHaveAttribute("href", "/chat");
    expect(screen.getByTestId("overwhelmed-help")).toBeVisible();
    expect(screen.getByTestId("overwhelmed-toggle")).toHaveAttribute("aria-pressed", "true");
  });

  it("removes charts, badges and secondary copy from the tree", () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId("overwhelmed-toggle"));

    expect(screen.queryByTestId("a-chart")).not.toBeInTheDocument();
    expect(screen.queryByTestId("a-badge")).not.toBeInTheDocument();
    expect(screen.queryByTestId("secondary-copy")).not.toBeInTheDocument();
    // The page the person was on is not yanked away.
    expect(screen.getByTestId("page-body")).toBeVisible();
    expect(document.documentElement.getAttribute("data-overwhelmed")).toBe("true");
  });

  it("restores everything when the person feels steadier", () => {
    render(<Harness />);
    fireEvent.click(screen.getByTestId("overwhelmed-toggle"));
    fireEvent.click(screen.getByTestId("overwhelmed-exit"));

    expect(screen.queryByTestId("overwhelmed-bar")).not.toBeInTheDocument();
    expect(screen.getByTestId("a-chart")).toBeVisible();
    expect(screen.getByTestId("a-badge")).toBeVisible();
    expect(document.documentElement.hasAttribute("data-overwhelmed")).toBe(false);
  });

  it("replaces the full navigation in the shell, and brings it back", () => {
    stubMatchMedia(true);
    renderWithProviders(
      <AppShell>
        <p>content</p>
      </AppShell>,
    );
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeVisible();

    fireEvent.click(screen.getByTestId("overwhelmed-toggle"));
    expect(screen.queryByRole("navigation", { name: "Primary" })).not.toBeInTheDocument();
    expect(screen.getByTestId("overwhelmed-bar")).toBeVisible();
    expect(screen.getByText("content")).toBeVisible();

    fireEvent.click(screen.getByTestId("overwhelmed-exit"));
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeVisible();
  });

  it("has no axe violations in overwhelmed mode", async () => {
    const { container } = render(<Harness />);
    fireEvent.click(screen.getByTestId("overwhelmed-toggle"));
    expect(await axe(container)).toHaveNoViolations();
  });
});
