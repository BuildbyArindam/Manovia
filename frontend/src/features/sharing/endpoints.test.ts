/**
 * The share endpoints' wire contract.
 *
 * Two things are worth pinning down here rather than in a component test: the
 * shape of the link that gets copied into a message (path plus fragment, never a
 * query string), and the fact that a token is escaped on its way into a URL —
 * a token is untrusted input from somebody else's clipboard.
 */

import { describe, expect, it } from "vitest";

import { ApiClient, MemoryTokenStore } from "@/lib/api";

import {
  PUBLIC_SHARES,
  SHARE_ERROR_CODES,
  SHARES,
  createShare,
  fetchPublicStatus,
  fetchShareAudit,
  fetchShareOptions,
  listShares,
  openPublicShare,
  previewShare,
  revokeAllShares,
  revokeShare,
  saveSharePreference,
  sharePath,
  shareUrl,
} from "./endpoints";
import type { CreateShareBody, ShareSelectionBody } from "./types";

interface Recorded {
  url: string;
  method: string;
  body: string | null;
  headers: Record<string, string>;
}

function recordingClient(recorded: Recorded[]) {
  const store = new MemoryTokenStore();
  store.write({
    accessToken: "access-token",
    refreshToken: "refresh-token",
    tokenType: "bearer",
    expiresAt: Date.now() + 3_600_000,
  });
  return new ApiClient({
    tokens: store,
    baseUrl: "/api/v1",
    fetchImpl: async (input, init) => {
      const headers: Record<string, string> = {};
      new Headers(init?.headers).forEach((value, key) => {
        headers[key] = value;
      });
      recorded.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: init?.body === undefined ? null : String(init.body),
        headers,
      });
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
}

const SELECTION: ShareSelectionBody = { items: ["note"], window_days: 30, note: "hello" };
const CREATE: CreateShareBody = {
  ...SELECTION,
  formats: ["pdf"],
  expires_in_days: 7,
  view_limit: 5,
  passcode: null,
  consent_sha256: "a".repeat(64),
};

describe("share endpoints", () => {
  it("builds the link with the key in the fragment and nowhere else", () => {
    const token = "Zk9xT7vLmQ2sRbNcWf1YdA";
    const secret = "bWFub3ZpYS1zaGFyZS12ZWN0b3Itc2VjcmV0LWtleSE";
    expect(sharePath(token)).toBe(`/s/${token}`);
    expect(shareUrl(token, secret)).toBe(`/s/${token}#k=${secret}`);
    expect(shareUrl(token, secret, "https://manovia.app")).toBe(
      `https://manovia.app/s/${token}#k=${secret}`,
    );
    const url = new URL(shareUrl(token, secret, "https://manovia.app"));
    // The token is in the path; the key is only in the hash, which a browser
    // does not send to a server, does not put in Referer, and does not log.
    expect(url.pathname).toBe(`/s/${token}`);
    expect(url.search).toBe("");
    expect(url.hash).toBe(`#k=${secret}`);
    expect(url.pathname).not.toContain(secret);
  });

  it("calls the owner endpoints with the token in the header, not the URL", async () => {
    const recorded: Recorded[] = [];
    const client = recordingClient(recorded);

    await fetchShareOptions(client);
    await saveSharePreference(true, client);
    await previewShare(SELECTION, client);
    await createShare(CREATE, client);
    await listShares(client);
    await fetchShareAudit("share-1", client);
    await revokeShare("share-1", client);
    await revokeAllShares(client);

    expect(recorded.map((call) => `${call.method} ${call.url}`)).toEqual([
      `GET /api/v1${SHARES}/options`,
      `POST /api/v1${SHARES}/preferences`,
      `POST /api/v1${SHARES}/preview`,
      `POST /api/v1${SHARES}`,
      `GET /api/v1${SHARES}`,
      `GET /api/v1${SHARES}/share-1/audit`,
      `POST /api/v1${SHARES}/share-1/revoke`,
      `POST /api/v1${SHARES}/revoke-all`,
    ]);
    for (const call of recorded) {
      expect(call.headers.authorization).toBe("Bearer access-token");
      expect(call.url).not.toContain("access-token");
    }
    // The create carries the consent hash from the preview, verbatim.
    const create = recorded[3];
    expect(create?.body).not.toBeNull();
    expect(JSON.parse(create?.body ?? "{}")).toEqual(CREATE);
    // And a preference change is exactly the one field the API accepts.
    expect(JSON.parse(recorded[1]?.body ?? "{}")).toEqual({ enabled: true });
  });

  it("calls the public endpoints without a token and escapes the path", async () => {
    const recorded: Recorded[] = [];
    const store = new MemoryTokenStore();
    const client = new ApiClient({
      tokens: store,
      baseUrl: "/api/v1",
      fetchImpl: async (input, init) => {
        recorded.push({
          url: String(input),
          method: init?.method ?? "GET",
          body: init?.body === undefined ? null : String(init.body),
          headers: {},
        });
        return new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    });

    await fetchPublicStatus("token one/../two", client);
    await openPublicShare("token one/../two", "483920", client);

    expect(recorded.map((call) => `${call.method} ${call.url}`)).toEqual([
      `GET /api/v1${PUBLIC_SHARES}/token%20one%2F..%2Ftwo`,
      `POST /api/v1${PUBLIC_SHARES}/token%20one%2F..%2Ftwo/open`,
    ]);
    // Nothing that could be mistaken for a path separator survives encoding.
    expect(recorded[0]?.url).not.toContain("/../");
    expect(JSON.parse(recorded[1]?.body ?? "{}")).toEqual({ passcode: "483920" });
  });

  it("names the error codes the UI writes copy for", () => {
    // These strings are the backend's; a rename there must break here.
    expect(SHARE_ERROR_CODES).toEqual({
      disabled: "sharing_disabled",
      notFound: "share_not_found",
      unavailable: "share_unavailable",
      requestInvalid: "share_request_invalid",
      consentMismatch: "consent_mismatch",
      limit: "share_limit",
      passcodeIncorrect: "passcode_incorrect",
      passcodeLocked: "passcode_locked",
      rateLimited: "rate_limited",
    });
  });
});
