/**
 * Application-level checks: the onboarding gate, the shell's landmarks, the
 * responsive navigation, and the fact that "Need help now?" is on every route.
 *
 * These run against the real `App`, so a routing or provider mistake shows up
 * here rather than in a browser.
 */

import { axe } from "jest-axe";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "@/App";
import { fetchCrisisResources } from "@/lib/endpoints";
import { ONBOARDING_STORAGE_KEY } from "@/onboarding/storage";
import { stubMatchMedia } from "@/test/matchMedia";

vi.mock("@/lib/endpoints", { spy: true });

const ROUTES: ReadonlyArray<{ path: string; heading: string }> = [
  { path: "/chat", heading: "Chat" },
  { path: "/mood", heading: "Mood" },
  { path: "/journal", heading: "Journal" },
  { path: "/exercises", heading: "Exercises" },
  { path: "/insights", heading: "Insights" },
  { path: "/settings", heading: "Settings" },
];

const CRISIS_RESOURCES = {
  last_verified: "2026-10-01",
  disclaimer: "Placeholder data.",
  resources: [
    {
      id: "us-988",
      region: "US",
      name: "988 Suicide & Crisis Lifeline",
      phone: "988",
      sms: null,
      url: null,
      hours: "24/7",
      description: "Free, confidential support.",
      priority: 1,
    },
  ],
};

function completeOnboarding(): void {
  window.localStorage.setItem(
    ONBOARDING_STORAGE_KEY,
    JSON.stringify({
      version: 1,
      completedAt: "2026-10-09T10:00:00.000Z",
      consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: false },
      authMode: "guest",
      user: { id: "user-1", email: null, isAnonymous: true },
    }),
  );
}

function renderApp(route: string): ReturnType<typeof render> {
  window.history.pushState({}, "", route);
  return render(<App />);
}

describe("App", () => {
  it("shows onboarding before the app, and has no axe violations", async () => {
    const { container } = renderApp("/chat");

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Welcome to Manovia");
    expect(screen.queryByRole("button", { name: "Need help now?" })).not.toBeInTheDocument();

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it("renders the shell's landmarks and a skip link once onboarding is done", async () => {
    completeOnboarding();
    const { container } = renderApp("/chat");

    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();

    const skip = screen.getByRole("link", { name: "Skip to main content" });
    expect(skip).toHaveAttribute("href", "#main");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main");

    // The skip link is the first thing a keyboard user reaches.
    skip.focus();
    expect(skip).toHaveFocus();

    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it("puts the help button on every route", () => {
    completeOnboarding();

    for (const route of ROUTES) {
      const { unmount } = renderApp(route.path);
      expect(screen.getByRole("button", { name: "Need help now?" })).toBeInTheDocument();
      unmount();
    }
  });

  it("renders each page under its own heading", () => {
    completeOnboarding();

    for (const route of ROUTES) {
      const { unmount } = renderApp(route.path);
      expect(screen.getByRole("heading", { level: 1, name: route.heading })).toBeInTheDocument();
      unmount();
    }
  });

  it("falls back to a not-found page for an unknown route", () => {
    completeOnboarding();

    renderApp("/does-not-exist");

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("That page does not exist");
  });

  it("uses a side navigation on desktop and a bottom bar on mobile", () => {
    completeOnboarding();
    stubMatchMedia(true);
    const desktop = renderApp("/chat");
    expect(screen.getByRole("navigation", { name: "Primary" })).toHaveClass("md:block");
    desktop.unmount();

    stubMatchMedia(false);
    renderApp("/chat");
    expect(screen.getByRole("navigation", { name: "Primary" })).toHaveClass("fixed");
  });

  it("opens the help dialog from the shell and shows the helplines", async () => {
    completeOnboarding();
    vi.mocked(fetchCrisisResources).mockResolvedValue(CRISIS_RESOURCES);
    const user = userEvent.setup();
    renderApp("/insights");

    await user.click(screen.getByRole("button", { name: "Need help now?" }));

    expect(await screen.findByRole("dialog")).toHaveAccessibleName("Need help now?");
    expect(await screen.findByText("988 Suicide & Crisis Lifeline")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 1, name: "Insights" })).toBeInTheDocument();
  });

  it("navigates between pages through the navigation", async () => {
    completeOnboarding();
    stubMatchMedia(true);
    const user = userEvent.setup();
    renderApp("/chat");

    await user.click(screen.getByRole("link", { name: /Journal/ }));

    expect(screen.getByRole("heading", { level: 1, name: "Journal" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Journal/ })).toHaveAttribute("aria-current", "page");
  });
});
