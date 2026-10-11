/**
 * Fakes for the privacy screen.
 *
 * Every function in `features/privacy/endpoints.ts` takes a client, so the
 * component can be driven with a real `ApiClient` over a fake `fetch` — which
 * means these tests exercise the real request shapes, the real headers and the
 * real error envelope rather than a hand-written stub layer.
 */

import { ApiClient, MemoryTokenStore } from "@/lib/api";

import type { ConsentHistory, DeletionReceipt, PrivacySettings } from "@/features/privacy/types";

export function privacySettings(overrides: Partial<PrivacySettings> = {}): PrivacySettings {
  return {
    store_chat: true,
    store_sentiment: true,
    retention_days: null,
    retention_choices: [30, 90, 180, 365],
    retention_min_days: 7,
    retention_max_days: 3650,
    been_saved: false,
    updated_at: null,
    ...overrides,
  };
}

export function consentHistory(overrides: Partial<ConsentHistory> = {}): ConsentHistory {
  return {
    items: [
      {
        kind: "terms",
        version: "2026-10-01",
        granted: true,
        created_at: "2026-10-11T09:00:00Z",
      },
      {
        kind: "privacy",
        version: "2026-10-01",
        granted: true,
        created_at: "2026-10-11T09:00:01Z",
      },
      {
        kind: "ai_disclosure",
        version: "2026-10-01",
        granted: true,
        created_at: "2026-10-11T09:00:02Z",
      },
    ],
    current_versions: {
      terms: "2026-10-01",
      privacy: "2026-10-01",
      ai_disclosure: "2026-10-01",
      store_chat: "2026-10-01",
    },
    ...overrides,
  };
}

export function deletionReceipt(overrides: Partial<DeletionReceipt> = {}): DeletionReceipt {
  return {
    receipt_id: "7f3a91c4-0000-4000-8000-000000000000",
    account_ref: "c0ffee1234567890abcdef1234567890",
    requested_via: "api",
    rows_deleted: { consents: 4, journal_entries: 2, mood_entries: 5, users: 1 },
    total_rows: 12,
    created_at: "2026-10-11T12:00:00Z",
    irreversible: true,
    ...overrides,
  };
}

export interface PrivacyFake {
  client: ApiClient;
  /** The settings the fake serves, mutated by PATCH. */
  settings: PrivacySettings;
  /** Every PATCH body the component sent, in order. */
  patches: Array<Record<string, unknown>>;
  /** How many times the export was fetched. */
  exportDownloads: number;
  /** When set, the next PATCH fails with this error envelope. */
  failSave: { status: number; code: string; message: string } | null;
  /** When set, the next export fails with this error envelope. */
  failExport: { status: number; code: string; message: string } | null;
  /** When set, DELETE fails with this error envelope. */
  failDelete: { status: number; code: string; message: string } | null;
  /** The last DELETE body the component sent. */
  lastDeleteBody: Record<string, unknown> | null;
  /** Consents the fake serves. */
  consents: ConsentHistory;
}

/** A client whose fetch answers exactly the privacy endpoints. */
export function makePrivacyFake(
  options: {
    settings?: PrivacySettings;
    consents?: ConsentHistory;
    /** Omit the stored session so token refresh is never attempted. */
    authenticated?: boolean;
  } = {},
): PrivacyFake {
  const store = new MemoryTokenStore();
  if (options.authenticated !== false) {
    store.write({
      accessToken: "access-token",
      refreshToken: "refresh-token",
      tokenType: "bearer",
      expiresAt: Date.now() + 3_600_000,
    });
  }

  const fake: PrivacyFake = {
    client: new ApiClient({ tokens: store, baseUrl: "/api/v1" }),
    settings: options.settings ?? privacySettings(),
    patches: [],
    exportDownloads: 0,
    failSave: null,
    failExport: null,
    failDelete: null,
    lastDeleteBody: null,
    consents: options.consents ?? consentHistory(),
  };

  const json = (body: unknown, status = 200): Response =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });

  const failure = (spec: { status: number; code: string; message: string }): Response =>
    new Response(
      JSON.stringify({ error: { code: spec.code, message: spec.message, request_id: "req-fake" } }),
      {
        status: spec.status,
        headers: { "Content-Type": "application/json", "X-Request-ID": "req-fake" },
      },
    );

  fake.client = new ApiClient({
    tokens: store,
    baseUrl: "/api/v1",
    fetchImpl: async (input, init) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url.endsWith("/privacy/settings") && method === "GET") {
        return json(fake.settings);
      }
      if (url.endsWith("/privacy/settings") && method === "PATCH") {
        if (fake.failSave !== null) {
          return failure(fake.failSave);
        }
        const patch = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
        fake.patches.push(patch);
        fake.settings = { ...fake.settings, ...patch, been_saved: true } as PrivacySettings;
        return json(fake.settings);
      }
      if (url.includes("/privacy/consents")) {
        return json(fake.consents);
      }
      if (url.includes("/privacy/export")) {
        fake.exportDownloads += 1;
        if (fake.failExport !== null) {
          return failure(fake.failExport);
        }
        return new Response(new Blob([new Uint8Array([80, 75, 3, 4])]), {
          status: 200,
          headers: {
            "Content-Type": "application/zip",
            "Content-Disposition": 'attachment; filename="manovia-export-2026-10-11.zip"',
          },
        });
      }
      if (url.endsWith("/privacy/account") && method === "DELETE") {
        fake.lastDeleteBody = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
        if (fake.failDelete !== null) {
          return failure(fake.failDelete);
        }
        return json(deletionReceipt());
      }
      return json(
        { error: { code: "not_found", message: "Not found", request_id: "req-fake" } },
        404,
      );
    },
  });

  return fake;
}
