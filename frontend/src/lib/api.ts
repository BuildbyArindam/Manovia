/**
 * The typed API client.
 *
 * Responsibilities, in order of importance:
 *
 * 1. **Error envelope handling.** Every failure becomes an {@link ApiError}
 *    carrying the backend's `{error: {code, message, request_id}}` envelope, so
 *    the UI can show a curated message and quote a request id instead of
 *    guessing from an HTTP status.
 * 2. **Token refresh.** A 401 (or an access token that is about to expire)
 *    triggers exactly one `POST /auth/refresh`; concurrent callers share that
 *    single in-flight refresh instead of stampeding the endpoint. A refresh
 *    that fails clears the session and notifies the app, which can then send
 *    the user back through onboarding.
 * 3. **No implicit behaviour.** The client never retries anything except a
 *    single post-refresh replay of the request that failed, never follows
 *    redirects on its own, and never logs tokens or bodies.
 *
 * The token store is injected, so tests use an in-memory store and the browser
 * uses `localStorage` (see the trade-off note in `BrowserTokenStore`).
 */

/** Default origin-relative base URL: the dev server proxies `/api` to FastAPI. */
export const DEFAULT_API_BASE_URL = "/api/v1";

/** How long before `expires_at` a token is considered stale and refreshed. */
export const DEFAULT_REFRESH_SKEW_MS = 30_000;

/** The header the backend uses to correlate a response with its log line. */
export const REQUEST_ID_HEADER = "X-Request-ID";

/** localStorage key for the browser token store. */
export const TOKEN_STORAGE_KEY = "manovia.tokens.v1";

/** The backend's error envelope: `{"error": {"code", "message", "request_id"}}`. */
export interface ApiErrorEnvelope {
  error: {
    code: string;
    message: string;
    request_id: string;
  };
}

/** Any failure surfaced to the UI, with the machine-readable code preserved. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string;

  constructor(status: number, code: string, message: string, requestId: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (!isRecord(value)) {
    return false;
  }
  const body = value.error;
  if (!isRecord(body)) {
    return false;
  }
  return typeof body.code === "string" && typeof body.message === "string";
}

/** The minimum a token endpoint must return for the client to hold a session. */
export interface SessionLike {
  access_token: string;
  refresh_token: string;
  expires_in?: number;
}

/** What the client keeps about a session. `expiresAt` is epoch milliseconds. */
export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  expiresAt: number;
}

export interface TokenStore {
  read(): AuthTokens | null;
  write(tokens: AuthTokens): void;
  clear(): void;
}

/** Session storage for tests and for a tab that must forget on reload. */
export class MemoryTokenStore implements TokenStore {
  private tokens: AuthTokens | null = null;

  read(): AuthTokens | null {
    return this.tokens;
  }

  write(tokens: AuthTokens): void {
    this.tokens = tokens;
  }

  clear(): void {
    this.tokens = null;
  }
}

/**
 * `localStorage`-backed session storage.
 *
 * Trade-off, recorded on purpose: a token in web storage is readable by any
 * script that manages to run on the page (an XSS bug would leak a session).
 * The alternative — an httpOnly, `Secure`, `SameSite` refresh cookie — is a
 * backend change (CSRF protection, `credentials: "include"`, CORS with
 * credentials) and is listed in the Day 5 parking lot. Until then the refresh
 * token lives here and the access token is short-lived by design.
 */
export class BrowserTokenStore implements TokenStore {
  constructor(
    private readonly storage: Storage = window.localStorage,
    private readonly key: string = TOKEN_STORAGE_KEY,
  ) {}

  read(): AuthTokens | null {
    try {
      const raw = this.storage.getItem(this.key);
      if (raw === null) {
        return null;
      }
      const parsed: unknown = JSON.parse(raw);
      if (!isRecord(parsed)) {
        return null;
      }
      const { accessToken, refreshToken, tokenType, expiresAt } = parsed;
      if (typeof accessToken !== "string" || typeof refreshToken !== "string") {
        return null;
      }
      if (typeof expiresAt !== "number" || !Number.isFinite(expiresAt)) {
        return null;
      }
      return {
        accessToken,
        refreshToken,
        tokenType: typeof tokenType === "string" ? tokenType : "bearer",
        expiresAt,
      };
    } catch {
      // Unreadable or unavailable storage is not an error the user should see.
      return null;
    }
  }

