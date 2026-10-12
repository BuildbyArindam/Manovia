/** Journal drafts kept on the device (Day 25): storage rules. */

import { describe, expect, it } from "vitest";

import {
  DRAFT_MAX_AGE_MS,
  JOURNAL_DRAFT_KEY,
  clearJournalDraft,
  draftDiffersFrom,
  loadJournalDraft,
  saveJournalDraft,
} from "./drafts";

const DRAFT = { entryId: null, title: "Tuesday", body: "Words.", template: "free", promptId: null };

describe("journal drafts", () => {
  it("round-trips a draft with the time it was kept", () => {
    const at = new Date("2026-10-12T09:00:00Z");
    saveJournalDraft(DRAFT, at);
    expect(loadJournalDraft(at.getTime())).toEqual({ ...DRAFT, savedAt: at.toISOString() });
  });

  it("an empty editor is no draft at all — and saving one clears the old", () => {
    saveJournalDraft(DRAFT);
    saveJournalDraft({ ...DRAFT, title: "  ", body: "\n" });
    expect(window.localStorage.getItem(JOURNAL_DRAFT_KEY)).toBeNull();
    expect(loadJournalDraft()).toBeNull();
  });

  it("forgets a draft older than 30 days", () => {
    const at = new Date("2026-09-01T09:00:00Z");
    saveJournalDraft(DRAFT, at);
    expect(loadJournalDraft(at.getTime() + DRAFT_MAX_AGE_MS + 1)).toBeNull();
    expect(window.localStorage.getItem(JOURNAL_DRAFT_KEY)).toBeNull();
  });

  it("ignores unreadable storage and clears on request", () => {
    window.localStorage.setItem(JOURNAL_DRAFT_KEY, "{broken");
    expect(loadJournalDraft()).toBeNull();
    window.localStorage.setItem(JOURNAL_DRAFT_KEY, JSON.stringify({ body: 3 }));
    expect(loadJournalDraft()).toBeNull();
    saveJournalDraft(DRAFT);
    clearJournalDraft();
    expect(window.localStorage.getItem(JOURNAL_DRAFT_KEY)).toBeNull();
  });

  it("only offers a draft back when it holds words the server copy does not", () => {
    const draft = { ...DRAFT, savedAt: "2026-10-12T09:00:00Z" };
    expect(draftDiffersFrom(draft, null)).toBe(true);
    expect(draftDiffersFrom(draft, { title: "Tuesday", body: "Words.  " })).toBe(false);
    expect(draftDiffersFrom(draft, { title: "Tuesday", body: "Fewer" })).toBe(true);
  });
});
