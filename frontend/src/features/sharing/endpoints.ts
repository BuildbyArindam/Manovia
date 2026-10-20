/**
 * The share bridge wire calls.
 *
 * Two groups, on opposite sides of the auth boundary:
 *
 * * the **owner** calls (`/shares/...`) go through the normal client, so they
 *   carry the access token and are behind the TERMS + PRIVACY consent gate;
 * * the **viewer** calls (`/public/shares/...`) are made by somebody with no
 *   Manovia account — a professional opening a link. They must not send a token
 *   and must not be redirected into onboarding when they fail, so they are made
 *   with a client whose token store is empty and whose 401 handling cannot fire
 *   (the endpoints never answer 401; an unusable link is a 404).
 *
 * Nothing here logs a token, a key or a passcode: they are credentials.
 */

import { api as defaultClient, type ApiClient } from "@/lib/api";

import type {
  AuditOut,
  CreateShareBody,
  CreatedShare,
  OpenOut,
  PreferenceOut,
  PreviewOut,
  PublicStatusOut,
  RevokeAllOut,
  RevokeOut,
  ShareItem,
  ShareOptions,
  ShareSelectionBody,
  ShareSummary,
} from "./types";

export const SHARES = "/shares";
export const PUBLIC_SHARES = "/public/shares";

/** The path a viewer opens. The key goes in the fragment, never in the path. */
export function sharePath(token: string): string {
  return `/s/${token}`;
}

/** The URL to copy: path plus the one-time key fragment. */
export function shareUrl(token: string, secret: string, origin = ""): string {
  return `${origin}${sharePath(token)}#k=${secret}`;
}

// --------------------------------------------------------------------------- //
// owner
// --------------------------------------------------------------------------- //

/** The catalogue, the deployment's limits, and the records that can be picked. */
export async function fetchShareOptions(client: ApiClient = defaultClient): Promise<ShareOptions> {
  return client.get<ShareOptions>(`${SHARES}/options`);
}

/** Is sharing switched on for this person? */
export async function fetchSharePreference(
  client: ApiClient = defaultClient,
): Promise<PreferenceOut> {
  return client.get<PreferenceOut>(`${SHARES}/preferences`);
}

/** Turn sharing on or off. Off is the default and off is reversible. */
export async function saveSharePreference(
  enabled: boolean,
  client: ApiClient = defaultClient,
): Promise<PreferenceOut> {
  return client.post<PreferenceOut>(`${SHARES}/preferences`, { enabled }, undefined);
}

/** Build the document without storing it: the preview and the consent hash. */
export async function previewShare(
  body: ShareSelectionBody,
  client: ApiClient = defaultClient,
): Promise<PreviewOut> {
  return client.post<PreviewOut>(`${SHARES}/preview`, body, undefined);
}

/** Create the link. The response carries the key and passcode exactly once. */
export async function createShare(
  body: CreateShareBody,
  client: ApiClient = defaultClient,
): Promise<CreatedShare> {
  return client.post<CreatedShare>(SHARES, body, undefined);
}

/** Every link this person has made, newest first. */
export async function listShares(client: ApiClient = defaultClient): Promise<ShareSummary[]> {
  return client.get<ShareSummary[]>(SHARES);
}

/** One link, for the detail view. */
export async function fetchShare(
  shareId: string,
  client: ApiClient = defaultClient,
): Promise<ShareSummary> {
  return client.get<ShareSummary>(`${SHARES}/${shareId}`);
}

/** The audit trail: what happened to this link, and how many times. */
export async function fetchShareAudit(
  shareId: string,
  client: ApiClient = defaultClient,
): Promise<AuditOut> {
  return client.get<AuditOut>(`${SHARES}/${shareId}/audit`);
}

/** Kill one link. */
export async function revokeShare(
  shareId: string,
  client: ApiClient = defaultClient,
): Promise<RevokeOut> {
  return client.post<RevokeOut>(`${SHARES}/${shareId}/revoke`, undefined, undefined);
}

/** Kill every live link in one click. */
export async function revokeAllShares(client: ApiClient = defaultClient): Promise<RevokeAllOut> {
  return client.post<RevokeAllOut>(`${SHARES}/revoke-all`, undefined, undefined);
}

// --------------------------------------------------------------------------- //
// viewer (no account)
// --------------------------------------------------------------------------- //

/** Is this link usable, and will it ask for a passcode? */
export async function fetchPublicStatus(
  token: string,
  client: ApiClient = defaultClient,
): Promise<PublicStatusOut> {
  return client.get<PublicStatusOut>(`${PUBLIC_SHARES}/${encodeURIComponent(token)}`);
}

/**
 * Hand over the ciphertext.
 *
 * This spends one view, so it is called once per opening and its result is kept
 * in memory: re-opening the document does not cost the person another view.
 */
export async function openPublicShare(
  token: string,
  passcode: string,
  client: ApiClient = defaultClient,
): Promise<OpenOut> {
  return client.post<OpenOut>(
    `${PUBLIC_SHARES}/${encodeURIComponent(token)}/open`,
    { passcode },
    undefined,
  );
}

// --------------------------------------------------------------------------- //
// selection helpers shared by the builder and its tests
// --------------------------------------------------------------------------- //

/** The items a fresh builder starts with: none. Sharing is opt-in per item. */
export const EMPTY_SELECTION: ShareItem[] = [];

/** Error codes the UI shows curated copy for. */
export const SHARE_ERROR_CODES = {
  disabled: "sharing_disabled",
  notFound: "share_not_found",
  unavailable: "share_unavailable",
  requestInvalid: "share_request_invalid",
  consentMismatch: "consent_mismatch",
  limit: "share_limit",
  passcodeIncorrect: "passcode_incorrect",
  passcodeLocked: "passcode_locked",
  rateLimited: "rate_limited",
} as const;
