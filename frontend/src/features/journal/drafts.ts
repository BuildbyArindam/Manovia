/**
 * Journal drafts kept on the device (Day 25).
 *
 * The journal autosaves to the server, which is where entries are encrypted.
 * That leaves one gap: words typed while the save cannot happen — offline, the
 * server down, the tab closed before the debounce fired — exist only in the
 * text box. This module closes it with a single local draft:
 *
 * * written whenever the editor holds words the server has not confirmed;
 * * removed the moment a save succeeds (a saved entry needs no local copy);
 * * offered back — never silently applied — the next time the journal opens;
 * * **local only**: it is never sent anywhere by this module, never synced in
 *   the background, and it is deleted on sign-out and when onboarding restarts.
 *
 * It is plain text in `localStorage` until it is saved or discarded, the same
 * trust boundary as the session token the browser already holds. ADR 0025
 * records that trade-off; the editor says "kept on this device only" in words
 * where the draft is shown.
 */

export const JOURNAL_DRAFT_KEY = "manovia.journal.draft.v1";

/** A draft older than this is more likely forgotten than wanted. */
export const DRAFT_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

export interface JournalDraft {
  /** The entry being edited, or `null` for a new one. */
  entryId: string | null;
  title: string;
  body: string;
  template: string;
  promptId: string | null;
  /** ISO timestamp of the last keystroke saved here. */
  savedAt: string;
}

function isDraft(value: unknown): value is JournalDraft {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const draft = value as Record<string, unknown>;
  return (
    (draft.entryId === null || typeof draft.entryId === "string") &&
    typeof draft.title === "string" &&
    typeof draft.body === "string" &&
    typeof draft.template === "string" &&
    (draft.promptId === null || typeof draft.promptId === "string") &&
    typeof draft.savedAt === "string"
  );
}

/** The stored draft, or `null` (none, unreadable, empty, or expired). */
export function loadJournalDraft(now: number = Date.now()): JournalDraft | null {
  try {
    const raw = window.localStorage.getItem(JOURNAL_DRAFT_KEY);
    if (raw === null) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    if (!isDraft(parsed)) {
      return null;
    }
    if (parsed.body.trim() === "" && parsed.title.trim() === "") {
      return null;
    }
    const age = now - Date.parse(parsed.savedAt);
    if (!Number.isFinite(age) || age > DRAFT_MAX_AGE_MS) {
      clearJournalDraft();
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function saveJournalDraft(
  draft: Omit<JournalDraft, "savedAt">,
  now: Date = new Date(),
): void {
  if (draft.body.trim() === "" && draft.title.trim() === "") {
    clearJournalDraft();
    return;
  }
  try {
    window.localStorage.setItem(
      JOURNAL_DRAFT_KEY,
      JSON.stringify({ ...draft, savedAt: now.toISOString() } satisfies JournalDraft),
    );
  } catch {
    // Quota or blocked storage: the words are still in the editor.
  }
}

export function clearJournalDraft(): void {
  try {
    window.localStorage.removeItem(JOURNAL_DRAFT_KEY);
  } catch {
    // Nothing stored, nothing to clear.
  }
}

/**
 * True when the draft holds something the given server copy does not — i.e.
 * offering it back would actually restore words. Whitespace-only differences
 * are not worth a prompt.
 */
export function draftDiffersFrom(
  draft: JournalDraft,
  server: { title: string; body: string } | null,
): boolean {
  if (server === null) {
    return draft.body.trim() !== "" || draft.title.trim() !== "";
  }
  return draft.body.trim() !== server.body.trim() || draft.title.trim() !== server.title.trim();
}
