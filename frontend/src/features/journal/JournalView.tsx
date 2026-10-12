import { type ReactElement, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CrisisCard } from "@/components/CrisisCard";
import { t } from "@/lib/i18n/i18n";
import {
  createJournalEntry,
  deleteJournalEntry,
  fetchJournalPrompts,
  type JournalEntry,
  type JournalPrompt,
  type JournalTemplate,
  type JournalTemplateId,
  listJournalEntries,
  updateJournalEntry,
} from "@/features/journal/endpoints";
import {
  countWords,
  filterEntriesClientSide,
  formatEntryToMarkdown,
  formatJournalCollectionToMarkdown,
  triggerMarkdownDownload,
} from "@/features/journal/markdown";
import type { MoodCrisis } from "@/features/mood/types";
import { type ApiClient, api as defaultClient, isApiError } from "@/lib/api";

const ACTIVE_ENTRY_STORAGE_KEY = "manovia.journal.activeEntryId";
const EDITOR_MODE_STORAGE_KEY = "manovia.journal.editorMode";

export interface JournalViewProps {
  client?: ApiClient;
  /** Debounce window in milliseconds before autosaving edits. Default: 800ms. */
  autosaveDelayMs?: number;
  /** Optional callback fired whenever Markdown export runs. */
  onExportMarkdown?: (filename: string, markdown: string) => void;
}

type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

function fallbackTemplates(): readonly JournalTemplate[] {
  return [
    {
      id: "free",
      label: t("journal.templates.freeLabel"),
      description: t("journal.templates.freeDescription"),
      scaffold_title: "",
      scaffold_body: "",
    },
    {
      id: "gratitude",
      label: t("journal.templates.gratitudeLabel"),
      description: t("journal.templates.gratitudeDescription"),
      scaffold_title: t("journal.templates.gratitudeScaffoldTitle"),
      scaffold_body: t("journal.templates.gratitudeScaffoldBody"),
    },
    {
      id: "worry_dump",
      label: t("journal.templates.worryLabel"),
      description: t("journal.templates.worryDescription"),
      scaffold_title: t("journal.templates.worryScaffoldTitle"),
      scaffold_body: t("journal.templates.worryScaffoldBody"),
    },
  ];
}

