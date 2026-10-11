import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  JournalEntry,
  JournalPromptsResponse,
  JournalWriteRequest,
} from "@/features/journal/endpoints";
import { JournalView } from "@/features/journal/JournalView";
import {
  countWords,
  filterEntriesClientSide,
  formatEntryToMarkdown,
  formatJournalCollectionToMarkdown,
} from "@/features/journal/markdown";
import type { MoodCrisis } from "@/features/mood/types";
import { ApiClient, MemoryTokenStore } from "@/lib/api";

const PROMPTS_FIXTURE: JournalPromptsResponse = {
  locale: "en",
  for_date: "2026-10-11",
  featured: {
    id: "prompt-small-anchor",
    category: "gratitude",
    template: "gratitude",
    text: "What was one quiet moment today that felt even slightly steadier than the rest?",
    hint: "It can be as small as warm tea, fresh air, or finishing one task.",
  },
  prompts: [
    {
      id: "prompt-small-anchor",
      category: "gratitude",
      template: "gratitude",
      text: "What was one quiet moment today that felt even slightly steadier than the rest?",
      hint: "It can be as small as warm tea, fresh air, or finishing one task.",
    },
    {
      id: "prompt-worry-unload",
      category: "worry_dump",
      template: "worry_dump",
      text: "If you could set down one heavy thought on this page so you don't have to carry it tonight, what would it be?",
      hint: "Don't edit or judge it — just name the worry plainly.",
    },
  ],
  templates: [
    {
      id: "free",
      label: "Free writing",
      description: "An open page with no structure — write whatever is on your mind.",
      scaffold_title: "",
      scaffold_body: "",
    },
    {
      id: "gratitude",
      label: "Gratitude",
      description: "Notice three small, concrete things that steadied or supported you today.",
      scaffold_title: "Three small anchors today",
      scaffold_body:
        "1. One small moment:\n2. Something that helped:\n3. One thing I got through:\n",
    },
    {
      id: "worry_dump",
      label: "Worry dump",
      description: "Get looping thoughts out of your head onto the page.",
      scaffold_title: "Setting down today's worries",
      scaffold_body: "What is looping in my head:\n- \nWhat is in my control:\n- \n",
    },
  ],
};

const CRISIS_FIXTURE: MoodCrisis = {
  level: "high",
  stored_level: "crisis",
  source: "rules",
  matched_categories: ["suicidal_ideation"],
  rationale_codes: ["suicidal_explicit"],
  about_someone_else: false,
  locale: "en",
  region: "IN",
  message: {
    template_id: "crisis.self.high",
    locale: "en",
    title: "You do not have to carry this alone",
    body: ["Please reach out to someone who can support you right now."],
    helpline_intro: "Free, confidential support:",
    emergency_instruction: "If you are in immediate danger, call 112.",
    trusted_person: null,
    safety_steps: [],
    closing: null,
    disclaimer: null,
  },
  emergency: null,
  resources: [
    {
      id: "kiran-in",
      region: "IN",
      kind: "crisis_line",
      name: "KIRAN Mental Health Helpline",
      number: "1800-599-0019",
      type: "call",
      text_keyword: null,
      instructions: null,
      hours: "24/7",
      languages: ["en", "hi"],
      audience: null,
      description: "24/7 toll-free mental health helpline.",
      url: null,
      source_url: "https://mohfw.gov.in",
      last_verified: "2026-10-01",
      needs_verification: false,
      verification_note: null,
      priority: 1,
      tel_href: "tel:18005990019",
      sms_href: null,
    },
  ],
  check_in_saved: true,
};

interface FakeJournalServer {
  client: ApiClient;
  entries: JournalEntry[];
  writes: Array<{ method: "POST" | "PUT"; id?: string; body: JournalWriteRequest }>;
  deletes: string[];
}

