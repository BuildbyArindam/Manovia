/**
 * The privacy wire calls.
 *
 * The export is the one endpoint that does not return JSON, so it goes through
 * {@link ApiClient.getForDownload} and comes back as a `Blob`. Everything else
 * is ordinary JSON: settings, the consent history, and deletion.
 */

import { api as defaultClient, type ApiClient } from "@/lib/api";

import type {
  ConsentHistory,
  DeletionReceipt,
  PrivacySettings,
  PrivacySettingsPatch,
  RetentionDays,
} from "./types";

export const PRIVACY_ENDPOINTS = {
  settings: "/privacy/settings",
  consents: "/privacy/consents",
  export: "/privacy/export",
  account: "/privacy/account",
} as const;

/** What Manovia is allowed to keep. Defaults when nothing was ever saved. */
export async function fetchPrivacySettings(
  client: ApiClient = defaultClient,
): Promise<PrivacySettings> {
  return client.get<PrivacySettings>(PRIVACY_ENDPOINTS.settings);
}

/** Change one or more choices. Omitted fields are left alone. */
export async function savePrivacySettings(
  payload: PrivacySettingsPatch,
  client: ApiClient = defaultClient,
): Promise<PrivacySettings> {
  return client.request<PrivacySettings>(PRIVACY_ENDPOINTS.settings, {
    method: "PATCH",
    body: payload,
  });
}

/** Every consent ever recorded for this account, newest first. */
export async function fetchConsentHistory(
  client: ApiClient = defaultClient,
): Promise<ConsentHistory> {
  return client.get<ConsentHistory>(PRIVACY_ENDPOINTS.consents);
}

export interface DownloadedExport {
  blob: Blob;
  /** The filename the server proposed, from `Content-Disposition`. */
  filename: string;
}

/**
 * Fetch the whole export and hand back a blob plus the server's filename.
 *
 * The caller decides what to do with the blob (the component saves it); this
 * function only guarantees that a failure is an `ApiError` rather than a
 * half-written file.
 */
export async function downloadExport(
  client: ApiClient = defaultClient,
  format: "zip" | "json" = "zip",
): Promise<DownloadedExport> {
  const response = await client.getForDownload(`${PRIVACY_ENDPOINTS.export}?format=${format}`);
  const blob = await response.blob();
  return { blob, filename: filenameFrom(response.headers.get("Content-Disposition"), format) };
}

/**
 * Read the server's filename out of `Content-Disposition`.
 *
 * Falls back to a dated name so a download never lands as "download" twice, and
 * never trusts the header for anything but the filename itself.
 */
export function filenameFrom(header: string | null, format: "zip" | "json" = "zip"): string {
  const fallback = `manovia-export-${new Date().toISOString().slice(0, 10)}.${
    format === "json" ? "json" : "zip"
  }`;
  if (header === null) {
    return fallback;
  }
  const match = /filename="([^"]+)"|filename=([^;]+)/.exec(header);
  if (match === null) {
    return fallback;
  }
  // Only the basename, and only safe characters: a header is untrusted input.
  const name = (match[1] ?? match[2] ?? "").trim().split(/[/\\]/).pop() ?? "";
  const cleaned = name.replace(/[^A-Za-z0-9._-]/g, "");
  return cleaned === "" ? fallback : cleaned;
}

/**
 * Delete the account and return the receipt.
 *
 * `confirm` must be the exact phrase the backend expects and, for an account
 * with a password, `password` must be present: the server re-authenticates and
 * counts a wrong password towards the login lockout.
 */
export async function deleteAccount(
  options: { confirm: string; password?: string },
  client: ApiClient = defaultClient,
): Promise<DeletionReceipt> {
  return client.request<DeletionReceipt>(PRIVACY_ENDPOINTS.account, {
    method: "DELETE",
    body: options.password === undefined ? { confirm: options.confirm } : options,
  });
}

/** The windows Settings offers, for a UI that must not invent its own list. */
export const RETENTION_LABELS: ReadonlyArray<{ days: RetentionDays; label: string }> = [
  { days: 30, label: "30 days" },
  { days: 90, label: "3 months" },
  { days: 180, label: "6 months" },
  { days: 365, label: "1 year" },
];
