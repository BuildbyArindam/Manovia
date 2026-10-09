/**
 * Typed endpoint wrappers.
 *
 * The wire shapes live here (and only here) so a backend rename is a
 * compile error rather than a runtime surprise. Every function takes the
 * client as an optional last argument, which is how the tests inject a client
 * backed by a fake `fetch`.
 */

import type { ApiClient } from "@/lib/api";
import { api as defaultClient } from "@/lib/api";

/** Mirrors `app.models.enums.ConsentKind` on the backend. */
export type ConsentKind = "terms" | "privacy" | "ai_disclosure" | "store_chat";

/** The consents onboarding must hold before the app can be used. */
export const REQUIRED_CONSENTS: readonly ConsentKind[] = ["terms", "privacy", "ai_disclosure"];

/** The one optional consent (storing chat history). */
export const OPTIONAL_CONSENTS: readonly ConsentKind[] = ["store_chat"];

export const ALL_CONSENTS: readonly ConsentKind[] = [...REQUIRED_CONSENTS, ...OPTIONAL_CONSENTS];

export interface UserProfile {
  id: string;
  email: string | null;
  is_anonymous: boolean;
  language: string;
  region: string | null;
  created_at: string;
}

export interface SessionResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: UserProfile;
}

export interface ConsentRequirement {
  kind: ConsentKind;
  version: string;
  title: string;
  summary: string;
  /** Only the AI disclosure carries the full onboarding text. */
  text?: string;
}

export interface ConsentRequirementsResponse {
  documents: ConsentRequirement[];
}

export interface ConsentGrant {
  kind: ConsentKind;
  version: string;
  granted: boolean;
}

export interface ConsentResponse {
  recorded: Array<{ kind: ConsentKind; version: string; granted: boolean; created_at: string }>;
}

export interface CrisisResource {
  id: string;
  region: string;
  name: string;
  phone: string | null;
  sms: string | null;
  url: string | null;
  hours: string;
  description: string;
  priority: number;
}

export interface CrisisResourcesResponse {
  /** ISO date the content was last checked by a human. */
  last_verified: string;
  disclaimer: string;
  resources: CrisisResource[];
}

export interface Credentials {
  email: string;
  password: string;
}

const AUTH = {
  guest: "/auth/guest",
  register: "/auth/register",
  login: "/auth/login",
  logout: "/auth/logout",
  me: "/auth/me",
} as const;

/** The current consent document set, including the AI disclosure text. */
export async function fetchConsentRequirements(
  client: ApiClient = defaultClient,
): Promise<ConsentRequirementsResponse> {
  return client.get<ConsentRequirementsResponse>("/consent/requirements");
}

/** Record the onboarding decisions. Requires an authenticated session. */
export async function recordConsents(
  client: ApiClient,
  grants: readonly ConsentGrant[],
): Promise<ConsentResponse> {
  return client.post<ConsentResponse>("/consent", { grants: [...grants] });
}

/** Sign in as an anonymous user: a real account, no email, no password. */
export async function createGuestSession(
  client: ApiClient = defaultClient,
): Promise<SessionResponse> {
  const session = await client.post<SessionResponse>(AUTH.guest);
  client.applySession(session);
  return session;
}

export async function registerAccount(
  credentials: Credentials,
  client: ApiClient = defaultClient,
): Promise<SessionResponse> {
  const session = await client.post<SessionResponse>(AUTH.register, credentials);
  client.applySession(session);
  return session;
}

export async function signIn(
  credentials: Credentials,
  client: ApiClient = defaultClient,
): Promise<SessionResponse> {
  const session = await client.post<SessionResponse>(AUTH.login, credentials);
  client.applySession(session);
  return session;
}

/** Idempotent on the server; always clears the local session. */
export async function signOut(client: ApiClient = defaultClient): Promise<void> {
  const refreshToken = client.refreshTokenForSignOut();
  try {
    if (refreshToken !== null) {
      await client.post<{ status: string }>(AUTH.logout, { refresh_token: refreshToken });
    }
  } finally {
    client.clearSession();
  }
}

export async function fetchProfile(client: ApiClient = defaultClient): Promise<UserProfile> {
  return client.get<UserProfile>(AUTH.me);
}

/**
 * Crisis helplines. Public on purpose: someone in trouble has not signed in and
 * must never be asked to. Cached by TanStack Query.
 */
export async function fetchCrisisResources(
  client: ApiClient = defaultClient,
): Promise<CrisisResourcesResponse> {
  return client.get<CrisisResourcesResponse>("/crisis/resources", { authenticated: false });
}
