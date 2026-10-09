/**
 * Content drift guard.
 *
 * The frontend carries a fallback copy of the AI disclosure and of the consent
 * versions, for the case where the API cannot be reached. If the backend ships a
 * new version of either, the app must not quietly show the old one — so this
 * test reads the backend's JSON and fails when they disagree.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { FALLBACK_AI_DISCLOSURE, FALLBACK_CONSENT_VERSION } from "@/onboarding/copy";
import { ALL_CONSENTS } from "@/lib/endpoints";

interface ConsentDocumentsFile {
  documents: Record<string, { version: string; text?: string }>;
}

// src/onboarding → src → frontend → repository root.
const HERE = dirname(fileURLToPath(import.meta.url));
const DOCUMENTS_PATH = join(
  HERE,
  "..",
  "..",
  "..",
  "backend",
  "app",
  "content",
  "consent_documents.json",
);
const DOCUMENTS: ConsentDocumentsFile = JSON.parse(
  readFileSync(DOCUMENTS_PATH, "utf8"),
) as ConsentDocumentsFile;

describe("onboarding content", () => {
  it("agrees with the backend about the current consent versions", () => {
    for (const kind of ALL_CONSENTS) {
      const document = DOCUMENTS.documents[kind];
      if (document === undefined) {
        throw new Error(`backend/app/content/consent_documents.json is missing ${kind}`);
      }
      expect(document.version).toBe(FALLBACK_CONSENT_VERSION);
    }
  });

  it("agrees with the backend about the AI disclosure text", () => {
    expect(DOCUMENTS.documents.ai_disclosure?.text).toBe(FALLBACK_AI_DISCLOSURE);
  });
});