function buildFakeJournalServer(initialEntries: JournalEntry[] = []): FakeJournalServer {
  const entries: JournalEntry[] = [...initialEntries];
  const writes: Array<{ method: "POST" | "PUT"; id?: string; body: JournalWriteRequest }> = [];
  const deletes: string[] = [];
  let seq = entries.length + 1;

  const tokens = new MemoryTokenStore();
  tokens.write({
    accessToken: "test-access-token",
    refreshToken: "test-refresh-token",
    tokenType: "bearer",
    expiresAt: Date.now() + 3_600_000,
  });

  const client = new ApiClient({
    tokens,
    fetchImpl: async (url, init) => {
      const method = (init?.method ?? "GET").toUpperCase();
      const parsed = new URL(url, "http://localhost");

      if (method === "GET" && parsed.pathname === "/api/v1/journal/prompts") {
        return new Response(JSON.stringify(PROMPTS_FIXTURE), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }

      if (method === "GET" && parsed.pathname === "/api/v1/journal") {
        return new Response(
          JSON.stringify({
            items: entries,
            count: entries.length,
            total: entries.length,
            limit: 50,
            offset: 0,
            has_more: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      if (method === "POST" && parsed.pathname === "/api/v1/journal") {
        const payload = JSON.parse(String(init?.body ?? "{}")) as JournalWriteRequest;
        writes.push({ method: "POST", body: payload });
        const id = `entry-${seq++}`;
        const now = "2026-10-11T12:00:00Z";
        const title = (payload.title ?? "").trim();
        const body = payload.body.trim();
        const storeEmotions = payload.store_emotions ?? true;
        const isHighRisk = body.toLowerCase().includes("kill myself");
        const created: JournalEntry = {
          id,
          title,
          body,
          key_id: "v1",
          sentiment: storeEmotions ? 0.35 : null,
          emotion_scores: storeEmotions ? { calm: 0.7, neutral: 0.3 } : null,
          template: payload.template ?? "free",
          prompt_id: payload.prompt_id ?? null,
          word_count: countWords(`${title} ${body}`),
          store_emotions: storeEmotions,
          created_at: now,
          updated_at: now,
          metadata: {
            word_count: countWords(`${title} ${body}`),
            store_emotions: storeEmotions,
            primary_emotion: storeEmotions ? "calm" : null,
            risk_level: isHighRisk ? "high" : "none",
            stored_risk_level: isHighRisk ? 3 : 0,
            resources: isHighRisk ? CRISIS_FIXTURE.resources : [],
            crisis: isHighRisk ? CRISIS_FIXTURE : null,
          },
          crisis: isHighRisk ? CRISIS_FIXTURE : null,
        };
        entries.unshift(created);
        return new Response(JSON.stringify(created), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        });
      }

      if (method === "PUT" && parsed.pathname.startsWith("/api/v1/journal/")) {
        const id = decodeURIComponent(parsed.pathname.replace("/api/v1/journal/", ""));
        const payload = JSON.parse(String(init?.body ?? "{}")) as JournalWriteRequest;
        writes.push({ method: "PUT", id, body: payload });
        const idx = entries.findIndex((item) => item.id === id);
        if (idx === -1) {
          return new Response(
            JSON.stringify({
              error: { code: "journal_not_found", message: "Not found", request_id: "r1" },
            }),
            { status: 404, headers: { "Content-Type": "application/json" } },
          );
        }
        const existing = entries[idx]!;
        const title = (payload.title ?? "").trim();
        const body = payload.body.trim();
        const storeEmotions = payload.store_emotions ?? true;
        const updated: JournalEntry = {
          ...existing,
          title,
          body,
          template: payload.template ?? existing.template,
          prompt_id: payload.prompt_id ?? null,
          word_count: countWords(`${title} ${body}`),
          store_emotions: storeEmotions,
          sentiment: storeEmotions ? 0.45 : null,
          emotion_scores: storeEmotions ? { joy: 0.8, calm: 0.2 } : null,
          updated_at: "2026-10-11T12:05:00Z",
          metadata: {
            ...existing.metadata,
            word_count: countWords(`${title} ${body}`),
            store_emotions: storeEmotions,
            primary_emotion: storeEmotions ? "joy" : null,
          },
        };
        entries[idx] = updated;
        return new Response(JSON.stringify(updated), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }

      if (method === "DELETE" && parsed.pathname.startsWith("/api/v1/journal/")) {
        const id = decodeURIComponent(parsed.pathname.replace("/api/v1/journal/", ""));
        deletes.push(id);
        const idx = entries.findIndex((item) => item.id === id);
        if (idx !== -1) {
          entries.splice(idx, 1);
        }
        return new Response(null, { status: 204 });
      }

      return new Response("Not Found", { status: 404 });
    },
  });

  return { client, entries, writes, deletes };
}

describe("JournalView & markdown helpers", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("debounces autosave, persists across reload, and exports valid Markdown", async () => {
    const user = userEvent.setup();
    const server = buildFakeJournalServer();
    const onExport = vi.fn();

    const { unmount } = render(
      <JournalView client={server.client} autosaveDelayMs={40} onExportMarkdown={onExport} />,
    );

    await screen.findByTestId("journal-empty-state");

    const titleInput = screen.getByTestId("journal-title-input");
    const bodyInput = screen.getByTestId("journal-body-input");

    await user.type(titleInput, "Evening tea");
    await user.type(bodyInput, "Took ten slow breaths on the balcony before dinner.");

    expect(screen.getByTestId("journal-word-count")).toHaveTextContent("11 words");

    // Wait for debounced autosave to complete
    await waitFor(() => {
      expect(server.writes.length).toBeGreaterThanOrEqual(1);
      expect(screen.getByTestId("journal-save-status")).toHaveTextContent("Saved");
    });

    const lastWrite = server.writes[server.writes.length - 1]!;
    expect(lastWrite.body.title).toBe("Evening tea");
    expect(lastWrite.body.body).toBe("Took ten slow breaths on the balcony before dinner.");

    // Simulate a browser page reload by unmounting and mounting a fresh JournalView
    unmount();

    render(<JournalView client={server.client} autosaveDelayMs={40} onExportMarkdown={onExport} />);

    await waitFor(() => {
      expect(screen.getByTestId("journal-title-input")).toHaveValue("Evening tea");
      expect(screen.getByTestId("journal-body-input")).toHaveValue(
        "Took ten slow breaths on the balcony before dinner.",
      );
    });

    // Click Export to Markdown and verify the exported content
    await user.click(screen.getByTestId("journal-export-button"));
    expect(onExport).toHaveBeenCalledTimes(1);
    const [filename, markdown] = onExport.mock.calls[0] as [string, string];
    expect(filename).toBe("manovia-journal-evening-tea.md");
    expect(markdown).toContain("# Evening tea");
    expect(markdown).toContain("Took ten slow breaths on the balcony before dinner.");
    expect(screen.getByTestId("journal-export-output")).toHaveTextContent(
      "Took ten slow breaths on the balcony before dinner.",
    );
  });

  it("supports gratitude and worry_dump templates, prompt chips, and emotion opt-out", async () => {
    const user = userEvent.setup();
    const server = buildFakeJournalServer();

    render(<JournalView client={server.client} autosaveDelayMs={40} />);
    await screen.findByTestId("journal-prompts-bar");

    // Select Gratitude template on a blank editor -> populates scaffold
    await user.click(screen.getByTestId("journal-template-gratitude"));
    expect(screen.getByTestId("journal-title-input")).toHaveValue("Three small anchors today");
    expect((screen.getByTestId("journal-body-input") as HTMLTextAreaElement).value).toContain(
      "1. One small moment:",
    );

    // Toggle off emotion scoring
    const emotionToggle = screen.getByTestId("journal-store-emotions-toggle");
    expect(emotionToggle).toBeChecked();
    await user.click(emotionToggle);
    expect(emotionToggle).not.toBeChecked();

    // Click a prompt chip
    await user.click(screen.getByTestId("journal-prompt-chip-prompt-small-anchor"));
    expect(screen.getByTestId("journal-prompt-hint")).toHaveTextContent(
      "It can be as small as warm tea",
    );

    await waitFor(() => {
      const latest = server.writes[server.writes.length - 1];
      expect(latest).toBeDefined();
      expect(latest?.body.store_emotions).toBe(false);
      expect(latest?.body.prompt_id).toBe("prompt-small-anchor");
    });
  });

  it("performs client-side search, distraction-free toggle, privacy explainer, and delete with confirmation", async () => {
    const user = userEvent.setup();
    const server = buildFakeJournalServer([
      {
        id: "entry-1",
        title: "Morning walk",
        body: "Saw sparrows near the banyan tree.",
        key_id: "v1",
        sentiment: 0.4,
        emotion_scores: { calm: 0.8 },
        template: "gratitude",
        prompt_id: null,
        word_count: 8,
        store_emotions: true,
        created_at: "2026-10-10T08:00:00Z",
        updated_at: "2026-10-10T08:00:00Z",
        metadata: {
          word_count: 8,
          store_emotions: true,
          primary_emotion: "calm",
          risk_level: "none",
          stored_risk_level: 0,
          resources: [],
          crisis: null,
        },
        crisis: null,
      },
      {
        id: "entry-2",
        title: "Deadline pressure",
        body: "Too many meetings stacked back to back.",
        key_id: "v1",
        sentiment: -0.3,
        emotion_scores: { anxiety: 0.7 },
        template: "worry_dump",
        prompt_id: null,
        word_count: 9,
        store_emotions: true,
        created_at: "2026-10-11T09:00:00Z",
        updated_at: "2026-10-11T09:00:00Z",
        metadata: {
          word_count: 9,
          store_emotions: true,
          primary_emotion: "anxiety",
          risk_level: "none",
          stored_risk_level: 0,
          resources: [],
          crisis: null,
        },
        crisis: null,
      },
    ]);

    render(<JournalView client={server.client} autosaveDelayMs={500} />);
    await waitFor(() => {
      expect(screen.getAllByTestId("journal-entry-item")).toHaveLength(2);
    });

    // Expand "Private by default" explainer and verify honest threat model copy
    await user.click(screen.getByTestId("journal-privacy-toggle"));
    expect(screen.getByTestId("journal-privacy-details")).toHaveTextContent(
      "Client-side search only",
    );
    expect(screen.getByTestId("journal-privacy-details")).toHaveTextContent(
      "What is NOT protected (threat model)",
    );

    // Client-side search filters in memory
    const searchInput = screen.getByTestId("journal-search-input");
    await user.type(searchInput, "banyan");
    expect(screen.getAllByTestId("journal-entry-item")).toHaveLength(1);
    expect(screen.getByTestId("journal-entry-item")).toHaveTextContent("Morning walk");

    await user.clear(searchInput);
    expect(screen.getAllByTestId("journal-entry-item")).toHaveLength(2);

    // Distraction-free toggle hides sidebar
    await user.click(screen.getByTestId("journal-focus-toggle"));
    expect(screen.queryByTestId("journal-sidebar")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("journal-focus-toggle"));
    expect(screen.getByTestId("journal-sidebar")).toBeInTheDocument();

    // Delete with confirmation
    await user.click(screen.getByTestId("journal-delete-button"));
    const dialog = screen.getByTestId("journal-delete-confirm-dialog");
    expect(dialog).toBeInTheDocument();

    // Cancel first
    await user.click(within(dialog).getByTestId("journal-delete-cancel"));
    expect(screen.queryByTestId("journal-delete-confirm-dialog")).not.toBeInTheDocument();
    expect(server.deletes).toHaveLength(0);

    // Confirm delete
    await user.click(screen.getByTestId("journal-delete-button"));
    await user.click(screen.getByTestId("journal-delete-confirm"));

    await waitFor(() => {
      expect(server.deletes).toEqual(["entry-1"]);
      expect(screen.getAllByTestId("journal-entry-item")).toHaveLength(1);
    });
  });

  it("shows crisis resources card when safety engine flags a HIGH-risk entry", async () => {
    const user = userEvent.setup();
    const server = buildFakeJournalServer();

    render(<JournalView client={server.client} autosaveDelayMs={40} />);
    await screen.findByTestId("journal-empty-state");

    await user.type(screen.getByTestId("journal-body-input"), "I want to kill myself tonight.");

    await waitFor(() => {
      expect(screen.getByTestId("journal-crisis-card")).toBeInTheDocument();
    });
    expect(screen.getByTestId("journal-crisis-card")).toHaveTextContent(
      "KIRAN Mental Health Helpline",
    );
  });

  it("unit-tests pure markdown and client-side search helpers", () => {
    expect(countWords("   ")).toBe(0);
    expect(countWords("one two three")).toBe(3);

    const md = formatEntryToMarkdown({
      title: "",
      body: "Just a paragraph.",
      template: "worry_dump",
      prompt_id: "prompt-worry-unload",
      created_at: "2026-10-11T00:00:00Z",
    });
    expect(md).toContain("# Untitled reflection");
    expect(md).toContain("prompt_id: prompt-worry-unload");

    expect(formatJournalCollectionToMarkdown([])).toContain("_No entries exported._");
    expect(filterEntriesClientSide([], "anything")).toEqual([]);
  });
});
