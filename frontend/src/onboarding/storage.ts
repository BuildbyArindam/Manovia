/**
 * Where onboarding lives between sessions.
 *
 * The record is deliberately small and versioned: if the shape ever changes,
 * an old record fails validation and the user is simply asked again rather than
 * crashing the app on boot.
 */

import { ALL_CONSENTS, type ConsentKind } from "@/lib/endpoints";

export const ONBOARDING_STORAGE_KEY = "manovia.onboarding.v1";

export type ConsentState = Record<ConsentKind, boolean>;

export type AuthMode = "guest" | "account";

export interface OnboardingUser {
  id: string;
  email: string | null;
  isAnonymous: boolean;
}

export interface OnboardingRecord {
  version: 1;
  completedAt: string;
  consents: ConsentState;
  authMode: AuthMode;
  user: OnboardingUser;
}

/** Every box unticked. The only state onboarding may start from. */
export const NO_CONSENTS: ConsentState = {
  terms: false,
  privacy: false,
  ai_disclosure: false,
  store_chat: false,
};

function isConsentState(value: unknown): value is ConsentState {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return ALL_CONSENTS.every((kind) => typeof record[kind] === "boolean");
}

function isOnboardingRecord(value: unknown): value is OnboardingRecord {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    record.version === 1 &&
    typeof record.completedAt === "string" &&
    isConsentState(record.consents) &&
    (record.authMode === "guest" || record.authMode === "account") &&
    typeof record.user === "object" &&
    record.user !== null
  );
}

export function loadOnboardingRecord(): OnboardingRecord | null {
  try {
    const raw = window.localStorage.getItem(ONBOARDING_STORAGE_KEY);
    if (raw === null) {
      return null;
    }
    const parsed: unknown = JSON.parse(raw);
    return isOnboardingRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function saveOnboardingRecord(record: OnboardingRecord): void {
  try {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Storage may be unavailable; the session still works for this tab.
  }
}

export function clearOnboardingRecord(): void {
  try {
    window.localStorage.removeItem(ONBOARDING_STORAGE_KEY);
  } catch {
    // Nothing to remove.
  }
}
