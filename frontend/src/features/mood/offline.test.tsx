/**
 * Day 25 integration: a mood check-in made offline, end to end through the
 * real components — the form, the queue, the queued confirmation, the pending
 * list, and the background sync that sends it when the connection returns.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { saveOnboardingRecord } from "@/onboarding/storage";
import { makeMoodFake, moodEntry, moodStreak } from "@/test/moodFakes";
import { resetOnline, setOnline } from "@/test/network";
import { renderWithProviders } from "@/test/utils";
import type { ApiClient } from "@/lib/api";

import { MoodView } from "./MoodView";
import { OutboxSync } from "./OutboxSync";
import { MOOD_OUTBOX_KEY, __resetOutboxSyncForTests, enqueueCheckIn, loadOutbox } from "./outbox";
import type { MoodCheckInResponse } from "./types";

function signIn(): void {
  saveOnboardingRecord({
    version: 1,
    completedAt: "2026-10-12T08:00:00Z",
    consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: false },
    authMode: "guest",
    user: { id: "user-1", email: null, isAnonymous: true },
  });
}

async function chooseFaceAndNote(note: string): Promise<void> {
  const user = userEvent.setup();
  await user.click(screen.getByRole("radio", { name: "Low" }));
  await user.type(screen.getByLabelText(/anything you want to add/i), note);
  await user.click(screen.getByRole("button", { name: "Save check-in" }));
}

beforeEach(() => {
  signIn();
});

afterEach(() => {
  resetOnline();
  __resetOutboxSyncForTests();
});

describe("checking in with no connection", () => {
  it("queues the check-in on the device and says so, with the helplines one tap away", async () => {
    const fake = makeMoodFake();
    setOnline(false);
    const { container } = renderWithProviders(<MoodView client={fake.client} />, {
      route: "/mood",
    });

    expect(screen.getByTestId("mood-offline-hint")).toBeInTheDocument();
    await chooseFaceAndNote("On the train.");

    const confirmation = await screen.findByTestId("mood-queued-confirmation");
    expect(confirmation).toHaveAttribute("role", "status");
    expect(screen.getByRole("heading", { name: "Saved on this device" })).toHaveFocus();
    expect(
      within(confirmation).getByRole("button", { name: "Need help now?" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("pending-count")).toHaveTextContent("1 check-in is waiting");
    expect(fake.sent).toEqual([]);

    const queued = loadOutbox();
    expect(queued).toHaveLength(1);
    expect(queued[0]).toMatchObject({
      status: "pending",
      userId: "user-1",
      payload: { valence: 2, note: "On the train." },
    });
    expect(await axe(container)).toHaveNoViolations();
  });

  it("queues under the same key when the request fails on the network", async () => {
    const attempts: Array<Record<string, unknown>> = [];
    const flaky = {
      get: makeMoodFake().client.get,
      post: async (_path: string, body: Record<string, unknown>) => {
        attempts.push(body);
        throw new TypeError("Failed to fetch");
      },
    } as unknown as ApiClient;
    renderWithProviders(<MoodView client={flaky} />, { route: "/mood" });

    await chooseFaceAndNote("Signal dropped.");

    expect(await screen.findByTestId("mood-queued-confirmation")).toBeInTheDocument();
    expect(attempts).toHaveLength(1);
    // The retry will reuse the attempted key, so a request that did arrive is
    // answered as a replay instead of stored twice.
    expect(loadOutbox()[0]?.clientId).toBe(attempts[0]?.client_id);
  });
});

describe("coming back online", () => {
  it("sends what was queued, once, and announces it", async () => {
    const fake = makeMoodFake();
    enqueueCheckIn(
      { valence: 2, energy: 3, emotions: [], factors: [], note: "Queued earlier." },
      { clientId: "queued-1", recordedAt: new Date().toISOString(), userId: "user-1" },
    );
    setOnline(false);
    renderWithProviders(<OutboxSync client={fake.client} />);
    expect(fake.sent).toEqual([]);

    setOnline(true);

    await waitFor(() => {
      expect(fake.sent).toHaveLength(1);
    });
    expect(fake.sent[0]).toMatchObject({ client_id: "queued-1", note: "Queued earlier." });
    expect(fake.sent[0]?.recorded_at).toEqual(expect.any(String));
    await waitFor(() => {
      expect(screen.getByTestId("outbox-announcer")).toHaveTextContent(
        "Your check-in from earlier has been sent.",
      );
    });
    expect(window.localStorage.getItem(MOOD_OUTBOX_KEY)).toBeNull();
  });

  it("shows the crisis help a synced note needed, because offline it could not", async () => {
    const crisisResponse: MoodCheckInResponse = {
      entry: moodEntry(),
      streak: moodStreak(),
      crisis: {
        level: "high",
        stored_level: "crisis",
        source: "rules",
        matched_categories: [],
        rationale_codes: [],
        about_someone_else: false,
        locale: "en",
        region: "IN",
        message: null,
        emergency: null,
        resources: [
          {
            id: "in-tele-manas",
            region: "IN",
            kind: "crisis_line",
            name: "Tele-MANAS",
            number: "14416",
            type: "call",
            text_keyword: null,
            instructions: null,
            hours: "24/7",
            languages: ["en"],
            audience: null,
            description: "Free mental health helpline.",
            url: null,
            source_url: "https://example.org",
            last_verified: "2026-10-01",
            needs_verification: false,
            verification_note: null,
            priority: 1,
            tel_href: "tel:14416",
            sms_href: null,
          },
        ],
        check_in_saved: true,
      },
    };
    const client = { post: async () => crisisResponse } as unknown as ApiClient;
    enqueueCheckIn(
      { valence: 1, energy: 1, emotions: [], factors: [], note: "a hard sentence" },
      { clientId: "queued-2", recordedAt: new Date().toISOString(), userId: "user-1" },
    );
    renderWithProviders(<OutboxSync client={client} />);

    const card = await screen.findByTestId("synced-crisis");
    expect(
      within(card).getByRole("heading", { name: "About a check-in you saved while offline" }),
    ).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /14416/ })).toHaveAttribute("href", "tel:14416");
    await userEvent.setup().click(within(card).getByRole("button", { name: "Close" }));
    expect(screen.queryByTestId("synced-crisis")).not.toBeInTheDocument();
  });

  it("lists a check-in that cannot be sent, with the reason and a way to discard it", async () => {
    enqueueCheckIn(
      { valence: 3, energy: 3, emotions: [], factors: [], note: null },
      { clientId: "old", recordedAt: "2026-01-01T09:00:00Z", userId: "user-1" },
    );
    const fake = makeMoodFake();
    // As in the app shell: the sync runs beside the page and finds it too old.
    renderWithProviders(
      <>
        <OutboxSync client={fake.client} />
        <MoodView client={fake.client} />
      </>,
      { route: "/mood" },
    );

    const failed = await screen.findByTestId("failed-check-ins");
    expect(failed).toHaveTextContent("It's more than 7 days old.");
    expect(fake.sent).toEqual([]);
    await userEvent
      .setup()
      .click(within(failed).getByRole("button", { name: /Discard the check-in from/ }));
    await waitFor(() => {
      expect(screen.queryByTestId("pending-check-ins")).not.toBeInTheDocument();
    });
    expect(loadOutbox()).toEqual([]);
  });
});
