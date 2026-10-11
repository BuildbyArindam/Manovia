/**
 * Pure helpers for word counting, client-side search, and Markdown export.
 *
 * Why search lives here in the browser:
 * Journal titles and bodies are encrypted at rest with per-user AES-256-GCM keys
 * and random 96-bit nonces. SQL `LIKE` or full-text indexing on ciphertext is
 * impossible without decrypting every row on the server. Instead, the browser
 * fetches the user's decrypted entries and filters them locally in memory.
 */

import type { JournalEntry } from "@/features/journal/endpoints";

const WORD_PATTERN = /\S+/g;

/** Count whitespace-delimited words in a string. */
export function countWords(text: string): number {
  const matches = text.trim().match(WORD_PATTERN);
  return matches === null ? 0 : matches.length;
}

/** Filter fetched journal entries in browser memory by a search query. */
export function filterEntriesClientSide(
  entries: readonly JournalEntry[],
  rawQuery: string,
): JournalEntry[] {
  const query = rawQuery.trim().toLowerCase();
  if (query === "") {
    return [...entries];
  }
  return entries.filter((entry) => {
    const titleMatch = entry.title.toLowerCase().includes(query);
    const bodyMatch = entry.body.toLowerCase().includes(query);
    const templateMatch = (entry.template ?? "").toLowerCase().includes(query);
    const emotionMatch = (entry.metadata?.primary_emotion ?? "").toLowerCase().includes(query);
    return titleMatch || bodyMatch || templateMatch || emotionMatch;
  });
}

export interface MarkdownExportDraft {
  title: string;
  body: string;
  template?: string | null;
  prompt_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

/** Render a single journal entry (or active editor draft) as clean Markdown. */
export function formatEntryToMarkdown(entry: MarkdownExportDraft): string {
  const heading = entry.title.trim() !== "" ? entry.title.trim() : "Untitled reflection";
  const created = entry.created_at
    ? new Date(entry.created_at).toISOString()
    : new Date().toISOString();
  const templateLabel = entry.template ?? "free";
  const words = countWords(`${entry.title} ${entry.body}`);

  const lines: string[] = [
    "---",
    `title: ${JSON.stringify(heading)}`,
    `date: ${created}`,
    `template: ${templateLabel}`,
    `words: ${words}`,
  ];
  if (entry.prompt_id) {
    lines.push(`prompt_id: ${entry.prompt_id}`);
  }
  lines.push("---", "", `# ${heading}`, "", entry.body.trim(), "");
  return lines.join("\n");
}

/** Render a collection of journal entries into a single portable Markdown file. */
export function formatJournalCollectionToMarkdown(entries: readonly JournalEntry[]): string {
  if (entries.length === 0) {
    return "# Manovia Journal Export\n\n_No entries exported._\n";
  }
  const sections = entries.map((entry) => formatEntryToMarkdown(entry).trim());
  return [
    `# Manovia Journal Export (${entries.length} ${entries.length === 1 ? "entry" : "entries"})`,
    "",
    ...sections,
    "",
  ].join("\n\n");
}

/** Trigger a client-side `.md` download when running in a browser with Blob support. */
export function triggerMarkdownDownload(filename: string, markdown: string): boolean {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return false;
  }
  if (typeof navigator !== "undefined" && /jsdom/i.test(navigator.userAgent)) {
    return false;
  }
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
    return false;
  }
  try {
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    if (typeof URL.revokeObjectURL === "function") {
      URL.revokeObjectURL(url);
    }
    return true;
  } catch {
    return false;
  }
}
