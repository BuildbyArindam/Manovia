/**
 * The read-only viewer a professional opens — no account, no onboarding, no
 * Manovia data of their own.
 *
 * The order of operations is the important part:
 *
 * 1. **Ask whether the link is usable before spending anything.** `GET
 *    /public/shares/{token}` returns no contents and costs no view.
 * 2. **Check the key is in the address.** If the fragment was cut off when the
 *    link was copied, say so — there is no point taking a passcode and burning
 *    one of the link's openings on a document that cannot be decrypted.
 * 3. **Open once.** `POST …/open` spends one view and returns ciphertext. The
 *    result is kept in state, so re-rendering, downloading twice or printing
 *    never spends another.
 * 4. **Decrypt locally** with the key derived from the URL fragment and the
 *    passcode, then render. Nothing decrypted is ever sent anywhere.
 *
 * Every refusal is worded for somebody who has never seen this app: an expired,
 * revoked or exhausted link gets the same short answer, because the server does
 * not distinguish them either.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { isApiError, api as defaultClient, type ApiClient } from "@/lib/api";
import { formatDateTime } from "@/lib/dates";
import { t } from "@/lib/i18n/i18n";

import {
  ShareCryptoUnavailable,
  ShareDecryptFailed,
  decryptDocument,
  deriveShareKey,
  keyFromUrl,
} from "./crypto";
import { SHARE_ERROR_CODES, fetchPublicStatus, openPublicShare } from "./endpoints";
import { Banner, SharePayloadView } from "./SharePayloadView";
import type { OpenOut, SharePayload } from "./types";

export interface ShareViewerProps {
  /** The token from the path: `/s/<token>`. */
  token: string;
  /** The key from the fragment, or null when the link arrived without one. */
  urlKey: string | null;
  client?: ApiClient;
}

interface DecryptedDocuments {
  payload: SharePayload | null;
  downloads: Array<{ name: string; url: string; filename: string }>;
}

/** The first integer in a server message, for copy that needs a number. */
function firstNumber(message: string): string | null {
  const match = /(\d+)/.exec(message);
  return match?.[1] ?? null;
}

function downloadLabel(name: string): string {
  if (name === "pdf") {
    return t("sharing.viewerDownloadPdf");
  }
  if (name === "fhir") {
    return t("sharing.viewerDownloadFhir");
  }
  return t("sharing.viewerDownloadPayload");
}

/**
 * One refusal. Used for a dead link, a missing key, and a document that would
 * not open — three situations where the honest answer is short and final, and
 * where extra detail would either leak something or raise a hope this page
 * cannot answer.
 */
function Refusal({ title, body }: { title: string; body: string }): ReactElement {
  return (
    <div className="card space-y-2" role="alert" data-testid="share-viewer-refused">
      <h2 className="text-xl">{title}</h2>
      <p className="text-sm text-ink-muted" data-testid="share-viewer-refused-body">
        {body}
      </p>
      <p className="text-sm text-ink-muted">{t("sharing.viewerFooter")}</p>
    </div>
  );
}

