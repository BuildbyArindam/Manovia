/**
 * Day 25: the accessibility statement, its reachability (footer, Settings, and
 * before onboarding), page titles, and the help shortcut.
 */

import { render, screen, within } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";

import { App } from "@/App";
import { CrisisHelpButton } from "@/components/CrisisHelpButton";
import { routeTitle } from "@/components/navigation";
import { saveOnboardingRecord } from "@/onboarding/storage";
import { renderWithProviders } from "@/test/utils";

import { AccessibilityPage, FEEDBACK_URL, STATEMENT_REVIEWED } from "./AccessibilityPage";

function signIn(): void {
  saveOnboardingRecord({
    version: 1,
    completedAt: "2026-10-12T08:00:00Z",
    consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: false },
    authMode: "guest",
    user: { id: "user-1", email: null, isAnonymous: true },
  });
}

describe("the accessibility statement", () => {
  it("states the aim, the measures, the checks, the limits and how to report", async () => {
    const { container } = renderWithProviders(<AccessibilityPage />, { route: "/accessibility" });

    expect(screen.getByRole("heading", { level: 1, name: "Accessibility" })).toBeInTheDocument();
    for (const heading of [
      "What we aim for",
      "What we do",
      "How we check",
      "What doesn't work well yet",
      "Tell us",
    ]) {
      expect(screen.getByRole("heading", { level: 2, name: heading })).toBeInTheDocument();
    }
    expect(screen.getByText(/WCAG\) 2\.2 at level AA/)).toBeInTheDocument();
    const limits = screen.getByRole("heading", { name: "What doesn't work well yet" })
      .parentElement as HTMLElement;
    expect(within(limits).getAllByRole("listitem").length).toBeGreaterThanOrEqual(4);
    expect(
      within(limits).getByText(/not yet been tested with disabled people/),
    ).toBeInTheDocument();

    const report = screen.getByRole("link", { name: /Report an accessibility problem on GitHub/ });
    expect(report).toHaveAttribute("href", FEEDBACK_URL);
    expect(report).toHaveAttribute("rel", "noopener noreferrer");
    expect(report).toHaveTextContent("(opens in a new tab)");
    expect(screen.getByTestId("a11y-reviewed")).toHaveTextContent("2026");
    expect(STATEMENT_REVIEWED).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    expect(await axe(container)).toHaveNoViolations();
  });

  it("can be read before onboarding, with a way back to the start", async () => {
    window.history.pushState({}, "", "/accessibility");
    render(<App />);
    expect(await screen.findByTestId("accessibility-page")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Manovia" })).toHaveAttribute("href", "/");
    window.history.pushState({}, "", "/");
  });

  it("is linked from every page's footer once onboarding is done", async () => {
    signIn();
    window.history.pushState({}, "", "/");
    render(<App />);
    const footer = await screen.findByRole("contentinfo");
    expect(within(footer).getByRole("link", { name: "Accessibility statement" })).toHaveAttribute(
      "href",
      "/accessibility",
    );
  });
});

describe("page titles (WCAG 2.4.2)", () => {
  it("names every route", () => {
    expect(routeTitle("/")).toBe("Home");
    expect(routeTitle("/mood")).toBe("Mood");
    expect(routeTitle("/exercises")).toBe("Exercises");
    expect(routeTitle("/accessibility")).toBe("Accessibility");
    expect(routeTitle("/nope")).toBe("That page does not exist");
  });

  it("sets document.title as the app navigates", async () => {
    signIn();
    window.history.pushState({}, "", "/settings");
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Settings" });
    expect(document.title).toBe("Settings · Manovia");
    window.history.pushState({}, "", "/");
  });
});

describe("the 'Need help now?' shortcut", () => {
  it("opens the helplines from ?help=1 and cleans the address", async () => {
    window.history.pushState({}, "", "/?help=1");
    renderWithProviders(<CrisisHelpButton openFromUrl />);
    expect(await screen.findByTestId("crisis-modal")).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("is ignored by the instances that did not ask for it", () => {
    window.history.pushState({}, "", "/?help=1");
    renderWithProviders(<CrisisHelpButton />);
    expect(screen.queryByTestId("crisis-modal")).not.toBeInTheDocument();
    window.history.pushState({}, "", "/");
  });
});
