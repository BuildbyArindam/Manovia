/**
 * API client tests.
 *
 * The interesting cases are the ones a real browser hits: a 401 that must be
 * answered with exactly one refresh, a stampede of 401s that must not become a
 * stampede of refreshes, and an error body that must arrive at the UI with its
 * machine-readable code intact.
 */

import { describe, expect, it, vi } from "vitest";

import {
  ApiClient,
  ApiError,
  BrowserTokenStore,
  MemoryTokenStore,
  type AuthTokens,
  type FetchImpl,
} from "@/lib/api";

interface TestUser {
  id: string;
  email: string | null;
  is_anonymous: boolean;
  language: string;
  region: string | null;
  created_at: string;
}

const SESSION = {
  access_token: "access-1",
  refresh_token: "refresh-1",
  token_type: "bearer",
  expires_in: 900,
  user: {
    id: "user-1",
    email: null,
    is_anonymous: true,
    language: "en",
    region: null,
    created_at: "2026-10-09T00:00:00Z",
  } satisfies TestUser,
};

const ROTATED_SESSION = {
  ...SESSION,
  access_token: "access-2",
  refresh_token: "refresh-2",
};

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function errorEnvelope(status: number, code: string, message: string): Response {
  return jsonResponse({ error: { code, message, request_id: "req-42" } }, status, {
    "X-Request-ID": "req-42",
  });
}

type Handler = (url: string, init?: RequestInit) => Response;

function mockFetch(handler: Handler): ReturnType<typeof vi.fn<FetchImpl>> {
  return vi.fn<FetchImpl>((url, init) => Promise.resolve(handler(url, init)));
}

function headerOf(init: RequestInit | undefined, name: string): string | undefined {
  const headers = init?.headers;
  if (headers === undefined) {
    return undefined;
  }
  return (headers as Record<string, string>)[name];
}

function makeClient(fetchImpl: FetchImpl, tokens: AuthTokens | null = null, now = 0): ApiClient {
  const store = new MemoryTokenStore();
  if (tokens !== null) {
    store.write(tokens);
  }
  return new ApiClient({ fetchImpl, tokens: store, now: () => now });
}

const FRESH_TOKENS: AuthTokens = {
  accessToken: "access-1",
  refreshToken: "refresh-1",
  tokenType: "bearer",
  expiresAt: 900_000,
};

const STALE_TOKENS: AuthTokens = {
  accessToken: "access-1",
  refreshToken: "refresh-1",
  tokenType: "bearer",
  expiresAt: 0,
};

describe("ApiClient requests", () => {
  it("prefixes the base URL, sends the bearer token, and parses JSON", async () => {
    const fetchImpl = mockFetch(() => jsonResponse({ id: "user-1" }));
    const client = makeClient(fetchImpl, FRESH_TOKENS);

    const result = await client.get<{ id: string }>("/auth/me");

    expect(result).toEqual({ id: "user-1" });
    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/api/v1/auth/me");
    expect(headerOf(fetchImpl.mock.calls[0]?.[1], "Authorization")).toBe("Bearer access-1");
  });

  it("serialises a JSON body and sets the content type", async () => {
    const fetchImpl = mockFetch(() => jsonResponse({ status: "ok" }));
    const client = makeClient(fetchImpl);

    await client.post("/consent", { grants: [] });

    const init = fetchImpl.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ grants: [] }));
    expect(headerOf(init, "Content-Type")).toBe("application/json");
  });

  it("omits the Authorization header for unauthenticated calls", async () => {
    const fetchImpl = mockFetch(() => jsonResponse({}));
    const client = makeClient(fetchImpl, FRESH_TOKENS);

    await client.get("/crisis/resources", { authenticated: false });

    expect(headerOf(fetchImpl.mock.calls[0]?.[1], "Authorization")).toBeUndefined();
  });
});

describe("ApiClient error envelope", () => {
  it("turns the backend envelope into an ApiError with its code and request id", async () => {
    const fetchImpl = mockFetch(() => errorEnvelope(403, "consent_required", "Consent required."));
    const client = makeClient(fetchImpl);

    const error = await client.get("/chat/sessions").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(403);
    expect(apiError.code).toBe("consent_required");
    expect(apiError.message).toBe("Consent required.");
    expect(apiError.requestId).toBe("req-42");
  });

  it("falls back to a status code and the status text for a non-envelope failure", async () => {
    const fetchImpl = mockFetch(
      () =>
        new Response("<html>boom</html>", {
          status: 500,
          statusText: "Internal Server Error",
        }),
    );
    const client = makeClient(fetchImpl);

    const error = (await client.get("/auth/me").catch((caught: unknown) => caught)) as ApiError;

    expect(error.code).toBe("http_500");
    expect(error.status).toBe(500);
    expect(error.message).toBe("Internal Server Error");
  });

  it("keeps FastAPI's `detail` for validation failures", async () => {
    const fetchImpl = mockFetch(() => jsonResponse({ detail: "Field required" }, 422));
    const client = makeClient(fetchImpl);

    const error = (await client.get("/auth/me").catch((caught: unknown) => caught)) as ApiError;

    expect(error.status).toBe(422);
    expect(error.message).toBe("Field required");
    expect(error.code).toBe("http_422");
  });
});

