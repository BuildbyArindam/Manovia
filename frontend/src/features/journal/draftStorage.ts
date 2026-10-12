/**
 * Local-only persistence for unsaved journal drafts (Day 25).
 *
 * The journal editor's words are personal. When a person is writing offline —
 * or closes the tab before an autosave completes — keeping the unsaved draft in
 * `localStorage` (`manovia.journal.draft.v1`) prevents data loss without sending
 * unfinished thoughts over the network. As soon as the entry is saved to the
 * encrypted backend (or the person explicitly discards the local draft), the
 * local copy is removed.
 */

import type { JournalTemplateId } from "./endpoints";

export const JOURNAL_DRAFT_STORAGE_KEY = "manovia.journal.draft.v1";

export interface LocalJournalDraft {
  activeId: string | null;
  title: string;
  body: string;
  template: JournalTemplateId;
  promptId: string | null;
  storeEmotions: boolean;
  updatedAt: string;
}

function isTemplateId(value: unknown): value is JournalTemplateId {
  return value === "free" || value === "gratitude" || value === "worry_dump";
}

function isLocalJournalDraft(value: unknown): value is LocalJournalDraft {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    (record.activeId === null || typeof record.activeId === "string") &&
    typeof record.title === "string" &&
    typeof record.body === "string" &&
    isTemplateId(record.template) &&
    (record.promptId === null || typeof record.promptId === "string") &&
    typeof record.storeEmotions === "boolean" &&
    typeof record.updatedAt === "string"
  );
}

export function loadJournalDraft(
  storage: Storage = window.localStorage,
): LocalJournalDraft | null {
  try {
    const raw = storage.getItem(JOURNAL_DRAFT_STORAGE_KEY);
    if (raw === null) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    if (!isLocalJournalDraft(parsed)) {
      return null;
    }
    if (parsed.title.trim() === "" && parsed.body.trim() === "") {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function saveJournalDraft(
  draft: Omit<LocalJournalDraft, "updatedAt">,
  storage: Storage = window.localStorage,
): void {
  try {
    if (draft.title.trim() === "" && draft.body.trim() === "") {
      storage.removeItem(JOURNAL_DRAFT_STORAGE_KEY);
      return;
    }
    const record: LocalJournalDraft = {
      ...draft,
      updatedAt: new Date().toISOString(),
    };
    storage.setItem(JOURNAL_DRAFT_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Ignore storage quota or private-mode restrictions.
  }
}

export function clearJournalDraft(storage: Storage = window.localStorage): void {
  try {
    storage.removeItem(JOURNAL_DRAFT_STORAGE_KEY);
  } catch {
    // Ignore storage errors.
  }
}
