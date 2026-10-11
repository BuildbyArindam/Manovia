/**
 * The mood page end to end, against a fake transport.
 *
 * Four behaviours get their own test because each one is a promise the product
 * makes: help arrives without the check-in being taken away, a failed save keeps
 * the draft, deleting a check-in is two steps and only then irreversible, and the
 * streak line is the forgiving one in every state the server can report.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@/lib/api";
import { createQueryClient } from "@/lib/queryClient";
import { makeMoodFake, moodEntry, moodStreak } from "@/test/moodFakes";
import { OnboardingProvider } from "@/onboarding/OnboardingProvider";
import { ThemeProvider } from "@/theme/ThemeProvider";

import { MoodView } from "./MoodView";
import type { MoodCrisis } from "./types";

const CRISIS: MoodCrisis = {
  level: "high",
  stored_level: "crisis",
  source: "rules",
  matched_categories: ["suicidal_ideation"],
  rationale_codes: ["si.kill_myself"],
  about_someone_else: false,
  locale: "en",
  region: "DEFAULT",
  message: {
    template_id: "crisis.high",
    locale: "en",
    title: "You matter, and you deserve support right now",
    body: ["What you just wrote matters, and I'm glad you did."],
    helpline_intro: "Free, confidential support — call or text:",
    emergency_instruction: "If you are in immediate danger, call the emergency number.",
    trusted_person: "Is there someone you could tell?",
    safety_steps: [],
    closing: null,
    disclaimer: null,
  },
  emergency: null,
  resources: [
    {
      id: "in-aasra",
      region: "IN",
      kind: "crisis_line",
      name: "AASRA",
      number: "+91 98204 66726",
      type: "call",
      text_keyword: null,
      instructions: null,
      hours: "24 hours",
      languages: ["en", "hi"],
      audience: null,
      description: "A helpline for anyone in distress.",
      url: null,
      source_url: "https://example.test/aasra",
      last_verified: "2026-10-09",
      needs_verification: false,
      verification_note: null,
      priority: 10,
      tel_href: "tel:+919820466726",
      sms_href: null,
    },
  ],
  check_in_saved: true,
};

function renderMoodView(client: ReturnType<typeof makeMoodFake>["client"]) {
  return render(
    <ThemeProvider>
      <QueryClientProvider client={createQueryClient()}>
        <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <OnboardingProvider>
            <MoodView client={client} />
          </OnboardingProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

async function completeCheckIn(): Promise<void> {
  const user = userEvent.setup();
  await user.click(screen.getByRole("radio", { name: "Very low" }));
  await user.click(screen.getByRole("checkbox", { name: "Health" }));
  await user.click(screen.getByRole("button", { name: "Save check-in" }));
}

describe("MoodView — a check-in that needs help", () => {
  it("shows the crisis card and says the check-in was saved anyway", async () => {
    const fake = makeMoodFake({ crisis: CRISIS });
    renderMoodView(fake.client);

    await completeCheckIn();

    const confirmation = await screen.findByTestId("mood-confirmation");
    expect(within(confirmation).getByText(/Your check-in is saved/)).toBeInTheDocument();

    const card = await screen.findByTestId("mood-crisis-card");
    expect(within(card).getByText(CRISIS.message?.title ?? "")).toBeInTheDocument();
    // The helpline is reachable in one tap, and labelled with its number.
    const link = within(card).getAllByRole("link", { name: /AASRA|91 98204/ })[0];
    expect(link).toHaveAttribute("href", "tel:+919820466726");
    expect(screen.getByText(/not a crisis service/i)).toBeInTheDocument();

    // And the check-in itself is in the history: help never costs the record.
    expect(await screen.findByTestId("mood-history-entry")).toBeInTheDocument();
  });

  it("does not show any crisis UI for an ordinary check-in", async () => {
    const fake = makeMoodFake();
    renderMoodView(fake.client);

    await completeCheckIn();

    await screen.findByTestId("mood-confirmation");
    expect(screen.queryByTestId("mood-crisis-card")).not.toBeInTheDocument();
    // The small action is still offered.
    expect(screen.getByTestId("mood-suggested-action")).toBeInTheDocument();
  });
});

describe("MoodView — when saving fails", () => {
  it("keeps the draft on screen and explains what happened", async () => {
    const fake = makeMoodFake({
      failCheckIn: new ApiError(503, "safety_unavailable", "Nothing was saved.", "req-1"),
    });
    renderMoodView(fake.client);

    await completeCheckIn();

    const error = await screen.findByTestId("mood-save-error");
    expect(error).toHaveTextContent("That did not save.");
    // Still on the form, with the choices intact — nothing was lost.
    expect(screen.getByTestId("mood-check-in-form")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Very low" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Health" })).toBeChecked();
  });

  it("explains a consent problem in plain words", async () => {
    const fake = makeMoodFake({
      failCheckIn: new ApiError(403, "consent_required", "Consent required: terms.", "req-2"),
    });
    renderMoodView(fake.client);

    await completeCheckIn();

    expect(await screen.findByTestId("mood-save-error")).toHaveTextContent(/data agreements/);
  });
});

describe("MoodView — history and deletion", () => {
  it("lists past check-ins with their words, and deletes only after confirming", async () => {
    const user = userEvent.setup();
    const fake = makeMoodFake({
      history: [
        moodEntry({
          id: "entry-a",
          local_date: "2026-03-14",
          valence: 2,
          energy: 4,
          emotions: ["sadness"],
          factors: ["work"],
          note: "Long day.",
        }),
      ],
    });
    renderMoodView(fake.client);

    const entry = await screen.findByTestId("mood-history-entry");
    expect(within(entry).getByText(/Sadness/)).toBeInTheDocument();
    expect(within(entry).getByText(/Work/)).toBeInTheDocument();
    expect(within(entry).getByText("Long day.")).toBeInTheDocument();

    // First click only asks; nothing is deleted yet.
    await user.click(within(entry).getByRole("button", { name: /Delete the check-in/ }));
    expect(within(entry).getByText(/cannot be undone/)).toBeInTheDocument();
    expect(fake.deleted).toEqual([]);

    // Backing out leaves it alone.
    await user.click(within(entry).getByRole("button", { name: "Keep it" }));
    expect(fake.deleted).toEqual([]);
    expect(screen.getByTestId("mood-history-entry")).toBeInTheDocument();

    // Confirming deletes, and the list updates without a reload.
    await user.click(screen.getByRole("button", { name: /Delete the check-in/ }));
    await user.click(screen.getByRole("button", { name: "Yes, delete it" }));

    await waitFor(() => {
      expect(fake.deleted).toEqual(["entry-a"]);
    });
    expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument();
  });
});

describe("MoodView — the streak, in every state the server can report", () => {
  it("welcomes somebody back without mentioning anything lost", async () => {
    const fake = makeMoodFake({
      streak: moodStreak({
        state: "returning",
        current_streak: 0,
        longest_streak: 6,
        checked_in_today: false,
        last_check_in: "2026-03-09",
        days_since_last_check_in: 6,
        total_check_ins: 11,
        freezes_used: 1,
        freeze_available: true,
      }),
    });
    renderMoodView(fake.client);

    const streak = await screen.findByTestId("mood-streak");
    expect(await within(streak).findByTestId("mood-streak-headline")).toHaveTextContent("Welcome back.");
    expect(within(streak).getByTestId("mood-streak-detail")).toHaveTextContent(
      /starting again counts the same/,
    );
    expect(streak.textContent ?? "").not.toMatch(/lost|broke|missed your/i);
  });

  it("says a run is running without making it a target", async () => {
    const fake = makeMoodFake({
      streak: moodStreak({ state: "today", current_streak: 14, longest_streak: 14, total_check_ins: 40 }),
    });
    renderMoodView(fake.client);

    const streak = await screen.findByTestId("mood-streak");
    expect(await within(streak).findByTestId("mood-streak-headline")).toHaveTextContent(
      "That is 14 days running.",
    );
    expect(streak.textContent ?? "").not.toMatch(/don't break|keep it up/i);
  });

  it("explains that a run is still open when today is empty", async () => {
    const fake = makeMoodFake({
      streak: moodStreak({
        state: "open",
        current_streak: 4,
        checked_in_today: false,
        last_check_in: "2026-03-14",
        days_since_last_check_in: 1,
      }),
    });
    renderMoodView(fake.client);

    const streak = await screen.findByTestId("mood-streak");
    expect(await within(streak).findByTestId("mood-streak-headline")).toHaveTextContent(
      "Today is still open.",
    );
    expect(within(streak).getByTestId("mood-streak-detail")).toHaveTextContent(/nothing resets/);
  });

  it("starts at day one without a lecture", async () => {
    const fake = makeMoodFake({
      streak: moodStreak({
        state: "never",
        current_streak: 0,
        longest_streak: 0,
        checked_in_today: false,
        last_check_in: null,
        days_since_last_check_in: null,
        total_check_ins: 0,
      }),
    });
    renderMoodView(fake.client);

    const streak = await screen.findByTestId("mood-streak");
    expect(await within(streak).findByTestId("mood-streak-headline")).toHaveTextContent("day one");
  });
});

describe("MoodView — accessibility and the reminder", () => {
  it("has no axe violations on a fresh page", async () => {
    const fake = makeMoodFake();
    const { container } = renderMoodView(fake.client);

    expect(await axe(container)).toHaveNoViolations();
  });

  it("keeps the reminder on the device and says nothing is sent", async () => {
    const user = userEvent.setup();
    const fake = makeMoodFake();
    renderMoodView(fake.client);

    const reminder = screen.getByTestId("mood-reminder");
    await user.click(within(reminder).getByRole("checkbox", { name: "Remember to remind me" }));
    // jsdom has no time-input key handling; the browser fires exactly this change
    // event when a time is chosen (the Playwright spec drives the real control).
    fireEvent.change(within(reminder).getByLabelText("Remind me at"), {
      target: { value: "07:30" },
    });

    expect(within(reminder).getByTestId("mood-reminder-status")).toHaveTextContent(
      "No reminders are sent yet",
    );
    expect(window.localStorage.getItem("manovia.mood.reminder.v1")).toBe(
      JSON.stringify({ enabled: true, time: "07:30" }),
    );
  });
});
