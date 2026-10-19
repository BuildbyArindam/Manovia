/**
 * Application-level checks: the onboarding gate, the shell's landmarks, the
 * responsive navigation, and the fact that "Need help now?" is on every route.
 *
 * These run against the real `App`, so a routing or provider mistake shows up
 * here rather than in a browser.
 */

import { axe } from "jest-axe";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "@/App";
import { routeTitle } from "@/components/navigation";
import { fetchCrisisResources } from "@/lib/endpoints";
import { TEST_RESPONSE } from "@/test/crisisFixtures";
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
  // Day 47: the share builder is one of the app's own surfaces, so it gets the
  // same checks as every other page — including "Need help now?" in the header.
  { path: "/shares", heading: "Prepare for my appointment" },
];

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

  it("labels the demo as a beta portfolio demo, not a clinical service (Day 30)", () => {
    completeOnboarding();
    renderApp("/chat");

    // The header carries a visible Beta badge next to the product name.
    expect(within(screen.getByRole("banner")).getByText("Beta")).toBeInTheDocument();

    // The footer states the positioning on every page, above the existing
    // self-help disclaimer. Both stay until clinical review is complete.
    const footer = within(screen.getByRole("contentinfo"));
    expect(
      footer.getByText("Portfolio demo — not a clinical service. Clinical review is not complete."),
    ).toBeInTheDocument();
    expect(
      footer.getByText(
        "Manovia is a self-help companion — not therapy, not a diagnosis, and not a crisis service.",
      ),
    ).toBeInTheDocument();
  });

  it("puts the help button on every route", () => {
    completeOnboarding();

    for (const route of ROUTES) {
      const { unmount } = renderApp(route.path);
      // Scoped to the header: the chat page carries a second "Need help now?"
      // inside its permanent "AI companion — not a therapist" line (Day 12), and
      // that one is a chat affordance, not the shell's.
      const header = within(screen.getByRole("banner"));
      expect(header.getByRole("button", { name: "Need help now?" })).toBeInTheDocument();
      unmount();
    }
  });

  it("renders each page under its own heading", async () => {
    completeOnboarding();

    for (const route of ROUTES) {
      const { unmount } = renderApp(route.path);
      // `find`, not `get`: the insights page is lazy-loaded so its chart library
      // stays out of the main bundle, which means its heading arrives a tick
      // after the route renders.
      expect(
        await screen.findByRole("heading", { level: 1, name: route.heading }),
      ).toBeInTheDocument();
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
    vi.mocked(fetchCrisisResources).mockResolvedValue(TEST_RESPONSE);
    const user = userEvent.setup();
    renderApp("/insights");

    await user.click(screen.getByRole("button", { name: "Need help now?" }));

    expect(await screen.findByRole("dialog")).toHaveAccessibleName("Need help now?");
    expect(await screen.findByText("Test Crisis Line")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("heading", { level: 1, name: "Insights" })).toBeInTheDocument();
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

  it("renders the public share viewer with no account and no shell", async () => {
    // Deliberately *not* calling completeOnboarding(): a professional opening a
    // shared link has no Manovia account and must not be asked to make one.
    const token = "vectorToken0123456789";
    const key = "bWFub3ZpYS1zaGFyZS12ZWN0b3Itc2VjcmV0LWtleSE";
    renderApp(`/s/${token}#k=${key}`);

    expect(await screen.findByTestId("share-viewer-page")).toBeInTheDocument();
    // No onboarding flow and none of the shell's chrome: no primary navigation
    // to get lost in, and no crisis button routing a reader into an account they
    // do not have.
    expect(screen.queryByRole("heading", { name: "Welcome to Manovia" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Primary" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /need help now/i })).not.toBeInTheDocument();
    // The one banner on the page is the shared document's own header.
    expect(screen.getByRole("banner")).toHaveTextContent("A summary shared with you");
    expect(screen.queryByRole("heading", { level: 1 })).toHaveTextContent(
      "A summary shared with you",
    );
  });

  it("titles a shared document rather than calling it a missing page", () => {
    expect(routeTitle("/s/vectorToken0123456789")).toBe("A summary shared with you");
    expect(routeTitle("/shares")).toBe("Share");
  });
});
