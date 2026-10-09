/**
 * Onboarding flow.
 *
 * The headline test is the first one: the flow cannot be completed without the
 * required consents — not because a button is disabled, but because the
 * provider refuses. The second half of that is checked at the provider level
 * (`GateProbe`), which is the part a UI refactor cannot accidentally remove.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ReactElement } from "react";

import { ApiError } from "@/lib/api";
import {
  createGuestSession,
  fetchConsentRequirements,
  recordConsents,
  registerAccount,
} from "@/lib/endpoints";
import { OnboardingFlow } from "@/onboarding/OnboardingFlow";
import { useOnboarding } from "@/onboarding/OnboardingProvider";
import { NO_CONSENTS, ONBOARDING_STORAGE_KEY } from "@/onboarding/storage";
import { renderWithProviders } from "@/test/utils";

// Spy mode keeps every other export real and lets the tests below override the
// four calls the flow makes.
vi.mock("@/lib/endpoints", { spy: true });

const REQUIREMENTS = {
  documents: [
    {
      kind: "terms" as const,
      version: "2026-10-01",
      title: "Terms of use",
      summary: "The ground rules.",
    },
    {
      kind: "privacy" as const,
      version: "2026-10-01",
      title: "Privacy policy",
      summary: "What we store and why.",
    },
    {
      kind: "ai_disclosure" as const,
      version: "2026-10-01",
      title: "AI disclosure",
      summary: "Manovia is software, not a person.",
      text: "Server-supplied AI disclosure text.",
    },
    {
      kind: "store_chat" as const,
      version: "2026-10-01",
      title: "Chat history",
      summary: "Optional storage of conversations.",
    },
  ],
};

const SESSION = {
  access_token: "access-1",
  refresh_token: "refresh-1",
  token_type: "bearer",
  expires_in: 900,
  user: {
    id: "user-1",
    email: null,
    is_anonymous: true,
    language: "en",
    region: null,
    created_at: "2026-10-09T00:00:00Z",
  },
};

/** Drives `complete()` directly, to test the gate rather than the UI. */
function GateProbe({ consents }: { consents: Partial<typeof NO_CONSENTS> }): ReactElement {
  const { complete, isComplete } = useOnboarding();
  const [result, setResult] = useState("pending");

  useEffect(() => {
    void complete({ consents: { ...NO_CONSENTS, ...consents }, authMode: "guest" }).then(
      (outcome) => {
        setResult(outcome.ok ? "completed" : `refused: ${outcome.error}`);
      },
    );
  }, [complete, consents]);

  return (
    <div>
      <span data-testid="result">{result}</span>
      <span data-testid="complete">{isComplete ? "yes" : "no"}</span>
    </div>
  );
}

/** Mirrors `App`: the flow is shown until onboarding is complete. */
function OnboardingGate(): ReactElement {
  const { isComplete } = useOnboarding();
  return isComplete ? <h1>Chat</h1> : <OnboardingFlow />;
}

async function clickContinue(
  user: ReturnType<typeof userEvent.setup>,
  times: number,
): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await user.click(screen.getByRole("button", { name: "Continue" }));
  }
}

/** welcome → scope → AI disclosure. */
async function walkToAiStep(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await clickContinue(user, 2);
}

/** welcome → scope → AI disclosure → agreements. */
async function walkToConsentStep(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await clickContinue(user, 3);
}

