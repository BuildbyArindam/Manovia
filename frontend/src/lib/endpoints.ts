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

/** Mirrors `ResourceKind` in `app/content/crisis.py`. */
export type CrisisResourceKind = "emergency" | "crisis_line" | "text_line" | "directory";

/** Mirrors `ContactType`: how the entry is actually reached. */
export type CrisisContactType = "call" | "text" | "chat" | "web";

export interface CrisisResource {
  id: string;
  region: string;
  /** `emergency` sorts first: immediate danger outranks every counselling line. */
  kind: CrisisResourceKind;
  name: string;
  /** The number to dial or text, exactly as it should be displayed. */
  number: string | null;
  type: CrisisContactType;
  /** The first word a text line expects (`HOME`, `SHOUT`, `CONNECT`). */
  text_keyword: string | null;
  /** Anything the caller must know to get through. */
  instructions: string | null;
  hours: string;
  languages: string[];
  audience: string | null;
  description: string;
  url: string | null;
  /** Where a person checked this entry. Every resource carries one. */
  source_url: string;
  /** ISO date this entry was last checked by a human. */
  last_verified: string;
  /** True when any field rests on a secondary or conflicting source. */
  needs_verification: boolean;
  verification_note: string | null;
  priority: number;
  /**
   * Built on the backend, so the dialling format lives in exactly one place.
   * `tel_href` is set for `call` entries, `sms_href` for `text` entries.
   */
  tel_href: string | null;
  sms_href: string | null;
}

export interface CrisisResourcesResponse {
  /** The region actually served; `null` when the whole file was requested. */
  region: string | null;
  requested_region: string | null;
  /** True when an unknown region was served the `DEFAULT` set instead. */
  fallback_used: boolean;
  version: number;
  /** ISO date the file as a whole was last checked by a human. */
  last_verified: string;
  source: string;
  disclaimer: string;
  known_regions: string[];
  /** This region's emergency number, called out so it can be shown first. */
  emergency: CrisisResource | null;
  resources: CrisisResource[];
  /** Ids flagged `needs_verification`, so a UI can label rather than assert. */
  needs_verification: string[];
}

/** Mirrors `RiskLevel.label` in `app/services/safety/base.py`. */
export type RiskLevel = "none" | "low" | "medium" | "high" | "imminent";

/** Mirrors `EscalationAction` in `app/services/safety/escalation.py`. */
export type EscalationAction =
  | "normal_reply"
  | "supportive_check_in"
  | "encourage_helpline"
  | "show_crisis_message"
  | "show_emergency_instruction"
  | "show_helplines"
  | "block_llm"
  | "record_safety_event";

/** A pre-written, already-substituted message. No placeholders survive. */
export interface CrisisMessage {
  template_id: string;
  locale: string;
  title: string;
  body: string[];
  helpline_intro: string | null;
  emergency_instruction: string | null;
  trusted_person: string | null;
  safety_steps: string[];
  closing: string | null;
  disclaimer: string | null;
}

/** Metadata about the detection — never the text that produced it. */
export interface AssessmentContext {
  first_person: boolean;
  third_person: boolean;
  quoted: boolean;
  fiction_frame: boolean;
  timeframe: boolean;
  negated_hits: number;
  figurative_hits: number;
  language: string | null;
  truncated: boolean;
  variants_searched: number;
}

export interface EscalationPolicy {
  template_id: string | null;
  actions: EscalationAction[];
  /** False at `high`/`imminent`: no model call happens for that turn. */
  allow_llm: boolean;
  deterministic_reply: boolean;
  show_helplines: boolean;
  show_emergency_instruction: boolean;
  record_event: boolean;
}

export interface AssessRequest {
  text: string;
  region?: string | null;
  locale?: string | null;
  /** Optional hint, recorded on the assessment. Never used to route. */
  language?: string | null;
}

export interface AssessResponse {
  level: RiskLevel;
  /** The four-tier database value: `high` and `imminent` both map to `crisis`. */
  stored_level: string;
  matched_categories: string[];
  /** Stable pattern ids and context codes — never fragments of the message. */
  rationale_codes: string[];
  context: AssessmentContext;
  policy: EscalationPolicy;
  /** True when the reply is addressed to a supporter, not the person in crisis. */
  about_someone_else: boolean;
  locale: string;
  locale_fallback_used: boolean;
  region: string;
  region_fallback_used: boolean;
  crisis: CrisisMessage | null;
  emergency: CrisisResource | null;
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
 *
 * With no region the whole shipped file comes back; with one, that region's list
 * plus the global directories and its own emergency number. An unknown region is
 * served the `DEFAULT` set with `fallback_used: true` rather than an error.
 */
export async function fetchCrisisResources(
  client: ApiClient = defaultClient,
  region?: string | null,
): Promise<CrisisResourcesResponse> {
  const query = region ? `?region=${encodeURIComponent(region)}` : "";
  return client.get<CrisisResourcesResponse>(`/crisis/resources${query}`, {
    authenticated: false,
  });
}

/**
 * Assess one message with the backend rules engine (no model call involved).
 *
 * Also public: the thing that tells you whether you need a helpline must not be
 * behind a sign-in. The response never echoes the input text — only the level,
 * the policy, pre-written copy and the region's helplines.
 */
export async function assessCrisis(
  payload: AssessRequest,
  client: ApiClient = defaultClient,
): Promise<AssessResponse> {
  return client.post<AssessResponse>("/crisis/assess", payload, { authenticated: false });
}
