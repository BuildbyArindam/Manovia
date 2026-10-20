/**
 * "Prepare for my appointment" — the owner half of the share bridge (Day 47).
 *
 * The screen is built around one promise: **what you see is what gets shared.**
 * Nothing is ticked by default, the preview is the real document (built by the
 * server, then shown as the sentences the person confirms), and creating a link
 * echoes the preview's hash back — so if the data moved underneath while they
 * were reading, the create is refused and they look again rather than sharing
 * something they did not just read.
 *
 * The other promise is about the key. It appears exactly once, in this tab, as
 * part of the link's `#k=…` fragment. There is no "show me the key again"
 * button, because there is no key again: the server stored ciphertext and never
 * saw it. The copy says so in as many places as it can without becoming noise.
 */

import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { isApiError, api as defaultClient, type ApiClient } from "@/lib/api";
import { formatDateTime } from "@/lib/dates";
import { t } from "@/lib/i18n/i18n";

import {
  SHARE_ERROR_CODES,
  createShare,
  fetchShareOptions,
  previewShare,
  saveSharePreference,
  shareUrl,
} from "./endpoints";
import type {
  CatalogueItem,
  CreateShareBody,
  CreatedShare,
  ShareFormat,
  ShareItem,
  ShareSelectionBody,
} from "./types";

export const shareKeys = {
  options: ["shares", "options"] as const,
  preference: ["shares", "preference"] as const,
  list: ["shares", "list"] as const,
  audit: (shareId: string) => ["shares", "audit", shareId] as const,
  preview: (signature: string) => ["shares", "preview", signature] as const,
};

/** How long the builder waits after a change before asking for a preview. */
export const PREVIEW_DEBOUNCE_MS = 400;

export interface ShareBuilderProps {
  client?: ApiClient;
  /** Called with the one response that carries the key, for tests and pages. */
  onCreated?: (created: CreatedShare) => void;
}

/** The copy error for a create/preview failure, chosen by the backend's code. */
function messageFor(error: unknown): string {
  if (!isApiError(error)) {
    return t("sharing.errorGeneric");
  }
  switch (error.code) {
    case SHARE_ERROR_CODES.disabled:
      return t("sharing.errorDisabled");
    case SHARE_ERROR_CODES.notFound:
      return t("sharing.errorNotFound");
    case SHARE_ERROR_CODES.consentMismatch:
      return t("sharing.errorMismatch");
    case SHARE_ERROR_CODES.limit: {
      // The number is the deployment's own cap, quoted by the server. Without a
      // number the translated sentence would read "you already have live links",
      // so the server's wording is used instead: an honest sentence beats a
      // broken one, even when it is not in the reader's language.
      const limit = /(\d+)/.exec(error.message)?.[1];
      return limit === undefined ? error.message : t("sharing.errorLimit", { count: limit });
    }
    case SHARE_ERROR_CODES.requestInvalid:
      return t("sharing.errorRequest");
    default:
      return error.message || t("sharing.errorGeneric");
  }
}