function readSessionValue(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSessionValue(key: string, value: string | null): void {
  try {
    if (value === null) {
      window.sessionStorage.removeItem(key);
    } else {
      window.sessionStorage.setItem(key, value);
    }
  } catch {
    // Ignore storage errors in private mode.
  }
}

export function JournalView({
  client = defaultClient,
  autosaveDelayMs = 800,
  onExportMarkdown,
}: JournalViewProps): ReactElement {
  const [entries, setEntries] = useState<JournalEntry[]>([]);
  const [totalEntries, setTotalEntries] = useState<number>(0);
  const [hasMore, setHasMore] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const [prompts, setPrompts] = useState<JournalPrompt[]>([]);
  const [templates, setTemplates] = useState<readonly JournalTemplate[]>(() => fallbackTemplates());

  // Active editor state
  const [activeId, setActiveId] = useState<string | null>(() =>
    readSessionValue(ACTIVE_ENTRY_STORAGE_KEY),
  );
  const [title, setTitle] = useState<string>("");
  const [body, setBody] = useState<string>("");
  const [template, setTemplate] = useState<JournalTemplateId>("free");
  const [promptId, setPromptId] = useState<string | null>(null);
  const [storeEmotions, setStoreEmotions] = useState<boolean>(true);

  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [lastSavedAt, setLastSavedAt] = useState<string | null>(null);
  const [activeCrisis, setActiveCrisis] = useState<MoodCrisis | null>(null);

  // UI toggles
  const [distractionFree, setDistractionFree] = useState<boolean>(false);
  const [showPrivacyDetails, setShowPrivacyDetails] = useState<boolean>(false);
  const [confirmingDelete, setConfirmingDelete] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [exportedMarkdown, setExportedMarkdown] = useState<string | null>(null);
  const [exportedFilename, setExportedFilename] = useState<string | null>(null);

  // Track last-persisted snapshot to avoid redundant autosaves
  const lastPersistedSnapshotRef = useRef<string>("");
  const activeIdRef = useRef<string | null>(activeId);
  activeIdRef.current = activeId;
  const saveInFlightRef = useRef<boolean>(false);

  const makeSnapshot = useCallback(
    (
      id: string | null,
      t: string,
      b: string,
      tpl: JournalTemplateId,
      pid: string | null,
      emotions: boolean,
    ): string => JSON.stringify({ id, t: t.trim(), b: b.trim(), tpl, pid, emotions }),
    [],
  );

  const selectEntryIntoEditor = useCallback(
    (entry: JournalEntry) => {
      const normalizedTpl: JournalTemplateId =
        entry.template === "gratitude" || entry.template === "worry_dump" ? entry.template : "free";
      setActiveId(entry.id);
      activeIdRef.current = entry.id;
      writeSessionValue(ACTIVE_ENTRY_STORAGE_KEY, entry.id);
      writeSessionValue(EDITOR_MODE_STORAGE_KEY, "editing");
      setTitle(entry.title);
      setBody(entry.body);
      setTemplate(normalizedTpl);
      setPromptId(entry.prompt_id);
      setStoreEmotions(entry.store_emotions);
      setLastSavedAt(entry.updated_at);
      setActiveCrisis(entry.crisis ?? entry.metadata?.crisis ?? null);
      setConfirmingDelete(false);
      setSaveState("saved");
      lastPersistedSnapshotRef.current = makeSnapshot(
        entry.id,
        entry.title,
        entry.body,
        normalizedTpl,
        entry.prompt_id,
        entry.store_emotions,
      );
    },
    [makeSnapshot],
  );

  // Initial load of prompts and entries
  useEffect(() => {
    let cancelled = false;
    async function loadInitial(): Promise<void> {
      setLoading(true);
      setErrorMessage(null);
      try {
        const [listResp, promptsResp] = await Promise.all([
          listJournalEntries({ limit: 50, offset: 0 }, client),
          fetchJournalPrompts({ count: 6 }, client).catch(() => null),
        ]);
        if (cancelled) {
          return;
        }
        setEntries(listResp.items);
        setTotalEntries(listResp.total);
        setHasMore(listResp.has_more);

        if (promptsResp !== null) {
          setPrompts(promptsResp.prompts);
          if (promptsResp.templates.length > 0) {
            setTemplates(promptsResp.templates);
          }
        }

        // Restore active entry across page reloads if one exists
        const storedId = readSessionValue(ACTIVE_ENTRY_STORAGE_KEY);
        const storedMode = readSessionValue(EDITOR_MODE_STORAGE_KEY);
        const matched =
          (storedId ? listResp.items.find((item) => item.id === storedId) : undefined) ??
          (storedMode !== "new" ? listResp.items[0] : undefined);

        if (matched !== undefined) {
          selectEntryIntoEditor(matched);
        } else {
          lastPersistedSnapshotRef.current = makeSnapshot(null, "", "", "free", null, true);
        }
      } catch (err) {
        if (!cancelled) {
          setErrorMessage(
            isApiError(err) ? err.message : t("journal.loadError"),
          );
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void loadInitial();
    return () => {
      cancelled = true;
    };
  }, [client, makeSnapshot, selectEntryIntoEditor]);

  const performSave = useCallback(async (): Promise<JournalEntry | null> => {
    const trimmedBody = body.trim();
    if (trimmedBody === "" || saveInFlightRef.current) {
      return null;
    }
    saveInFlightRef.current = true;
    setSaveState("saving");
    setErrorMessage(null);

    try {
      const currentId = activeIdRef.current;
      const payload = {
        title: title.trim(),
        body: trimmedBody,
        template,
        prompt_id: promptId,
        store_emotions: storeEmotions,
      };
      const saved =
        currentId === null
          ? await createJournalEntry(payload, client)
          : await updateJournalEntry(currentId, payload, client);

      setActiveId(saved.id);
      activeIdRef.current = saved.id;
      writeSessionValue(ACTIVE_ENTRY_STORAGE_KEY, saved.id);
      writeSessionValue(EDITOR_MODE_STORAGE_KEY, "editing");
      setLastSavedAt(saved.updated_at);
      setActiveCrisis(saved.crisis ?? saved.metadata?.crisis ?? null);
      setSaveState("saved");

      lastPersistedSnapshotRef.current = makeSnapshot(
        saved.id,
        saved.title,
        saved.body,
        template,
        promptId,
        storeEmotions,
      );

      setEntries((prev) => {
        const exists = prev.some((item) => item.id === saved.id);
        if (exists) {
          return prev.map((item) => (item.id === saved.id ? saved : item));
        }
        setTotalEntries((count) => count + 1);
        return [saved, ...prev];
      });
      return saved;
    } catch (err) {
      setSaveState("error");
      setErrorMessage(isApiError(err) ? err.message : t("journal.saveError"));
      return null;
    } finally {
      saveInFlightRef.current = false;
    }
  }, [body, client, makeSnapshot, promptId, storeEmotions, template, title]);

  // Debounced autosave effect
  useEffect(() => {
    const trimmedBody = body.trim();
    if (trimmedBody === "") {
      return undefined;
    }
    const currentSnapshot = makeSnapshot(activeId, title, body, template, promptId, storeEmotions);
    if (currentSnapshot === lastPersistedSnapshotRef.current) {
      return undefined;
    }
    setSaveState("dirty");
    const timer = window.setTimeout(() => {
      void performSave();
    }, autosaveDelayMs);
    return () => {
      window.clearTimeout(timer);
    };
  }, [
    activeId,
    autosaveDelayMs,
    body,
    makeSnapshot,
    performSave,
    promptId,
    storeEmotions,
    template,
    title,
  ]);

  const handleStartNewEntry = useCallback(
    (initialTemplate: JournalTemplateId = "free") => {
      const tpl = templates.find((t) => t.id === initialTemplate);
      setActiveId(null);
      activeIdRef.current = null;
      writeSessionValue(ACTIVE_ENTRY_STORAGE_KEY, null);
      writeSessionValue(EDITOR_MODE_STORAGE_KEY, "new");
      setTemplate(initialTemplate);
      setTitle(tpl && initialTemplate !== "free" ? tpl.scaffold_title : "");
      setBody(tpl && initialTemplate !== "free" ? tpl.scaffold_body : "");
      setPromptId(null);
      setActiveCrisis(null);
      setConfirmingDelete(false);
      setSaveState("idle");
      lastPersistedSnapshotRef.current = makeSnapshot(
        null,
        "",
        "",
        initialTemplate,
        null,
        storeEmotions,
      );
    },
    [makeSnapshot, storeEmotions, templates],
  );

  const handleSelectTemplate = useCallback(
    (nextTemplate: JournalTemplateId) => {
      setTemplate(nextTemplate);
      const tpl = templates.find((t) => t.id === nextTemplate);
      if (tpl && nextTemplate !== "free" && body.trim() === "") {
        if (title.trim() === "") {
          setTitle(tpl.scaffold_title);
        }
        setBody(tpl.scaffold_body);
      }
    },
    [body, templates, title],
  );

  const handleSelectPromptChip = useCallback(
    (prompt: JournalPrompt) => {
      if (promptId === prompt.id) {
        setPromptId(null);
        return;
      }
      setPromptId(prompt.id);
      setTemplate(prompt.template);
      if (title.trim() === "") {
        setTitle(prompt.text);
      }
    },
    [promptId, title],
  );

  const handleConfirmDelete = useCallback(async () => {
    if (activeId === null) {
      return;
    }
    const idToDelete = activeId;
    try {
      await deleteJournalEntry(idToDelete, client);
      setEntries((prev) => prev.filter((item) => item.id !== idToDelete));
      setTotalEntries((count) => Math.max(0, count - 1));
      setConfirmingDelete(false);
      handleStartNewEntry("free");
    } catch (err) {
      setErrorMessage(isApiError(err) ? err.message : t("journal.deleteError"));
    }
  }, [activeId, client, handleStartNewEntry]);

  const handleExportMarkdown = useCallback(() => {
    const hasDraft = title.trim() !== "" || body.trim() !== "";
    let markdown: string;
    let filename: string;
    if (hasDraft) {
      const activeEntry = entries.find((item) => item.id === activeId);
      markdown = formatEntryToMarkdown({
        title,
        body,
        template,
        prompt_id: promptId,
        created_at: activeEntry?.created_at,
        updated_at: activeEntry?.updated_at,
      });
      const slug =
        (title.trim() || "reflection")
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "")
          .slice(0, 40) || "reflection";
      filename = `manovia-journal-${slug}.md`;
    } else {
      markdown = formatJournalCollectionToMarkdown(entries);
      filename = "manovia-journal-export.md";
    }
    setExportedMarkdown(markdown);
    setExportedFilename(filename);
    triggerMarkdownDownload(filename, markdown);
    onExportMarkdown?.(filename, markdown);
  }, [activeId, body, entries, onExportMarkdown, promptId, template, title]);

  const handleLoadMore = useCallback(async () => {
    try {
      const next = await listJournalEntries({ limit: 50, offset: entries.length }, client);
      setEntries((prev) => [...prev, ...next.items]);
      setTotalEntries(next.total);
      setHasMore(next.has_more);
    } catch (err) {
      setErrorMessage(isApiError(err) ? err.message : t("journal.loadMoreError"));
    }
  }, [client, entries.length]);

  const liveWordCount = useMemo(() => countWords(`${title} ${body}`), [title, body]);

  const selectedPrompt = useMemo(
    () => prompts.find((p) => p.id === promptId) ?? null,
    [prompts, promptId],
  );

  const filteredEntries = useMemo(
    () => filterEntriesClientSide(entries, searchQuery),
    [entries, searchQuery],
  );

  return (
    <div className="space-y-6" data-testid="journal-view">
      {/* Header & Distraction-free toggle */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-brand-700">
            {t("journal.eyebrow")}
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-stone-900">{t("journal.heading")}</h1>
          <p className="mt-1 text-sm text-stone-600">
            {t("journal.lede")}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="journal-focus-toggle"
            aria-pressed={distractionFree}
            onClick={() => setDistractionFree((prev) => !prev)}
            className="rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-xs font-medium text-stone-700 hover:bg-stone-50"
          >
            {distractionFree ? t("journal.exitFocus") : t("journal.focusMode")}
          </button>
          <button
            type="button"
            data-testid="journal-new-button"
            onClick={() => handleStartNewEntry("free")}
            className="rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-xs font-medium text-stone-700 hover:bg-stone-50"
          >
            {t("journal.newEntry")}
          </button>
          <button
            type="button"
            data-testid="journal-export-button"
            onClick={handleExportMarkdown}
            className="rounded-lg border border-brand-200 bg-brand-50 px-3 py-1.5 text-xs font-medium text-brand-800 hover:bg-brand-100"
          >
            {t("journal.exportMarkdown")}
          </button>
        </div>
      </div>

      {/* Crisis resources card when safety engine flags an entry */}
      {activeCrisis !== null ? (
        <CrisisCard
          message={activeCrisis.message}
          resources={activeCrisis.resources}
          emergency={activeCrisis.emergency}
          announce
          heading={t("journal.crisisHeading")}
          testId="journal-crisis-card"
        />
      ) : null}

      {/* Error banner */}
      {errorMessage !== null ? (
        <div
          role="alert"
          data-testid="journal-error-banner"
          className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {errorMessage}
        </div>
      ) : null}

      {/* Private by default explainer */}
      {!distractionFree ? (
        <section
          aria-label={t("journal.privacyExplainerLabel")}
          data-testid="journal-privacy-explainer"
          className="rounded-xl border border-brand-200 bg-brand-50/50 p-4 text-sm text-stone-800"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full bg-brand-700" />
              <strong className="font-semibold text-stone-900">
                {t("journal.privacyBadge")}
              </strong>
            </div>
            <button
              type="button"
              data-testid="journal-privacy-toggle"
              aria-expanded={showPrivacyDetails}
              onClick={() => setShowPrivacyDetails((prev) => !prev)}
              className="text-xs font-medium text-brand-800 underline hover:text-brand-900"
            >
              {showPrivacyDetails ? t("journal.privacyToggleHide") : t("journal.privacyToggleShow")}
            </button>
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-stone-600">
            {t("journal.privacyIntro1")}{" "}
            <code>FIELD_ENCRYPTION_KEY</code>
            {t("journal.privacyIntro2")}
          </p>
          {showPrivacyDetails ? (
            <div
              data-testid="journal-privacy-details"
              className="mt-3 space-y-2 border-t border-brand-200/80 pt-3 text-xs leading-relaxed text-stone-700"
            >
              <p>
                <strong>{t("journal.searchOnlyPrefix")}</strong>{" "}
                {t("journal.searchOnlyBody")}
              </p>
              <p>
                <strong>{t("journal.scoresPrefix")}</strong>{" "}
                {t("journal.scoresBody")}
              </p>
              <p>
                <strong>{t("journal.threatPrefix")}</strong>{" "}
                {t("journal.threatBody1")}{" "}
                <code>FIELD_ENCRYPTION_KEY</code>
                {t("journal.threatBody2")}
              </p>
            </div>
          ) : null}
        </section>
      ) : null}

      <div
        className={distractionFree ? "mx-auto max-w-3xl" : "grid gap-6 lg:grid-cols-[1fr_20rem]"}
      >
        {/* Main Distraction-Free Editor Column */}
        <section
          aria-label={t("journal.editorLabel")}
          data-testid="journal-editor"
          className="card space-y-4 p-6"
        >
          {/* Template selector pills */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div
              role="group"
              aria-label={t("journal.templatesLabel")}
              className="flex flex-wrap items-center gap-1.5"
            >
              {templates.map((tpl) => {
                const isSelected = template === tpl.id;
                return (
                  <button
                    key={tpl.id}
                    type="button"
                    data-testid={`journal-template-${tpl.id}`}
                    aria-pressed={isSelected}
                    onClick={() => handleSelectTemplate(tpl.id)}
                    className={
                      isSelected
                        ? "rounded-full bg-brand-700 px-3 py-1 text-xs font-medium text-white"
                        : "rounded-full border border-stone-300 bg-white px-3 py-1 text-xs font-medium text-stone-700 hover:bg-stone-50"
                    }
                  >
                    {tpl.label}
                  </button>
                );
              })}
            </div>

            {/* Word count + Autosave status */}
            <div className="flex items-center gap-3 text-xs text-stone-500">
              <span data-testid="journal-word-count" className="font-medium text-stone-700">
                {liveWordCount === 1
                  ? t("journal.wordCountOne", { count: liveWordCount })
                  : t("journal.wordCountMany", { count: liveWordCount })}
              </span>
              <span aria-live="polite" data-testid="journal-save-status">
                {saveState === "saving" && t("journal.statusSaving")}
                {saveState === "dirty" && t("journal.statusUnsaved")}
                {saveState === "saved" && (
                  <span title={lastSavedAt ?? undefined}>{t("journal.statusSaved")}</span>
                )}
                {saveState === "error" && t("journal.statusSaveFailed")}
                {saveState === "idle" && t("journal.statusReady")}
              </span>
            </div>
          </div>

          {/* Writing prompt chips */}
          {prompts.length > 0 && !distractionFree ? (
            <div className="space-y-2" data-testid="journal-prompts-bar">
              <p className="text-xs font-medium text-stone-600">
                {t("journal.promptsIntro")}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {prompts.map((prompt) => {
                  const selected = promptId === prompt.id;
                  return (
                    <button
                      key={prompt.id}
                      type="button"
                      data-testid={`journal-prompt-chip-${prompt.id}`}
                      aria-pressed={selected}
                      onClick={() => handleSelectPromptChip(prompt)}
                      className={
                        selected
                          ? "rounded-lg border border-brand-600 bg-brand-50 px-2.5 py-1 text-left text-xs font-medium text-brand-900"
                          : "rounded-lg border border-stone-200 bg-stone-50 px-2.5 py-1 text-left text-xs text-stone-700 hover:border-stone-300 hover:bg-stone-100"
                      }
                    >
                      {prompt.text}
                    </button>
                  );
                })}
              </div>
              {selectedPrompt !== null ? (
                <p
                  data-testid="journal-prompt-hint"
                  className="rounded-lg bg-stone-100 px-3 py-2 text-xs italic text-stone-600"
                >
                  {t("journal.promptHint", { hint: selectedPrompt.hint })}
                </p>
              ) : null}
            </div>
          ) : null}

          {/* Title input */}
          <div>
            <label htmlFor="journal-title-input" className="sr-only">
              {t("journal.titleLabel")}
            </label>
            <input
              id="journal-title-input"
              data-testid="journal-title-input"
              type="text"
              maxLength={200}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("journal.titlePlaceholder")}
              className="w-full border-0 border-b border-stone-200 bg-transparent pb-2 text-lg font-medium text-stone-900 placeholder:text-stone-400 focus:border-brand-600 focus:outline-none"
            />
          </div>

          {/* Distraction-free body textarea */}
          <div>
            <label htmlFor="journal-body-input" className="sr-only">
              Journal entry
            </label>
            <textarea
              id="journal-body-input"
              data-testid="journal-body-input"
              rows={distractionFree ? 16 : 11}
              maxLength={20000}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder={t("journal.editorPlaceholder")}
              className="w-full resize-y rounded-lg border border-stone-200 bg-stone-50/40 p-3.5 text-sm leading-relaxed text-stone-900 placeholder:text-stone-400 focus:border-brand-600 focus:bg-white focus:outline-none"
            />
          </div>

          {/* Footer controls: emotion toggle, Save now, Delete with confirmation */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-200 pt-3">
            <label className="flex items-center gap-2 text-xs text-stone-700">
              <input
                type="checkbox"
                data-testid="journal-store-emotions-toggle"
                checked={storeEmotions}
                onChange={(e) => setStoreEmotions(e.target.checked)}
                className="h-4 w-4 rounded border-stone-300 text-brand-700 focus:ring-brand-600"
              />
              <span>Save numeric emotion &amp; sentiment scores (in the clear)</span>
            </label>

            <div className="flex items-center gap-2">
              {activeId !== null && !confirmingDelete ? (
                <button
                  type="button"
                  data-testid="journal-delete-button"
                  onClick={() => setConfirmingDelete(true)}
                  className="rounded-lg border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50"
                >
                  {t("journal.deleteEntry")}
                </button>
              ) : null}

              <button
                type="button"
                data-testid="journal-save-button"
                disabled={body.trim() === "" || saveState === "saving"}
                onClick={() => {
                  void performSave();
                }}
                className="rounded-lg bg-brand-700 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-brand-800 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {saveState === "saving" ? t("journal.statusSaving") : t("journal.saveNow")}
              </button>
            </div>
          </div>

          {/* Delete confirmation dialog */}
          {confirmingDelete ? (
            <div
              role="alertdialog"
              aria-label={t("journal.deleteConfirmLabel")}
              data-testid="journal-delete-confirm-dialog"
              className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-900"
            >
              <span>
                {t("journal.deleteAsk")}
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  data-testid="journal-delete-cancel"
                  onClick={() => setConfirmingDelete(false)}
                  className="rounded-md border border-stone-300 bg-white px-2.5 py-1 font-medium text-stone-700 hover:bg-stone-50"
                >
                  {t("journal.deleteCancel")}
                </button>
                <button
                  type="button"
                  data-testid="journal-delete-confirm"
                  onClick={() => {
                    void handleConfirmDelete();
                  }}
                  className="rounded-md bg-red-700 px-2.5 py-1 font-medium text-white hover:bg-red-800"
                >
                  {t("journal.deleteConfirm")}
                </button>
              </div>
            </div>
          ) : null}

          {/* Exported Markdown preview drawer */}
          {exportedMarkdown !== null ? (
            <div
              data-testid="journal-export-preview"
              className="space-y-2 rounded-lg border border-stone-200 bg-stone-50 p-3"
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-stone-800">
                  {t("journal.exportTitle", { filename: exportedFilename })}
                </span>
                <button
                  type="button"
                  data-testid="journal-export-close"
                  onClick={() => setExportedMarkdown(null)}
                  className="text-xs text-stone-500 hover:text-stone-800"
                >
                  {t("journal.exportClose")}
                </button>
              </div>
              <pre
                data-testid="journal-export-output"
                className="max-h-48 overflow-auto whitespace-pre-wrap rounded border border-stone-200 bg-white p-2.5 font-mono text-xs text-stone-800"
              >
                {exportedMarkdown}
              </pre>
            </div>
          ) : null}
        </section>

        {/* Sidebar: Client-side search & Past entries list */}
        {!distractionFree ? (
          <aside
            aria-label={t("journal.entriesLabel")}
            data-testid="journal-sidebar"
            className="space-y-4"
          >
            <div className="card space-y-3 p-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-stone-900">
                  Your entries ({totalEntries})
                </h2>
              </div>

              <div>
                <label htmlFor="journal-search-input" className="sr-only">
                  Search entries in browser
                </label>
                <input
                  id="journal-search-input"
                  data-testid="journal-search-input"
                  type="search"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t("journal.searchPlaceholder")}
                  className="w-full rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-xs text-stone-900 placeholder:text-stone-400 focus:border-brand-600 focus:outline-none"
                />
                <p className="mt-1 text-[11px] text-stone-500">
                  Searched locally in your browser — server-side search on ciphertext is impossible.
                </p>
              </div>

              {loading ? (
                <p className="py-4 text-center text-xs text-stone-500">{t("journal.decryptingEntries")}</p>
              ) : filteredEntries.length === 0 ? (
                <p
                  data-testid="journal-empty-state"
                  className="py-4 text-center text-xs text-stone-500"
                >
                  {searchQuery.trim() !== ""
                    ? t("journal.searchEmpty")
                    : t("journal.entriesEmpty")}
                </p>
              ) : (
                <ul className="space-y-2" data-testid="journal-entry-list">
                  {filteredEntries.map((item) => {
                    const isCurrent = item.id === activeId;
                    const displayTitle =
                      item.title.trim() !== "" ? item.title : t("journal.untitled");
                    return (
                      <li key={item.id}>
                        <button
                          type="button"
                          data-testid="journal-entry-item"
                          onClick={() => selectEntryIntoEditor(item)}
                          className={
                            isCurrent
                              ? "w-full rounded-lg border border-brand-600 bg-brand-50/70 p-3 text-left"
                              : "w-full rounded-lg border border-stone-200 bg-white p-3 text-left hover:border-stone-300 hover:bg-stone-50"
                          }
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="truncate text-xs font-semibold text-stone-900">
                              {displayTitle}
                            </span>
                            <span className="shrink-0 text-[11px] text-stone-500">
                              {item.word_count}w
                            </span>
                          </div>
                          <p className="mt-1 line-clamp-2 text-xs text-stone-600">{item.body}</p>
                          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-stone-500">
                            {item.template ? (
                              <span className="rounded bg-stone-100 px-1.5 py-0.5 font-medium text-stone-700">
                                {item.template}
                              </span>
                            ) : null}
                            {item.metadata?.primary_emotion ? (
                              <span className="rounded bg-brand-50 px-1.5 py-0.5 font-medium text-brand-800">
                                {item.metadata.primary_emotion}
                              </span>
                            ) : null}
                          </div>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}

              {hasMore ? (
                <button
                  type="button"
                  data-testid="journal-load-more"
                  onClick={() => {
                    void handleLoadMore();
                  }}
                  className="w-full rounded-lg border border-stone-300 bg-white py-1.5 text-xs font-medium text-stone-700 hover:bg-stone-50"
                >
                  Load more entries
                </button>
              ) : null}
            </div>
          </aside>
        ) : null}
      </div>
    </div>
  );
}
