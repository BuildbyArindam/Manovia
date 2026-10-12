/**
 * Day 25 integration: journal words written offline are kept on the device,
 * offered back after a reload (never applied silently), and saved — then
 * removed from the device — once the connection returns.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { JOURNAL_DRAFT_KEY, saveJournalDraft } from "@/features/journal/drafts";
import { JournalView } from "@/features/journal/JournalView";
import { ApiClient, MemoryTokenStore } from "@/lib/api";
import { resetOnline, setOnline } from "@/test/network";

interface Fake {
  client: ApiClient;
  writes: Array<{ body: string }>;
}

function journalServer(): Fake {
  const writes: Array<{ body: string }> = [];
  const tokens = new MemoryTokenStore();
  tokens.write({
    accessToken: "a",
    refreshToken: "r",
    tokenType: "bearer",
    expiresAt: Date.now() + 3_600_000,
  });
  const json = (body: unknown, status = 200): Response =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const client = new ApiClient({
    tokens,
    fetchImpl: async (url, init) => {
      if (navigator.onLine === false) {
        throw new TypeError("Failed to fetch");
      }
      const method = (init?.method ?? "GET").toUpperCase();
      const path = new URL(url, "http://localhost").pathname;
      if (method === "GET" && path === "/api/v1/journal/prompts") {
        return json({
          locale: "en",
          for_date: "2026-10-12",
          featured: null,
          prompts: [],
          templates: [],
        });
      }
      if (method === "GET" && path === "/api/v1/journal") {
        return json({ items: [], count: 0, total: 0, limit: 50, offset: 0, has_more: false });
      }
      if (method === "POST" && path === "/api/v1/journal") {
        const body = JSON.parse(String(init?.body)) as { title: string; body: string };
        writes.push({ body: body.body });
        return json(
          {
            id: "entry-1",
            title: body.title,
            body: body.body,
            template: "free",
            prompt_id: null,
            store_emotions: true,
            word_count: 3,
            created_at: "2026-10-12T09:00:00Z",
            updated_at: "2026-10-12T09:00:00Z",
            metadata: null,
            crisis: null,
          },
          201,
        );
      }
      return json({ error: { code: "not_found", message: "no", request_id: "x" } }, 404);
    },
  });
  return { client, writes };
}

afterEach(() => {
  resetOnline();
});

describe("journal drafts offline", () => {
  it("keeps offline words on the device and saves them when the connection returns", async () => {
    const fake = journalServer();
    setOnline(false);
    render(<JournalView client={fake.client} autosaveDelayMs={50} />);
    expect(await screen.findByTestId("journal-offline-note")).toBeInTheDocument();

    await userEvent.setup().type(screen.getByTestId("journal-body-input"), "Offline words.");
    await waitFor(() => {
      expect(screen.getByTestId("journal-save-status")).toHaveTextContent(
        "Offline — kept on this device",
      );
    });
    await waitFor(() => {
      expect(window.localStorage.getItem(JOURNAL_DRAFT_KEY)).toContain("Offline words.");
    });
    expect(fake.writes).toEqual([]);

    setOnline(true);
    await waitFor(() => {
      expect(fake.writes.map((write) => write.body)).toEqual(["Offline words."]);
    });
    await waitFor(() => {
      expect(window.localStorage.getItem(JOURNAL_DRAFT_KEY)).toBeNull();
    });
    expect(screen.getByTestId("journal-save-status")).toHaveTextContent("Saved");
  });

  it("offers an earlier draft back instead of applying it, and can discard it", async () => {
    saveJournalDraft({
      entryId: null,
      title: "",
      body: "Left from before.",
      template: "free",
      promptId: null,
    });
    const fake = journalServer();
    render(<JournalView client={fake.client} autosaveDelayMs={50} />);

    const prompt = await screen.findByTestId("journal-draft-restore");
    expect(prompt).toHaveTextContent("You have an unsaved draft from");
    expect(screen.getByTestId("journal-body-input")).toHaveValue("");

    await userEvent.setup().click(screen.getByRole("button", { name: "Discard draft" }));
    expect(screen.queryByTestId("journal-draft-restore")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(JOURNAL_DRAFT_KEY)).toBeNull();
    expect(fake.writes).toEqual([]);
  });

  it("restores a draft into the editor, which then saves it", async () => {
    saveJournalDraft({
      entryId: null,
      title: "",
      body: "Restore me.",
      template: "free",
      promptId: null,
    });
    const fake = journalServer();
    render(<JournalView client={fake.client} autosaveDelayMs={50} />);

    await userEvent.setup().click(await screen.findByRole("button", { name: "Restore draft" }));
    expect(screen.getByTestId("journal-body-input")).toHaveValue("Restore me.");
    await waitFor(() => {
      expect(fake.writes.map((write) => write.body)).toEqual(["Restore me."]);
    });
  });
});