export function ShareBuilder({
  client = defaultClient,
  onCreated,
}: ShareBuilderProps): ReactElement {
  const queryClient = useQueryClient();

  const optionsQuery = useQuery({
    queryKey: shareKeys.options,
    queryFn: () => fetchShareOptions(client),
    staleTime: 30_000,
  });
  const options = optionsQuery.data;

  // --- the selection -------------------------------------------------------
  const [items, setItems] = useState<ShareItem[]>([]);
  const [windowDays, setWindowDays] = useState<number | null>(null);
  const [recordIds, setRecordIds] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [questions, setQuestions] = useState<string[]>([]);
  const [formats, setFormats] = useState<ShareFormat[]>(["pdf"]);
  const [expiresInDays, setExpiresInDays] = useState<number | null>(null);
  const [viewLimit, setViewLimit] = useState<number | null>(null);
  const [passcode, setPasscode] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [copied, setCopied] = useState<"link" | "passcode" | null>(null);
  const [created, setCreated] = useState<CreatedShare | null>(null);

  // The deployment's numbers arrive with the options; until then the inputs show
  // nothing rather than a guess that could be outside the allowed range.
  useEffect(() => {
    if (options === undefined) {
      return;
    }
    setWindowDays((current) => current ?? DEFAULT_WINDOW(options));
    setExpiresInDays((current) => current ?? options.defaults.default_ttl_days);
    setViewLimit((current) => current ?? options.defaults.default_view_limit);
  }, [options]);

  const toggleItem = useCallback((key: ShareItem) => {
    setConfirmed(false);
    setItems((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  }, []);

  const toggleFormat = useCallback((format: ShareFormat) => {
    setConfirmed(false);
    setFormats((current) =>
      current.includes(format) ? current.filter((item) => item !== format) : [...current, format],
    );
  }, []);

  const selection: ShareSelectionBody = useMemo(() => {
    const body: ShareSelectionBody = {
      items,
      window_days: windowDays ?? 30,
    };
    if (items.includes("note") && note.trim() !== "") {
      body.note = note;
    }
    if (items.includes("questions")) {
      const cleaned = questions.map((question) => question.trim()).filter((q) => q !== "");
      if (cleaned.length > 0) {
        body.questions = cleaned;
      }
    }
    if (items.includes("thought_records") && recordIds.length > 0) {
      body.thought_record_ids = recordIds;
    }
    return body;
  }, [items, windowDays, note, questions, recordIds]);

  // A signature, not the object: react-query keys must be stable across renders
  // and the preview must be re-requested only when the selection really changes.
  const signature = JSON.stringify(selection);
  const [debouncedSignature, setDebouncedSignature] = useState(signature);
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSignature(signature), PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [signature]);

  const canPreview = items.length > 0 && formats.length > 0;
  const previewQuery = useQuery({
    queryKey: shareKeys.preview(debouncedSignature),
    queryFn: () => previewShare(JSON.parse(debouncedSignature) as ShareSelectionBody, client),
    enabled: canPreview && debouncedSignature === signature && options?.enabled === true,
    staleTime: 60_000,
    retry: false,
  });
  const preview = debouncedSignature === signature ? previewQuery.data : undefined;

  const preferenceMutation = useMutation({
    mutationFn: (enabled: boolean) => saveSharePreference(enabled, client),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: shareKeys.options });
    },
  });

  const createMutation = useMutation({
    mutationFn: (body: CreateShareBody) => createShare(body, client),
    onSuccess: (result) => {
      setCreated(result);
      setConfirmed(false);
      onCreated?.(result);
      void queryClient.invalidateQueries({ queryKey: shareKeys.list });
    },
    onError: (error) => {
      // The data moved while they were reading: throw the preview away so the
      // next one is built from what is true now.
      if (isApiError(error) && error.code === SHARE_ERROR_CODES.consentMismatch) {
        void queryClient.invalidateQueries({ queryKey: shareKeys.preview(debouncedSignature) });
      }
    },
  });

  const copy = useCallback(async (what: "link" | "passcode", value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(what);
    } catch {
      setCopied(null);
    }
  }, []);

  if (optionsQuery.isLoading) {
    return (
      <p role="status" className="text-ink-muted" data-testid="share-builder-loading">
        {t("app.loading")}
      </p>
    );
  }
  if (optionsQuery.isError || options === undefined) {
    return (
      <p role="alert" className="text-ink-muted" data-testid="share-builder-error">
        {messageFor(optionsQuery.error)}
      </p>
    );
  }

  if (!options.deployment_enabled) {
    return (
      <div className="card" data-testid="share-deployment-off">
        <h2 className="text-xl">{t("sharing.offTitle")}</h2>
        <p className="mt-2 text-ink-muted">{t("sharing.deploymentOff")}</p>
      </div>
    );
  }

  if (!options.enabled) {
    return (
      <div className="card space-y-3" data-testid="share-off">
        <h2 className="text-xl">{t("sharing.offTitle")}</h2>
        <p className="text-ink-muted">{t("sharing.offBody")}</p>
        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-4 py-3">
          <input
            type="checkbox"
            role="switch"
            data-testid="share-enable-toggle"
            checked={false}
            disabled={preferenceMutation.isPending}
            onChange={(event) => preferenceMutation.mutate(event.target.checked)}
            className="mt-1 h-5 w-5 accent-accent-bg"
          />
          <span className="font-semibold">{t("sharing.switchLabel")}</span>
        </label>
      </div>
    );
  }

  const catalogue: CatalogueItem[] = options.items;
  const noteLimit = options.limits.note_chars;
  const questionLimit = options.limits.questions;
  const questionChars = options.limits.question_chars;
  const recordLimit = options.limits.thought_records;
  const windows = options.limits.windows.length > 0 ? options.limits.windows : [14, 30, 90];

  // A created link takes the screen. The key is shown exactly once and cannot be
  // fetched again, so it gets the person's whole attention rather than sitting
  // above a half-filled builder they might carry on editing.
  if (created !== null) {
    return (
      <div className="space-y-6" data-testid="share-builder">
        <CreatedLink
          created={created}
          copied={copied}
          onCopy={copy}
          onAnother={() => {
            setCreated(null);
            setItems([]);
            setNote("");
            setQuestions([]);
            setRecordIds([]);
            setConfirmed(false);
          }}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="share-builder">
      <section className="card space-y-4" aria-labelledby="share-items-heading">
        <div>
          <h2 id="share-items-heading" className="text-xl">
            {t("sharing.itemsHeading")}
          </h2>
          <p className="mt-1 text-sm text-ink-muted">{t("sharing.itemsHelp")}</p>
        </div>

        <div className="space-y-3">
          {catalogue.map((item) => (
            <label
              key={item.key}
              className="flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-4 py-3"
            >
              <input
                type="checkbox"
                className="mt-1 h-5 w-5 accent-accent-bg"
                data-testid={`share-item-${item.key}`}
                checked={items.includes(item.key)}
                onChange={() => toggleItem(item.key)}
                aria-describedby={`share-item-${item.key}-detail`}
              />
              <span id={`share-item-${item.key}-detail`} className="text-sm">
                <span className="block font-semibold">{item.label}</span>
                <span className="block text-ink-muted">{item.blurb}</span>
                <span className="mt-1 block">
                  <span className="font-semibold">{t("sharing.includesLabel")}: </span>
                  {item.includes.join("; ")}
                </span>
                <span className="block text-ink-muted">
                  <span className="font-semibold">{t("sharing.excludesLabel")}: </span>
                  {item.excludes.join("; ")}
                </span>
              </span>
            </label>
          ))}
        </div>

        {items.includes("thought_records") ? (
          <fieldset className="space-y-2" data-testid="share-records">
            <legend className="font-semibold">{t("sharing.recordsHeading")}</legend>
            <p className="text-sm text-ink-muted">
              {t("sharing.recordsHelp", { max: recordLimit })}
            </p>
            {options.thought_records.length === 0 ? (
              <p className="text-sm text-ink-muted">{t("sharing.recordsNone")}</p>
            ) : (
              <div className="space-y-2">
                {options.thought_records.slice(0, recordLimit * 5).map((record) => (
                  <label
                    key={record.id}
                    className="flex cursor-pointer items-start gap-3 rounded-xl border border-border px-4 py-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      className="mt-1 h-4 w-4 accent-accent-bg"
                      data-testid={`share-record-${record.id}`}
                      checked={recordIds.includes(record.id)}
                      disabled={!recordIds.includes(record.id) && recordIds.length >= recordLimit}
                      onChange={() => {
                        setConfirmed(false);
                        setRecordIds((current) =>
                          current.includes(record.id)
                            ? current.filter((id) => id !== record.id)
                            : [...current, record.id],
                        );
                      }}
                    />
                    <span>
                      {t("sharing.recordMeta", {
                        date: formatDateTime(record.created_at),
                        emotion: record.emotion ?? t("sharing.recordNoEmotion"),
                        before: record.intensity_before ?? "—",
                        after: record.intensity_after ?? "—",
                      })}
                    </span>
                  </label>
                ))}
                <p className="text-sm text-ink-muted" aria-live="polite">
                  {t("sharing.recordsChosen", { count: recordIds.length, max: recordLimit })}
                </p>
              </div>
            )}
          </fieldset>
        ) : null}

        <div className="space-y-2">
          <label className="block text-sm font-semibold" htmlFor="share-window">
            {t("sharing.windowLabel")}
          </label>
          <select
            id="share-window"
            data-testid="share-window"
            className="w-full rounded-xl border border-border-strong bg-surface px-3 py-2"
            value={windowDays ?? windows[windows.length - 1]}
            onChange={(event) => {
              setConfirmed(false);
              setWindowDays(Number(event.target.value));
            }}
          >
            {windows.map((days) => (
              <option key={days} value={days}>
                {t("sharing.windowOption", { days })}
              </option>
            ))}
          </select>
          <p className="text-sm text-ink-muted">{t("sharing.windowHelp")}</p>
        </div>

        {items.includes("note") ? (
          <div className="space-y-2">
            <label className="block text-sm font-semibold" htmlFor="share-note">
              {t("sharing.noteLabel")}
            </label>
            <textarea
              id="share-note"
              data-testid="share-note"
              rows={4}
              maxLength={noteLimit}
              className="w-full rounded-xl border border-border-strong bg-surface px-3 py-2"
              value={note}
              onChange={(event) => {
                setConfirmed(false);
                setNote(event.target.value);
              }}
              aria-describedby="share-note-count"
            />
            <p id="share-note-count" className="text-sm text-ink-muted" aria-live="polite">
              {t("sharing.noteCount", { count: note.length, max: noteLimit })}
            </p>
          </div>
        ) : null}

        {items.includes("questions") ? (
          <fieldset className="space-y-2" data-testid="share-questions">
            <legend className="font-semibold">{t("sharing.questionsLabel")}</legend>
            <p className="text-sm text-ink-muted">
              {t("sharing.questionsHelp", { max: questionLimit, chars: questionChars })}
            </p>
            {questions.map((question, index) => (
              <div key={index} className="flex items-start gap-2">
                <label className="sr-only" htmlFor={`share-question-${index}`}>
                  {t("sharing.questionsLabel")} {index + 1}
                </label>
                <input
                  id={`share-question-${index}`}
                  type="text"
                  data-testid={`share-question-${index}`}
                  maxLength={questionChars}
                  placeholder={t("sharing.questionPlaceholder")}
                  className="w-full rounded-xl border border-border-strong bg-surface px-3 py-2"
                  value={question}
                  onChange={(event) => {
                    setConfirmed(false);
                    setQuestions((current) =>
                      current.map((item, position) =>
                        position === index ? event.target.value : item,
                      ),
                    );
                  }}
                />
                <button
                  type="button"
                  className="btn-secondary shrink-0"
                  data-testid={`share-question-remove-${index}`}
                  aria-label={t("sharing.removeQuestion", { index: index + 1 })}
                  onClick={() => {
                    setConfirmed(false);
                    setQuestions((current) => current.filter((_, position) => position !== index));
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
            <button
              type="button"
              className="btn-secondary"
              data-testid="share-question-add"
              disabled={questions.length >= questionLimit}
              onClick={() => {
                setConfirmed(false);
                setQuestions((current) => [...current, ""]);
              }}
            >
              {t("sharing.addQuestion")}
            </button>
          </fieldset>
        ) : null}
      </section>

      <section className="card space-y-3" aria-labelledby="share-formats-heading">
        <h2 id="share-formats-heading" className="text-xl">
          {t("sharing.formatsHeading")}
        </h2>
        {(
          [
            ["pdf", t("sharing.formatPdf"), t("sharing.formatPdfHelp")],
            ["fhir", t("sharing.formatFhir"), t("sharing.formatFhirHelp")],
          ] as Array<[ShareFormat, string, string]>
        ).map(([format, label, help]) => (
          <label
            key={format}
            className="flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-4 py-3"
          >
            <input
              type="checkbox"
              className="mt-1 h-5 w-5 accent-accent-bg"
              data-testid={`share-format-${format}`}
              checked={formats.includes(format)}
              onChange={() => toggleFormat(format)}
            />
            <span className="text-sm">
              <span className="block font-semibold">{label}</span>
              <span className="block text-ink-muted">{help}</span>
            </span>
          </label>
        ))}
      </section>

      <section className="card space-y-4" aria-labelledby="share-link-heading">
        <h2 id="share-link-heading" className="text-xl">
          {t("sharing.linkHeading")}
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="block text-sm font-semibold" htmlFor="share-expires">
              {t("sharing.expiresLabel")}
            </label>
            <input
              id="share-expires"
              type="number"
              min={options.defaults.min_ttl_days}
              max={options.defaults.max_ttl_days}
              data-testid="share-expires"
              className="mt-1 w-full rounded-xl border border-border-strong bg-surface px-3 py-2"
              value={expiresInDays ?? options.defaults.default_ttl_days}
              onChange={(event) => {
                setConfirmed(false);
                setExpiresInDays(Number(event.target.value));
              }}
              aria-describedby="share-expires-help"
            />
            <p id="share-expires-help" className="mt-1 text-sm text-ink-muted">
              {t("sharing.expiresHelp", {
                min: options.defaults.min_ttl_days,
                max: options.defaults.max_ttl_days,
                default: options.defaults.default_ttl_days,
              })}
            </p>
          </div>
          <div>
            <label className="block text-sm font-semibold" htmlFor="share-views">
              {t("sharing.viewsLabel")}
            </label>
            <input
              id="share-views"
              type="number"
              min={1}
              max={options.defaults.max_view_limit}
              data-testid="share-views"
              className="mt-1 w-full rounded-xl border border-border-strong bg-surface px-3 py-2"
              value={viewLimit ?? options.defaults.default_view_limit}
              onChange={(event) => {
                setConfirmed(false);
                setViewLimit(Number(event.target.value));
              }}
              aria-describedby="share-views-help"
            />
            <p id="share-views-help" className="mt-1 text-sm text-ink-muted">
              {t("sharing.viewsHelp", { max: options.defaults.max_view_limit })}
            </p>
          </div>
        </div>
        <div>
          <label className="block text-sm font-semibold" htmlFor="share-passcode">
            {t("sharing.passcodeLabel")}
          </label>
          <input
            id="share-passcode"
            type="text"
            autoComplete="off"
            data-testid="share-passcode"
            className="mt-1 w-full rounded-xl border border-border-strong bg-surface px-3 py-2"
            value={passcode}
            onChange={(event) => {
              setConfirmed(false);
              setPasscode(event.target.value);
            }}
            aria-describedby="share-passcode-help"
          />
          <p id="share-passcode-help" className="mt-1 text-sm text-ink-muted">
            {t("sharing.passcodeHelp", { min: options.defaults.passcode_min_length })}{" "}
            {t("sharing.passcodeOptional")}
          </p>
        </div>
      </section>

      <section className="card space-y-3" aria-labelledby="share-preview-heading">
        <h2 id="share-preview-heading" className="text-xl">
          {t("sharing.previewHeading")}
        </h2>
        {!canPreview ? (
          <p className="text-sm text-ink-muted" data-testid="share-preview-empty">
            {t("sharing.previewEmpty")}
          </p>
        ) : previewQuery.isFetching ? (
          <p role="status" className="text-sm text-ink-muted" data-testid="share-preview-loading">
            {t("sharing.previewLoading")}
          </p>
        ) : previewQuery.isError ? (
          <div className="space-y-2">
            <p role="alert" className="text-sm" data-testid="share-preview-error">
              {t("sharing.previewError")} {messageFor(previewQuery.error)}
            </p>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void previewQuery.refetch()}
            >
              {t("sharing.previewRetry")}
            </button>
          </div>
        ) : preview === undefined ? (
          <p className="text-sm text-ink-muted" data-testid="share-preview-stale">
            {t("sharing.previewChanged")}
          </p>
        ) : (
          <div className="space-y-3" data-testid="share-preview">
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {preview.consent_lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <p className="text-sm text-ink-muted" data-testid="share-preview-hashes">
              consent {preview.consent_sha256.slice(0, 12)} · document{" "}
              {preview.payload_sha256.slice(0, 12)}
            </p>
          </div>
        )}

        <label className="flex cursor-pointer items-start gap-3 rounded-xl border border-border-strong px-4 py-3">
          <input
            type="checkbox"
            className="mt-1 h-5 w-5 accent-accent-bg"
            data-testid="share-confirm"
            checked={confirmed}
            disabled={preview === undefined}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          <span className="text-sm font-semibold">{t("sharing.confirmLabel")}</span>
        </label>

        {createMutation.isError ? (
          <p role="alert" className="text-sm" data-testid="share-create-error">
            {messageFor(createMutation.error)}
          </p>
        ) : null}

        <button
          type="button"
          className="btn-primary"
          data-testid="share-create"
          disabled={preview === undefined || !confirmed || createMutation.isPending}
          onClick={() => {
            // No preview, no hash, no create: the button is disabled without one,
            // and sending an empty hash would only trade a clear message here for
            // a 422 from the server.
            if (preview === undefined) {
              return;
            }
            createMutation.mutate({
              ...selection,
              formats,
              expires_in_days: expiresInDays,
              view_limit: viewLimit,
              passcode: passcode.trim() === "" ? null : passcode,
              consent_sha256: preview.consent_sha256,
            });
          }}
        >
          {createMutation.isPending ? t("sharing.creating") : t("sharing.createButton")}
        </button>
      </section>
    </div>
  );
}

/** The window the mood and exercise summaries default to (the middle choice). */
function DEFAULT_WINDOW(options: { limits: { windows: number[] } }): number {
  const windows = options.limits.windows;
  if (windows.length === 0) {
    return 30;
  }
  return windows.includes(30) ? 30 : (windows[Math.floor(windows.length / 2)] ?? 30);
}

interface CreatedLinkProps {
  created: CreatedShare;
  copied: "link" | "passcode" | null;
  onCopy: (what: "link" | "passcode", value: string) => Promise<void>;
  onAnother: () => void;
}

/**
 * The link, shown once.
 *
 * The URL is rendered in full (path plus fragment) and is selectable even if the
 * clipboard API is unavailable, because losing this panel means losing the
 * document: nobody — not the owner, not Manovia — can rebuild the key.
 */
function CreatedLink({ created, copied, onCopy, onAnother }: CreatedLinkProps): ReactElement {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const url = shareUrl(created.token, created.secret, origin);
  return (
    <section
      className="card space-y-4 border-2 border-accent-bg"
      aria-labelledby="share-created-heading"
      data-testid="share-created"
    >
      <h2 id="share-created-heading" className="text-xl">
        {t("sharing.createdHeading")}
      </h2>
      <p role="alert" className="text-sm font-semibold" data-testid="share-created-warning">
        {created.warning}
      </p>

      <div>
        <span className="block text-sm font-semibold" id="share-link-label">
          {t("sharing.linkLabel")}
        </span>
        <p
          className="mt-1 break-all rounded-xl border border-border-strong bg-surface px-3 py-2 font-mono text-xs"
          data-testid="share-created-link"
          aria-labelledby="share-link-label"
        >
          {url}
        </p>
        <p className="mt-1 text-sm text-ink-muted">{t("sharing.linkHelp")}</p>
        <button
          type="button"
          className="btn-secondary mt-2"
          data-testid="share-copy-link"
          onClick={() => void onCopy("link", url)}
        >
          {copied === "link" ? t("sharing.copied") : t("sharing.copyLink")}
        </button>
      </div>

      <div>
        <span className="block text-sm font-semibold" id="share-passcode-label">
          {t("sharing.passcodeCreated")}
        </span>
        <p
          className="mt-1 font-mono text-lg tracking-widest"
          data-testid="share-created-passcode"
          aria-labelledby="share-passcode-label"
        >
          {created.passcode}
        </p>
        {created.passcode_generated ? (
          <p className="text-sm text-ink-muted">{t("sharing.passcodeGeneratedNote")}</p>
        ) : null}
        <button
          type="button"
          className="btn-secondary mt-2"
          data-testid="share-copy-passcode"
          onClick={() => void onCopy("passcode", created.passcode)}
        >
          {copied === "passcode" ? t("sharing.copied") : t("sharing.copyPasscode")}
        </button>
      </div>

      <dl className="grid gap-2 text-sm sm:grid-cols-2" data-testid="share-created-facts">
        <div>
          <dt className="font-semibold">{t("sharing.expiresLabel")}</dt>
          <dd data-testid="share-created-expires">{formatDateTime(created.expires_at)}</dd>
        </div>
        <div>
          <dt className="font-semibold">{t("sharing.viewsLabel")}</dt>
          <dd data-testid="share-created-views">
            {t("sharing.viewsAllowed", { count: created.view_limit })}
          </dd>
        </div>
      </dl>

      <div data-testid="share-created-documents">
        <h3 className="text-sm font-semibold">{t("sharing.documentsHeading")}</h3>
        <ul className="mt-1 list-disc pl-5 text-sm text-ink-muted">
          {Object.entries(created.document_bytes).map(([name, bytes]) => (
            <li key={name}>{t("sharing.documentRow", { name, bytes })}</li>
          ))}
        </ul>
      </div>

      <ul className="list-disc space-y-1 pl-5 text-sm" data-testid="share-created-consent">
        {created.consent_lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>

      <button
        type="button"
        className="btn-secondary"
        data-testid="share-another"
        onClick={onAnother}
      >
        {t("sharing.anotherButton")}
      </button>
    </section>
  );
}