describe("OnboardingFlow", () => {
  it("starts on the welcome step", () => {
    vi.mocked(fetchConsentRequirements).mockResolvedValue(REQUIREMENTS);

    renderWithProviders(<OnboardingFlow />);

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Welcome to Manovia");
    expect(screen.getByText(/^Step 1 of 5$/)).toBeInTheDocument();
  });

  it("cannot be completed without the required consents", async () => {
    vi.mocked(fetchConsentRequirements).mockResolvedValue(REQUIREMENTS);
    const user = userEvent.setup();
    renderWithProviders(<OnboardingFlow />);

    await walkToConsentStep(user);

    // Every agreement is separate and every one starts unticked.
    for (const label of [/Terms of use/, /Privacy policy/, /AI disclosure/, /Chat history/]) {
      expect(screen.getByLabelText(label)).not.toBeChecked();
    }

    const cont = screen.getByRole("button", { name: "Continue" });
    expect(cont).toBeDisabled();
    expect(screen.getByText(/Tick the three required agreements/)).toBeInTheDocument();

    // The optional agreement alone changes nothing.
    await user.click(screen.getByLabelText(/Chat history/));
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();

    // The three required ones do.
    await user.click(screen.getByLabelText(/Terms of use/));
    await user.click(screen.getByLabelText(/Privacy policy/));
    await user.click(screen.getByLabelText(/AI disclosure/));
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Choose how to start");
    expect(vi.mocked(createGuestSession)).not.toHaveBeenCalled();
  });

  it("refuses to complete at the provider level, whatever the UI does", async () => {
    vi.mocked(fetchConsentRequirements).mockResolvedValue(REQUIREMENTS);

    renderWithProviders(<GateProbe consents={{ terms: true, privacy: true }} />);

    expect(await screen.findByTestId("result")).toHaveTextContent(/^refused: /);
    expect(screen.getByTestId("complete")).toHaveTextContent("no");
    expect(vi.mocked(createGuestSession)).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(ONBOARDING_STORAGE_KEY)).toBeNull();
  });

  it("shows the AI disclosure from the server, and a fallback when it cannot be fetched", async () => {
    vi.mocked(fetchConsentRequirements).mockResolvedValue(REQUIREMENTS);
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(<OnboardingFlow />);

    await walkToAiStep(user);
    expect(screen.getByTestId("ai-disclosure")).toHaveTextContent(
      "Server-supplied AI disclosure text.",
    );
    unmount();

    vi.mocked(fetchConsentRequirements).mockRejectedValue(new Error("offline"));
    renderWithProviders(<OnboardingFlow />);
    await walkToAiStep(user);

    expect(screen.getByTestId("ai-disclosure")).toHaveTextContent(/AI-powered self-help companion/);
    expect(screen.getByText(/could not fetch the current disclosure/)).toBeInTheDocument();
  });

  it("records the consents and completes as a guest", async () => {
    vi.mocked(fetchConsentRequirements).mockResolvedValue(REQUIREMENTS);
    vi.mocked(createGuestSession).mockResolvedValue(SESSION);
    vi.mocked(recordConsents).mockResolvedValue({ recorded: [] });
    const user = userEvent.setup();
    renderWithProviders(<OnboardingGate />);

    await walkToConsentStep(user);
    await user.click(screen.getByLabelText(/Terms of use/));
    await user.click(screen.getByLabelText(/Privacy policy/));
    await user.click(screen.getByLabelText(/AI disclosure/));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Continue as a guest" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Chat" })).toBeInTheDocument();
    expect(vi.mocked(recordConsents)).toHaveBeenCalledWith(expect.anything(), [
      { kind: "terms", version: "2026-10-01", granted: true },
      { kind: "privacy", version: "2026-10-01", granted: true },
      { kind: "ai_disclosure", version: "2026-10-01", granted: true },
    ]);

    const stored = window.localStorage.getItem(ONBOARDING_STORAGE_KEY);
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored ?? "{}")).toMatchObject({
      version: 1,
      authMode: "guest",
      consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: false },
    });
  });

  it("creates an account when asked, and surfaces a friendly error", async () => {
    vi.mocked(fetchConsentRequirements).mockResolvedValue(REQUIREMENTS);
    vi.mocked(registerAccount).mockResolvedValue({
      ...SESSION,
      user: { ...SESSION.user, email: "a@example.com", is_anonymous: false },
    });
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(<OnboardingGate />);

    await walkToConsentStep(user);
    await user.click(screen.getByLabelText(/Terms of use/));
    await user.click(screen.getByLabelText(/Privacy policy/));
    await user.click(screen.getByLabelText(/AI disclosure/));
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.click(screen.getByRole("button", { name: "Create an account with email" }));
    await user.type(screen.getByLabelText("Email"), "a@example.com");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Create account and start" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Chat" })).toBeInTheDocument();
    expect(vi.mocked(registerAccount)).toHaveBeenCalledWith(
      { email: "a@example.com", password: "correct horse battery" },
      expect.anything(),
    );

    // A taken address reads as advice, not as a raw code. Start from a clean
    // slate: the first run wrote its record to localStorage, which is exactly
    // what a returning user should get.
    unmount();
    window.localStorage.clear();
    vi.mocked(registerAccount).mockRejectedValue(
      new ApiError(409, "email_taken", "An account with this email already exists.", "req-1"),
    );
    renderWithProviders(<OnboardingGate />);
    await walkToConsentStep(user);
    await user.click(screen.getByLabelText(/Terms of use/));
    await user.click(screen.getByLabelText(/Privacy policy/));
    await user.click(screen.getByLabelText(/AI disclosure/));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(screen.getByRole("button", { name: "Create an account with email" }));
    await user.type(screen.getByLabelText("Email"), "a@example.com");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Create account and start" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Choose how to start");
  });

  it("moves focus to the new step's heading and announces the change", async () => {
    vi.mocked(fetchConsentRequirements).mockResolvedValue(REQUIREMENTS);
    const user = userEvent.setup();
    renderWithProviders(<OnboardingFlow />);

    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByRole("heading", { level: 1 })).toHaveFocus();
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Step 2 of 5: What Manovia is"),
    );
  });

  it("can go back without losing the agreements already ticked", async () => {
    vi.mocked(fetchConsentRequirements).mockResolvedValue(REQUIREMENTS);
    const user = userEvent.setup();
    renderWithProviders(<OnboardingFlow />);

    await walkToConsentStep(user);
    await user.click(screen.getByLabelText(/Terms of use/));
    await user.click(screen.getByRole("button", { name: "Back" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByLabelText(/Terms of use/)).toBeChecked();
  });
});