describe("ApiClient token refresh", () => {
  it("refreshes once on a 401 and replays the original request with the new token", async () => {
    const fetchImpl = mockFetch((url, init) => {
      if (url.endsWith("/auth/refresh")) {
        return jsonResponse(ROTATED_SESSION);
      }
      if (headerOf(init, "Authorization") === "Bearer access-1") {
        return errorEnvelope(401, "token_expired", "Your session has expired.");
      }
      return jsonResponse({ id: "user-1" });
    });
    const client = makeClient(fetchImpl, FRESH_TOKENS);

    const result = await client.get<{ id: string }>("/auth/me");

    expect(result).toEqual({ id: "user-1" });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    // 1: rejected, 2: refresh, 3: replay with the rotated token.
    expect(fetchImpl.mock.calls[2]?.[0]).toBe("/api/v1/auth/me");
    expect(headerOf(fetchImpl.mock.calls[2]?.[1], "Authorization")).toBe("Bearer access-2");
    expect(client.refreshTokenForSignOut()).toBe("refresh-2");
  });

  it("does not try to refresh when there is no session", async () => {
    const fetchImpl = mockFetch(() => errorEnvelope(401, "token_missing", "Sign in to continue."));
    const onAuthExpired = vi.fn();
    const client = new ApiClient({ fetchImpl, tokens: new MemoryTokenStore(), onAuthExpired });

    const error = (await client.get("/auth/me").catch((caught: unknown) => caught)) as ApiError;

    expect(error.code).toBe("token_missing");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(onAuthExpired).toHaveBeenCalledTimes(1);
  });

  it("clears the session and reports it when the refresh itself fails", async () => {
    const fetchImpl = mockFetch((url) => {
      if (url.endsWith("/auth/refresh")) {
        return errorEnvelope(401, "refresh_token_reused", "This session was already signed out.");
      }
      return errorEnvelope(401, "token_expired", "Your session has expired.");
    });
    const onAuthExpired = vi.fn();
    const store = new MemoryTokenStore();
    store.write(FRESH_TOKENS);
    const client = new ApiClient({ fetchImpl, tokens: store, onAuthExpired });

    const error = (await client.get("/auth/me").catch((caught: unknown) => caught)) as ApiError;

    expect(error.status).toBe(401);
    expect(client.hasUsableSession()).toBe(false);
    expect(client.refreshTokenForSignOut()).toBeNull();
    expect(onAuthExpired).toHaveBeenCalledTimes(1);
  });

  it("shares one refresh between concurrent 401s", async () => {
    const fetchImpl = mockFetch((url, init) => {
      if (url.endsWith("/auth/refresh")) {
        return jsonResponse(ROTATED_SESSION);
      }
      if (headerOf(init, "Authorization") === "Bearer access-1") {
        return errorEnvelope(401, "token_expired", "Your session has expired.");
      }
      return jsonResponse({ ok: true });
    });
    const client = makeClient(fetchImpl, FRESH_TOKENS);

    const results = await Promise.all([
      client.get<{ ok: boolean }>("/auth/me"),
      client.get<{ ok: boolean }>("/auth/me"),
      client.get<{ ok: boolean }>("/auth/me"),
    ]);

    expect(results).toEqual([{ ok: true }, { ok: true }, { ok: true }]);
    const refreshCalls = fetchImpl.mock.calls.filter((call) => call[0].endsWith("/auth/refresh"));
    expect(refreshCalls).toHaveLength(1);
  });

  it("refreshes a stale token before sending the request", async () => {
    const fetchImpl = mockFetch((url) => {
      if (url.endsWith("/auth/refresh")) {
        return jsonResponse(ROTATED_SESSION);
      }
      return jsonResponse({ ok: true });
    });
    const client = makeClient(fetchImpl, STALE_TOKENS);

    await client.get("/auth/me");

    expect(fetchImpl.mock.calls[0]?.[0]).toBe("/api/v1/auth/refresh");
    expect(headerOf(fetchImpl.mock.calls[1]?.[1], "Authorization")).toBe("Bearer access-2");
  });

  it("never refreshes on a 403", async () => {
    const fetchImpl = mockFetch(() => errorEnvelope(403, "consent_required", "Consent required."));
    const client = makeClient(fetchImpl, FRESH_TOKENS);

    await client.get("/chat/sessions").catch(() => undefined);

    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("replays a 401 exactly once, so a persistent 401 cannot loop", async () => {
    const fetchImpl = mockFetch((url, init) => {
      if (url.endsWith("/auth/refresh")) {
        return jsonResponse(ROTATED_SESSION);
      }
      if (headerOf(init, "Authorization") === "Bearer access-1") {
        return errorEnvelope(401, "token_expired", "Your session has expired.");
      }
      return errorEnvelope(401, "token_invalid", "Sign in to continue.");
    });
    const client = makeClient(fetchImpl, FRESH_TOKENS);

    const error = (await client.get("/auth/me").catch((caught: unknown) => caught)) as ApiError;

    // attempt, refresh, replay — and then it stops.
    expect(error.code).toBe("token_invalid");
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });
});

describe("token stores", () => {
  it("round-trips a session through the browser store", () => {
    const storage = window.localStorage;
    const store = new BrowserTokenStore(storage);

    store.write(FRESH_TOKENS);

    expect(store.read()).toEqual(FRESH_TOKENS);
    store.clear();
    expect(store.read()).toBeNull();
  });

  it("ignores anything that is not a stored session", () => {
    const storage = window.localStorage;
    const store = new BrowserTokenStore(storage);

    storage.setItem("manovia.tokens.v1", "not json");
    expect(store.read()).toBeNull();

    storage.setItem("manovia.tokens.v1", JSON.stringify({ access_token: 1 }));
    expect(store.read()).toBeNull();
  });
});