  write(tokens: AuthTokens): void {
    try {
      this.storage.setItem(this.key, JSON.stringify(tokens));
    } catch {
      // Private mode, quota, or a blocked origin: the session still works in
      // memory for this tab.
    }
  }

  clear(): void {
    try {
      this.storage.removeItem(this.key);
    } catch {
      // Nothing to do — the session is gone from the client either way.
    }
  }
}

export type FetchImpl = (input: string, init?: RequestInit) => Promise<Response>;

export interface RequestOptions {
  /** Attach the bearer token and refresh it on 401. Default: `true`. */
  authenticated?: boolean;
  signal?: AbortSignal | undefined;
  headers?: Record<string, string> | undefined;
}

export interface ApiClientOptions {
  baseUrl?: string;
  fetchImpl?: FetchImpl;
  tokens?: TokenStore;
  /** Injectable clock; tests drive token expiry without waiting. */
  now?: () => number;
  /** Called once when a session could not be refreshed. */
  onAuthExpired?: (error: ApiError) => void;
  refreshSkewMs?: number;
}

interface InternalRequest extends RequestOptions {
  method: string;
  body?: unknown;
}

async function toApiError(response: Response): Promise<ApiError> {
  const requestId = response.headers.get(REQUEST_ID_HEADER) ?? "unknown";
  let code = `http_${response.status}`;
  let message =
    response.statusText === "" ? "Something went wrong. Please try again." : response.statusText;
  const text = await response.text().catch(() => "");
  if (text !== "") {
    try {
      const payload: unknown = JSON.parse(text);
      if (isErrorEnvelope(payload)) {
        code = payload.error.code;
        message = payload.error.message;
      } else if (isRecord(payload) && typeof payload.detail === "string") {
        // FastAPI's own validation/HTTP errors, which do not use the envelope.
        message = payload.detail;
      }
    } catch {
      // HTML or plain text: the status text is the honest answer.
    }
  }
  return new ApiError(response.status, code, message, requestId);
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: FetchImpl;
  private readonly tokens: TokenStore;
  private readonly now: () => number;
  private readonly onAuthExpired: ((error: ApiError) => void) | undefined;
  private readonly refreshSkewMs: number;
  private refreshInFlight: Promise<boolean> | null = null;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? DEFAULT_API_BASE_URL).replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init));
    this.tokens = options.tokens ?? new MemoryTokenStore();
    this.now = options.now ?? (() => Date.now());
    this.onAuthExpired = options.onAuthExpired;
    this.refreshSkewMs = options.refreshSkewMs ?? DEFAULT_REFRESH_SKEW_MS;
  }

  /** Perform one request. Prefer {@link get} / {@link post}. */
  async request<T>(path: string, init: InternalRequest): Promise<T> {
    return this.send<T>(path, init, true);
  }

  async get<T>(path: string, options?: RequestOptions): Promise<T> {
    return this.send<T>(path, { ...options, method: "GET" }, true);
  }

  async post<T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> {
    return this.send<T>(path, { ...options, method: "POST", body }, true);
  }

  /** Store the tokens from a sign-in/refresh response. */
  applySession(payload: SessionLike): AuthTokens {
    const tokens = toAuthTokens(payload, this.now());
    if (tokens === null) {
      throw new Error("A session response must carry access_token and refresh_token");
    }
    this.tokens.write(tokens);
    return tokens;
  }

  clearSession(): void {
    this.tokens.clear();
  }

  /** The stored refresh token, for `POST /auth/logout` (which needs it). */
  refreshTokenForSignOut(): string | null {
    return this.tokens.read()?.refreshToken ?? null;
  }

  /** True when a session exists and its access token is not stale. */
  hasUsableSession(): boolean {
    const tokens = this.tokens.read();
    return tokens !== null && tokens.expiresAt - this.now() > this.refreshSkewMs;
  }

  /**
   * Refresh the session, once. Concurrent callers await the same promise, so a
   * page that fires five requests on an expired token makes one refresh call.
   */
  async refreshAccessToken(): Promise<boolean> {
    if (this.refreshInFlight !== null) {
      return this.refreshInFlight;
    }
    const attempt = this.performRefresh().finally(() => {
      this.refreshInFlight = null;
    });
    this.refreshInFlight = attempt;
    return attempt;
  }

  private async performRefresh(): Promise<boolean> {
    const tokens = this.tokens.read();
    if (tokens === null || tokens.refreshToken === "") {
      this.tokens.clear();
      return false;
    }
    try {
      const response = await this.fetchImpl(this.url("/auth/refresh"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: tokens.refreshToken }),
      });
      if (!response.ok) {
        // Expired, revoked, or reused: the family is gone, so drop the session.
        this.tokens.clear();
        return false;
      }
      const payload: unknown = await response.json();
      const rotated = toAuthTokens(payload, this.now());
      if (rotated === null) {
        this.tokens.clear();
        return false;
      }
      this.tokens.write(rotated);
      return true;
    } catch {
      this.tokens.clear();
      return false;
    }
  }

  private async send<T>(path: string, init: InternalRequest, allowRefresh: boolean): Promise<T> {
    const headers: Record<string, string> = { ...init.headers };
    let body: string | undefined;
    if (init.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(init.body);
    }

    const authenticated = init.authenticated !== false;
    if (authenticated) {
      const tokens = await this.tokensForRequest();
      if (tokens !== null) {
        headers.Authorization = `Bearer ${tokens.accessToken}`;
      }
    }

    const response = await this.fetchImpl(this.url(path), {
      method: init.method,
      headers,
      body,
      signal: init.signal,
    });

    if (response.status === 401 && allowRefresh && authenticated) {
      const refreshed = await this.refreshAccessToken();
      if (refreshed) {
        // Replay the original request exactly once, with the new token.
        return this.send<T>(path, init, false);
      }
    }

    if (!response.ok) {
      const error = await toApiError(response);
      if (response.status === 401 && authenticated) {
        this.onAuthExpired?.(error);
      }
      throw error;
    }

    if (response.status === 204) {
      return undefined as T;
    }
    const text = await response.text();
    return (text === "" ? undefined : JSON.parse(text)) as T;
  }

  /**
   * The tokens to send, refreshing first when the access token is stale.
   * Returns the (possibly expired) stored tokens when a refresh is not
   * possible, so the server gets the chance to answer with a 401 the caller can
   * turn into "sign in again".
   */
  private async tokensForRequest(): Promise<AuthTokens | null> {
    const tokens = this.tokens.read();
    if (tokens === null || tokens.refreshToken === "") {
      return tokens;
    }
    if (tokens.expiresAt - this.now() > this.refreshSkewMs) {
      return tokens;
    }
    const refreshed = await this.refreshAccessToken();
    return refreshed ? this.tokens.read() : tokens;
  }

  private url(path: string): string {
    return `${this.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
  }
}

/** Read a token pair out of a sign-in/refresh body, or `null` if it is not one. */
function toAuthTokens(payload: unknown, now: number): AuthTokens | null {
  if (!isRecord(payload)) {
    return null;
  }
  const { access_token: accessToken, refresh_token: refreshToken, expires_in: expiresIn } = payload;
  if (typeof accessToken !== "string" || accessToken === "") {
    return null;
  }
  if (typeof refreshToken !== "string" || refreshToken === "") {
    return null;
  }
  const seconds =
    typeof expiresIn === "number" && Number.isFinite(expiresIn) && expiresIn > 0 ? expiresIn : 900;
  return {
    accessToken,
    refreshToken,
    tokenType: "bearer",
    expiresAt: now + seconds * 1000,
  };
}

/** Build a client for the browser: `/api/v1` plus a persistent token store. */
export function createApiClient(options: ApiClientOptions = {}): ApiClient {
  const baseUrl =
    options.baseUrl ??
    (import.meta.env.VITE_API_BASE_URL === undefined
      ? DEFAULT_API_BASE_URL
      : import.meta.env.VITE_API_BASE_URL);
  return new ApiClient({
    ...options,
    baseUrl,
    tokens: options.tokens ?? new BrowserTokenStore(),
  });
}

/** The application-wide client. */
export const api: ApiClient = createApiClient();
