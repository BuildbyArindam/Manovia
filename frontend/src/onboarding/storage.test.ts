import { describe, expect, it } from "vitest";

import {
  NO_CONSENTS,
  ONBOARDING_STORAGE_KEY,
  loadOnboardingRecord,
  saveOnboardingRecord,
  type OnboardingRecord,
} from "@/onboarding/storage";

function record(overrides: Partial<OnboardingRecord> = {}): OnboardingRecord {
  return {
    version: 1,
    completedAt: "2026-10-09T10:00:00.000Z",
    consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: false },
    authMode: "guest",
    user: { id: "user-1", email: null, isAnonymous: true },
    ...overrides,
  };
}

describe("onboarding storage", () => {
  it("round-trips a completed record", () => {
    const saved = record();

    saveOnboardingRecord(saved);

    expect(loadOnboardingRecord()).toEqual(saved);
  });

  it("returns null when nothing is stored", () => {
    expect(loadOnboardingRecord()).toBeNull();
  });

  it("starts with every consent unticked", () => {
    expect(NO_CONSENTS).toEqual({
      terms: false,
      privacy: false,
      ai_disclosure: false,
      store_chat: false,
    });
  });

  it("rejects a record from a different shape or version", () => {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, JSON.stringify({ version: 2 }));
    expect(loadOnboardingRecord()).toBeNull();

    window.localStorage.setItem(
      ONBOARDING_STORAGE_KEY,
      JSON.stringify(
        record({ consents: { terms: "yes" } as unknown as OnboardingRecord["consents"] }),
      ),
    );
    expect(loadOnboardingRecord()).toBeNull();

    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, "{{{not json");
    expect(loadOnboardingRecord()).toBeNull();
  });

  it("clears the record", () => {
    saveOnboardingRecord(record());

    window.localStorage.removeItem(ONBOARDING_STORAGE_KEY);

    expect(loadOnboardingRecord()).toBeNull();
  });
});