export function ShareViewer({
  token,
  urlKey,
  client = defaultClient,
}: ShareViewerProps): ReactElement {
  const [passcode, setPasscode] = useState("");
  const [documents, setDocuments] = useState<DecryptedDocuments | null>(null);
  const [opened, setOpened] = useState<OpenOut | null>(null);
  const [failure, setFailure] = useState<{ title: string; body: string } | null>(null);
  const objectUrls = useRef<string[]>([]);

  // Revoke any blob URL this viewer created. A shared document left addressable
  // in the browser after the tab closes is a small leak with an easy fix.
  useEffect(() => {
    const created = objectUrls.current;
    return () => {
      for (const url of created) {
        try {
          URL.revokeObjectURL(url);
        } catch {
          // Nothing to clean up where there are no object URLs.
        }
      }
    };
  }, []);

  const statusQuery = useQuery({
    queryKey: ["public-share", token],
    queryFn: () => fetchPublicStatus(token, client),
    retry: false,
    staleTime: 30_000,
  });

  /**
   * Derive the key once, then decrypt every document in the response.
   *
   * An unreadable `payload` document is not fatal: the PDF and the FHIR bundle
   * are still offered as downloads, which is what a professional's workflow
   * needs anyway.
   */
  const decryptAll = useCallback(
    async (result: OpenOut, entered: string): Promise<DecryptedDocuments> => {
      // Prefer the key in the live address, so somebody who pastes a corrected
      // link uses the corrected key; fall back to the one the page parsed.
      const fromAddress = typeof window === "undefined" ? null : keyFromUrl(window.location.href);
      const key = await deriveShareKey({
        secret: fromAddress ?? urlKey ?? "",
        passcode: entered,
        token,
      });
      const downloads: DecryptedDocuments["downloads"] = [];
      let payload: SharePayload | null = null;

      const makeDownload = (
        name: string,
        bytes: Uint8Array,
        filename: string,
      ): { name: string; url: string; filename: string } => {
        try {
          const url = URL.createObjectURL(new Blob([bytes as BlobPart]));
          objectUrls.current.push(url);
          return { name, url, filename };
        } catch {
          // No object URLs here (an old browser, or a test environment): the
          // document still renders on screen; only the download is missing.
          return { name, url: "", filename };
        }
      };

      for (const document of result.documents) {
        const bytes = await decryptDocument(key, document);
        if (document.name === "payload") {
          try {
            payload = JSON.parse(new TextDecoder().decode(bytes)) as SharePayload;
          } catch {
            payload = null;
          }
          downloads.push(makeDownload("payload", bytes, "manovia-share-summary.json"));
          continue;
        }
        downloads.push(
          makeDownload(
            document.name,
            bytes,
            document.name === "pdf"
              ? "manovia-appointment-summary.pdf"
              : "manovia-appointment-summary.fhir.json",
          ),
        );
      }
      return { payload, downloads };
    },
    [token, urlKey],
  );

  const openMutation = useMutation({
    mutationFn: (entered: string) => openPublicShare(token, entered, client),
    onSuccess: async (result, entered) => {
      setFailure(null);
      try {
        setDocuments(await decryptAll(result, entered));
        setOpened(result);
      } catch (error) {
        if (error instanceof ShareCryptoUnavailable) {
          setFailure({
            title: t("sharing.viewerCryptoUnavailable"),
            body: t("sharing.viewerCryptoBody"),
          });
          return;
        }
        // One message for a wrong key, a wrong passcode and a changed byte: the
        // viewer is not told which, and neither is anybody watching over them.
        setFailure({
          title: t("sharing.viewerDecryptFailed"),
          body:
            error instanceof ShareDecryptFailed
              ? t("sharing.viewerDecryptFailedBody")
              : t("sharing.errorGeneric"),
        });
      }
    },
  });

  const errorCopy = useMemo(() => {
    const error = openMutation.error;
    if (!isApiError(error)) {
      return null;
    }
    if (error.code === SHARE_ERROR_CODES.passcodeIncorrect) {
      const remaining = firstNumber(error.message);
      return remaining === null
        ? error.message
        : t("sharing.viewerPasscodeWrong", { count: remaining });
    }
    if (error.code === SHARE_ERROR_CODES.passcodeLocked) {
      const minutes = firstNumber(error.message);
      return minutes === null ? error.message : t("sharing.viewerLocked", { minutes });
    }
    if (error.code === SHARE_ERROR_CODES.rateLimited) {
      return t("sharing.viewerRateLimited");
    }
    if (error.code === SHARE_ERROR_CODES.unavailable) {
      // A link that died between the status call and the open: the same answer
      // as any other dead link, not a form error.
      return null;
    }
    return error.message === "" ? t("sharing.errorGeneric") : error.message;
  }, [openMutation.error]);

  if (statusQuery.isLoading) {
    return (
      <p role="status" className="text-ink-muted" data-testid="share-viewer-loading">
        {t("app.loading")}
      </p>
    );
  }

  const status = statusQuery.data;
  if (statusQuery.isError || status === undefined || !status.available) {
    return (
      <Refusal
        title={t("sharing.viewerUnavailableTitle")}
        body={t("sharing.viewerUnavailableBody")}
      />
    );
  }

  if (urlKey === null) {
    return <Refusal title={t("sharing.viewerNoKeyTitle")} body={t("sharing.viewerNoKeyBody")} />;
  }

  if (failure !== null) {
    return (
      <div className="space-y-4">
        <Refusal title={failure.title} body={failure.body} />
        <button
          type="button"
          className="btn-secondary"
          data-testid="share-viewer-retry"
          onClick={() => {
            setFailure(null);
            setPasscode("");
          }}
        >
          {t("sharing.viewerRetry")}
        </button>
      </div>
    );
  }

  if (documents !== null) {
    return (
      <div className="space-y-4" data-testid="share-viewer-open">
        <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm text-ink-muted">
          <span data-testid="share-viewer-views">
            {t("sharing.viewerViewsLeft", { count: opened?.views_remaining ?? 0 })}
          </span>
          {opened !== null ? (
            <span data-testid="share-viewer-expires">
              {t("sharing.viewerExpiresOn", { date: formatDateTime(opened.expires_at) })}
            </span>
          ) : null}
        </div>

        {documents.payload !== null ? (
          <SharePayloadView payload={documents.payload} />
        ) : (
          <Banner title={status.disclaimer_title} body={status.disclaimer} />
        )}

        <div className="flex flex-wrap gap-2" data-testid="share-viewer-downloads">
          {documents.downloads
            .filter((download) => download.url !== "")
            .map((download) => (
              <a
                key={download.name}
                className="btn-secondary"
                href={download.url}
                download={download.filename}
                data-testid={`share-download-${download.name}`}
              >
                {downloadLabel(download.name)}
              </a>
            ))}
          <button
            type="button"
            className="btn-secondary"
            data-testid="share-viewer-print"
            onClick={() => window.print()}
          >
            {t("sharing.viewerPrint")}
          </button>
        </div>

        <p className="text-sm text-ink-muted">{t("sharing.viewerFooter")}</p>
      </div>
    );
  }

  return (
    <form
      className="card space-y-4"
      data-testid="share-viewer-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (passcode.trim() === "" || openMutation.isPending) {
          return;
        }
        openMutation.mutate(passcode);
      }}
    >
      <div>
        <h2 className="text-xl">{t("sharing.viewerTitle")}</h2>
        <p className="mt-1 text-sm text-ink-muted">{t("sharing.viewerLede")}</p>
      </div>

      <Banner title={status.disclaimer_title} body={status.disclaimer} />

      <p className="text-sm" role="note" data-testid="share-viewer-spends">
        {status.requires_passcode ? t("sharing.viewerSpentWarning") : t("sharing.viewerNoPasscode")}
      </p>

      <div>
        <label className="block text-sm font-semibold" htmlFor="share-viewer-passcode">
          {t("sharing.viewerPasscodeLabel")}
        </label>
        <input
          id="share-viewer-passcode"
          type="password"
          autoComplete="off"
          data-testid="share-viewer-passcode"
          className="mt-1 w-full rounded-xl border border-border-strong bg-surface px-3 py-2 font-mono tracking-widest"
          value={passcode}
          onChange={(event) => setPasscode(event.target.value)}
          aria-describedby="share-viewer-error"
        />
      </div>

      <p role="alert" className="text-sm" id="share-viewer-error" data-testid="share-viewer-error">
        {errorCopy ?? ""}
      </p>

      <button
        type="submit"
        className="btn-primary"
        data-testid="share-viewer-submit"
        disabled={passcode.trim() === "" || openMutation.isPending}
      >
        {openMutation.isPending ? t("sharing.viewerOpening") : t("sharing.viewerOpen")}
      </button>
    </form>
  );
}
