/**
 * Day 25 regression: being offline is not being signed out.
 *
 * Before Day 25, `performRefresh()` caught *every* error and cleared the token
 * store. A refresh that failed because the phone had no signal therefore
 * destroyed the session — and for a guest (no email, no password) a destroyed
 * session is a lost account: their mood history and journal become unreachable
 * forever. The access token lives 15 minutes, so the first request made after
 * a quarter of an hour offline was enough to trigger it.
 *
 * The rule these tests pin: a refresh that the *server* rejects still clears the
 * session (that behaviour is covered in `api.test.ts` and unchanged), but a
 * refresh that never reached the server keeps it, reports a network failure,
 * and never fires `onAuthExpired`.
 */

import { describe, expect, it, vi } from "vitest";

import {
  ApiClient,
  ApiError,
  MemoryTokenStore,
  NetworkUnavailableError,
  isNetworkFailure,
  type AuthTokens,
  type FetchImpl,
} from "@/lib/api";

const STALE: AuthTokens = {
  accessToken: "access-1",
  refreshToken: "refresh-1",
  tokenType: "bearer",
  // `now` is pinned far past this, so every request refreshes first.
  expiresAt: 1_000,
};

function offlineFetch(): ReturnType<typeof vi.fn<FetchImpl>> {
  return vi.fn<FetchImpl>(() => Promise.reject(new TypeError("Failed to fetch")));
}

function clientWith(fetchImpl: FetchImpl, onAuthExpired = vi.fn()) {
  const store = new MemoryTokenStore();
  store.write(STALE);
  const client = new ApiClient({ fetchImpl, tokens: store, now: () => 10_000_000, onAuthExpired });
  return { client, store, onAuthExpired };
}

describe("a refresh that fails on the network keeps the session", () => {
  it("does not clear the stored tokens when the refresh request cannot be sent", async () => {
    const { client, store } = clientWith(offlineFetch());

    const refreshed = await client.refreshAccessToken();

    expect(refreshed).toBe(false);
    expect(store.read()).toEqual(STALE);
    expect(client.refreshTokenForSignOut()).toBe("refresh-1");
  });

  it("surfaces an offline request as a network failure, not an auth error", async () => {
    const { client, store, onAuthExpired } = clientWith(offlineFetch());

    const error = await client.get("/mood/streak").catch((caught: unknown) => caught);

    expect(isNetworkFailure(error)).toBe(true);
    expect(error).not.toBeInstanceOf(ApiError);
    expect(onAuthExpired).not.toHaveBeenCalled();
    expect(store.read()?.refreshToken).toBe("refresh-1");
  });

  it("does not report an expired session when a 401 is followed by an offline refresh", async () => {
    // The flaky-network case: the request reached the server (401, stale token)
    // but the refresh a moment later did not get out.
    const fetchImpl = vi.fn<FetchImpl>((url) => {
      if (url.endsWith("/auth/refresh")) {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            error: { code: "token_expired", message: "Expired.", request_id: "r" },
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      );
    });
    const store = new MemoryTokenStore();
    store.write({ ...STALE, expiresAt: 10_000_000_000 }); // fresh, so no pre-refresh
    const onAuthExpired = vi.fn();
    const client = new ApiClient({ fetchImpl, tokens: store, now: () => 0, onAuthExpired });

    const error = await client.get("/auth/me").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(NetworkUnavailableError);
    expect(onAuthExpired).not.toHaveBeenCalled();
    expect(store.read()?.refreshToken).toBe("refresh-1");
  });

  it("recovers on reconnect: the kept refresh token rotates and the request succeeds", async () => {
    let online = false;
    const fetchImpl = vi.fn<FetchImpl>((url, init) => {
      if (!online) {
        return Promise.reject(new TypeError("Failed to fetch"));
      }
      if (url.endsWith("/auth/refresh")) {
        const body = JSON.parse(String(init?.body)) as { refresh_token: string };
        expect(body.refresh_token).toBe("refresh-1");
        return Promise.resolve(
          new Response(
            JSON.stringify({
              access_token: "access-2",
              refresh_token: "refresh-2",
              expires_in: 900,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    });
    const { client } = clientWith(fetchImpl);

    await client.get("/mood/streak").catch(() => undefined);
    online = true;
    const result = await client.get<{ ok: boolean }>("/mood/streak");

    expect(result).toEqual({ ok: true });
    expect(client.refreshTokenForSignOut()).toBe("refresh-2");
  });

  it("still clears the session when the server answers the refresh with garbage", async () => {
    const fetchImpl = vi.fn<FetchImpl>(() =>
      Promise.resolve(new Response("<html>proxy error</html>", { status: 200 })),
    );
    const { client, store } = clientWith(fetchImpl);

    expect(await client.refreshAccessToken()).toBe(false);
    expect(store.read()).toBeNull();
  });
});

describe("isNetworkFailure", () => {
  it("is true for a rejected fetch and for NetworkUnavailableError only", () => {
    expect(isNetworkFailure(new TypeError("Failed to fetch"))).toBe(true);
    expect(isNetworkFailure(new NetworkUnavailableError())).toBe(true);
    expect(isNetworkFailure(new ApiError(503, "safety_unavailable", "x", "r"))).toBe(false);
    expect(isNetworkFailure(new ApiError(0, "http_0", "x", "r"))).toBe(false);
    expect(isNetworkFailure(new Error("other"))).toBe(false);
  });
});
