/**
 * Onboarding state: what has been agreed to, and by which kind of account.
 *
 * The rule this module exists to enforce (AGENTS.md safety rule 3, and the
 * backend's `require_consent` gate): **onboarding cannot complete without the
 * required consents**. `complete()` checks that itself rather than trusting the
 * UI to have disabled a button, so a future refactor of the flow cannot quietly
 * drop the gate. `OnboardingFlow.test.tsx` asserts both halves of it.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { api, isApiError } from "@/lib/api";
import {
  ALL_CONSENTS,
  REQUIRED_CONSENTS,
  createGuestSession,
  fetchConsentRequirements,
  recordConsents,
  registerAccount,
  type ConsentKind,
  type Credentials,
} from "@/lib/endpoints";
import { FALLBACK_CONSENT_VERSION } from "./copy";
import {
  clearOnboardingRecord,
  loadOnboardingRecord,
  saveOnboardingRecord,
  type AuthMode,
  type ConsentState,
  type OnboardingRecord,
} from "./storage";

export interface CompleteOnboardingInput {
  consents: ConsentState;
  authMode: AuthMode;
  credentials?: Credentials;
}

export type CompleteResult = { ok: true; record: OnboardingRecord } | { ok: false; error: string };

interface OnboardingContextValue {
  record: OnboardingRecord | null;
  isComplete: boolean;
  complete: (input: CompleteOnboardingInput) => Promise<CompleteResult>;
  reset: () => void;
}

const OnboardingContext = createContext<OnboardingContextValue | null>(null);

/** Consent document versions, from the server when it answers. */
async function resolveConsentVersions(): Promise<Record<ConsentKind, string>> {
  const fallback = Object.fromEntries(
    ALL_CONSENTS.map((kind) => [kind, FALLBACK_CONSENT_VERSION]),
  ) as Record<ConsentKind, string>;
  try {
    const response = await fetchConsentRequirements(api);
    for (const kind of ALL_CONSENTS) {
      const document = response.documents.find((entry) => entry.kind === kind);
      if (document !== undefined) {
        fallback[kind] = document.version;
      }
    }
    return fallback;
  } catch {
    // The documents are the source of truth; if they cannot be fetched we send
    // the shipped version and let the API reject a mismatch loudly.
    return fallback;
  }
}

function friendlyAuthError(code: string, message: string): string {
  switch (code) {
    case "invalid_email":
      return "That email address does not look right. Please check it and try again.";
    case "email_taken":
      return "An account with this email already exists. Try signing in instead.";
    case "password_invalid":
      return message;
    default:
      return message;
  }
}

export function OnboardingProvider({ children }: { children: ReactNode }): ReactElement {
  const [record, setRecord] = useState<OnboardingRecord | null>(loadOnboardingRecord);

  const complete = useCallback(async (input: CompleteOnboardingInput): Promise<CompleteResult> => {
    // The gate. Required consents must be granted, whatever the UI believes.
    const missing = REQUIRED_CONSENTS.filter((kind) => input.consents[kind] !== true);
    if (missing.length > 0) {
      return {
        ok: false,
        error: `Manovia needs these agreements before you can start: ${missing.join(", ")}.`,
      };
    }
    if (
      input.authMode === "account" &&
      (input.credentials === undefined ||
        input.credentials.email.trim() === "" ||
        input.credentials.password === "")
    ) {
      return { ok: false, error: "Enter an email address and a password to create an account." };
    }

    try {
      const versions = await resolveConsentVersions();
      const session =
        input.authMode === "guest"
          ? await createGuestSession(api)
          : await registerAccount(input.credentials as Credentials, api);

      // Record what was agreed, at the version the server currently publishes.
      const grants = ALL_CONSENTS.filter((kind) => input.consents[kind]).map((kind) => ({
        kind,
        version: versions[kind],
        granted: true,
      }));
      await recordConsents(api, grants);

      const next: OnboardingRecord = {
        version: 1,
        completedAt: new Date().toISOString(),
        consents: input.consents,
        authMode: input.authMode,
        user: {
          id: session.user.id,
          email: session.user.email,
          isAnonymous: session.user.is_anonymous,
        },
      };
      saveOnboardingRecord(next);
      setRecord(next);
      return { ok: true, record: next };
    } catch (error) {
      if (isApiError(error)) {
        return { ok: false, error: friendlyAuthError(error.code, error.message) };
      }
      // A failed fetch surfaces as a TypeError; anything else is a malformed
      // response. Both are honest failures, so say what is actually wrong.
      return {
        ok: false,
        error:
          error instanceof TypeError
            ? "Manovia could not be reached. Check your connection and try again."
            : "Something went wrong while setting up your session. Please try again.",
      };
    }
  }, []);

  const reset = useCallback(() => {
    clearOnboardingRecord();
    api.clearSession();
    setRecord(null);
  }, []);

  const value = useMemo<OnboardingContextValue>(
    () => ({ record, isComplete: record !== null, complete, reset }),
    [record, complete, reset],
  );

  return <OnboardingContext.Provider value={value}>{children}</OnboardingContext.Provider>;
}

export function useOnboarding(): OnboardingContextValue {
  const context = useContext(OnboardingContext);
  if (context === null) {
    throw new Error("useOnboarding must be used inside an <OnboardingProvider>");
  }
  return context;
}
