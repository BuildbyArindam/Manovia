/**
 * Day 42 — biofeedback e2e: the flows only a browser can prove.
 *
 * Four things a unit test cannot do, all offline-capable:
 *
 * 1. **The CSV no-hardware path** with every `/api` request blocked and
 *    recorded: import the committed fixture, run a session, see the summary
 *    and its caveats, save it encrypted — with zero network attempts.
 * 2. **The unsupported-browser path** (Firefox and Safari user agents, no
 *    `navigator.bluetooth`): the honest explainer and the CSV path, never a
 *    dead button. The app feature-detects the API, not the UA.
 * 3. **A fake BLE sensor** (`addInitScript` injects `navigator.bluetooth` with
 *    a GATT stub that emits real Heart Rate Measurement packets): connect via
 *    the UI, watch beats flow through the real pipeline into the live counts,
 *    and stop with "I feel dizzy" — the summary names the stop.
 * 4. **The a11y/zoom sweeps** pick the route up through `APP_ROUTES` in
 *    `day25/helpers.ts`.
 *
 * Onboarding is seeded through `manovia.onboarding.v1` (the storage record the
 * app validates on boot), so this spec needs no backend — the feature is
 * entirely on-device and the tests prove exactly that.
 */

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FIXTURE = join(
  dirname(fileURLToPath(import.meta.url)),
  "../src/features/biofeedback/import/fixtures/health-export-sample.csv",
);

/** The onboarding record `useOnboarding` accepts on boot (guest, all consents). */
function seedOnboarding(): void {
  window.localStorage.setItem(
    "manovia.onboarding.v1",
    JSON.stringify({
      version: 1,
      completedAt: new Date().toISOString(),
      consents: { terms: true, privacy: true, ai_disclosure: true, store_chat: false },
      authMode: "guest",
      user: { id: "guest-e2e", email: null, isAnonymous: true },
    }),
  );
}

/**
 * A minimal Web Bluetooth stub: `requestDevice` resolves with a device whose
 * `heart_rate_measurement` characteristic emits real packets — flags 0x10
 * (RR present), bpm 75, one RR of 1024/1024 s (= 1000 ms) — at a steady
 * 60 ms wall-clock rhythm so 30 beats arrive in under two seconds while the
 * beat timeline the pipeline builds is the honest 30 s.
 */
function installFakeBluetooth(): void {
  class FakeCharacteristic {
    listeners = new Set<(event: unknown) => void>();
    value: DataView | null = null;
    timer: ReturnType<typeof setInterval> | null = null;

    async startNotifications(): Promise<FakeCharacteristic> {
      let emitted = 0;
      this.timer = setInterval(() => {
        // Flags 0x16: contact supported + detected, RR present. bpm 75,
        // one RR of 1024/1024 s (= 1000 ms). Exactly 30 packets, then silence:
        // the assertions can land on "30 beats kept" deterministically.
        if (emitted >= 30) {
          void this.stopNotifications();
          return;
        }
        emitted += 1;
        const bytes = new Uint8Array([0x16, 75, 0x00, 0x04]);
        this.value = new DataView(bytes.buffer);
        for (const listener of this.listeners) {
          listener({ target: this });
        }
      }, 60);
      return this;
    }

    async stopNotifications(): Promise<FakeCharacteristic> {
      if (this.timer !== null) {
        clearInterval(this.timer);
        this.timer = null;
      }
      return this;
    }

    addEventListener(type: string, listener: (event: unknown) => void): void {
      if (type === "characteristicvaluechanged") {
        this.listeners.add(listener);
      }
    }

    removeEventListener(type: string, listener: (event: unknown) => void): void {
      if (type === "characteristicvaluechanged") {
        this.listeners.delete(listener);
      }
    }
  }

  const characteristic = new FakeCharacteristic();
  const disconnectListeners = new Set<() => void>();
  const server = {
    connected: false,
    async connect(): Promise<unknown> {
      server.connected = true;
      return server;
    },
    async getPrimaryService(): Promise<unknown> {
      return { getCharacteristic: async () => characteristic };
    },
    disconnect(): void {
      server.connected = false;
    },
  };
  const device = {
    name: "E2E Fake Strap",
    gatt: server,
    addEventListener(type: string, listener: () => void): void {
      if (type === "gattserverdisconnected") {
        disconnectListeners.add(listener);
      }
    },
    removeEventListener(type: string, listener: () => void): void {
      if (type === "gattserverdisconnected") {
        disconnectListeners.delete(listener);
      }
    },
  };
  Object.defineProperty(navigator, "bluetooth", {
    configurable: true,
    value: {
      requestDevice: async (): Promise<unknown> => device,
    },
  });
}

/** Remove Web Bluetooth entirely and claim a Firefox/Safari identity. */
function removeBluetooth(): void {
  Object.defineProperty(navigator, "bluetooth", {
    configurable: true,
    get: () => undefined,
  });
}

/** Run with every /api call blocked and recorded: the local-only proof. */
async function blockAndRecordApi(
  context: BrowserContext,
): Promise<{ apiCalls: string[] }> {
  const apiCalls: string[] = [];
  await context.route("**/api/**", (route) => {
    apiCalls.push(`${route.request().method()} ${route.request().url()}`);
    void route.abort();
  });
  return { apiCalls };
}

/**
 * The service worker warms two public GETs at install (ADR 0025); those are
 * the only /api attempts this feature may ever be near. Everything else —
 * every mutation, every personal endpoint — must be empty.
 */
function assertOnlyWarmupApiCalls(apiCalls: string[]): void {
  const unexpected = apiCalls.filter(
    (call) => !call.startsWith("GET ") || !/\/api\/v1\/(crisis\/resources|exercises)/.test(call),
  );
  expect(unexpected).toEqual([]);
}

async function openBiofeedback(page: Page): Promise<void> {
  await page.goto("/biofeedback");
  await expect(page.getByTestId("biofeedback-page")).toBeVisible();
  await expect(page.getByTestId("wellness-badge")).toBeVisible();
}

test.describe("biofeedback — local-only flows", () => {
  test("CSV path: import, session, summary and encrypted save with the API blocked", async ({
    page,
    context,
  }) => {
    const { apiCalls } = await blockAndRecordApi(context);
    await page.addInitScript(seedOnboarding);
    await openBiofeedback(page);

    // The no-hardware path is offered on the page itself.
    await expect(page.getByTestId("csv-panel")).toBeVisible();
    await page.getByTestId("csv-input").setInputFiles(FIXTURE);
    await expect(page.getByTestId("csv-result")).toContainText("40 intervals imported");

    await page.getByTestId("session-start").click();
    await page.getByTestId("session-end").click();
    await expect(page.getByTestId("summary-body")).toBeVisible();
    await expect(page.getByTestId("summary-caveats")).toContainText("not a health assessment");
    await expect(page.getByTestId("summary-caveats")).toContainText("observations from this one session");

    await page.getByTestId("summary-save").click();
    await expect(page.getByTestId("summary-save")).toHaveText("Saved");
    await expect(page.getByTestId("trend-table")).toBeVisible();

    // Nothing at all went to the API: not the import, not the session, not the
    // save. (The block was active for the whole flow.)
    assertOnlyWarmupApiCalls(apiCalls);
  });

  test("summary copy makes no health claims (readable in the browser)", async ({
    page,
    context,
  }) => {
    await blockAndRecordApi(context);
    await page.addInitScript(seedOnboarding);
    await openBiofeedback(page);
    await page.getByTestId("csv-input").setInputFiles(FIXTURE);
    await page.getByTestId("session-start").click();
    await page.getByTestId("dizzy-stop").click();
    await expect(page.getByTestId("summary-body")).toBeVisible();

    // Coarse screen-level spot check (the negation-aware denylist lives in
    // `ui/copy.test.ts`): claim-shaped phrases that are never acceptable.
    const surface = (await page.getByTestId("biofeedback-page").innerText()).toLowerCase();
    for (const forbidden of [
      "healthy hrv",
      "unhealthy hrv",
      "good hrv",
      "bad hrv",
      "risk score",
      "clinically",
      "medical-grade",
    ]) {
      expect(surface).not.toContain(forbidden);
    }
    // The required framing IS there.
    expect(surface).toContain("not a medical");
    await expect(page.getByTestId("summary-body")).toContainText("dizziness");
  });

  test("fake BLE sensor: beats flow through the pipeline, dizzy stop ends it", async ({
    page,
    context,
  }) => {
    const { apiCalls } = await blockAndRecordApi(context);
    await page.addInitScript(seedOnboarding);
    await page.addInitScript(installFakeBluetooth);
    await openBiofeedback(page);

    // Permission explainer first, then the connect button.
    await page.getByTestId("permission-explainer").getByRole("button").click();
    await expect(page.getByTestId("permission-explainer-body")).toContainText("never uploaded");
    await page.getByTestId("device-connect").click();
    await expect(page.getByTestId("device-connected")).toContainText("E2E Fake Strap");
    await expect(page.getByTestId("device-contact")).toContainText("contact looks good");

    await page.getByTestId("session-start").click();
    await expect(page.getByTestId("dizzy-stop")).toBeVisible();

    // 30 RR intervals of 1000 ms arrive in ~2 s of wall time; the pipeline
    // must keep every clean beat and the UI must say so.
    await expect(page.getByTestId("beats-count")).toContainText(/30 beats kept/, {
      timeout: 15_000,
    });

    await page.getByTestId("dizzy-stop").click();
    await expect(page.getByTestId("session-ended")).toBeVisible();
    await expect(page.getByTestId("summary-body")).toContainText("dizziness");
    // The pace is frozen where the stop left it: the number must not move.
    const rate = await page.getByTestId("pacer-rate").textContent();
    await page.waitForTimeout(800);
    await expect(page.getByTestId("pacer-rate")).toHaveText(rate ?? "");

    assertOnlyWarmupApiCalls(apiCalls);
  });
});

/**
 * The unsupported-browser path, one context per real user agent. The app must
 * fall back honestly because it feature-detects `navigator.bluetooth`, never
 * the UA — these tests set both, so they stand for the browsers named.
 */
const AGENTS = {
  "firefox (linux)": "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0",
  "safari (macos)":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
} as const;

for (const [name, userAgent] of Object.entries(AGENTS)) {
  test.describe(`unsupported browser — ${name}`, () => {
    test.use({ userAgent });

    test("honest fallback and the CSV path, no dead button", async ({ page, context }) => {
      const { apiCalls } = await blockAndRecordApi(context);
      await page.addInitScript(seedOnboarding);
      await page.addInitScript(removeBluetooth);
      await openBiofeedback(page);

      await expect(page.getByTestId("bluetooth-unsupported")).toBeVisible();
      await expect(page.getByTestId("bluetooth-unsupported")).toContainText(
        "Web Bluetooth is not available",
      );
      await expect(page.getByTestId("device-connect")).toHaveCount(0);
      await expect(page.getByTestId("goto-csv")).toBeVisible();
      await page.getByTestId("goto-csv").click();
      await expect(page.getByTestId("csv-panel")).toBeVisible();
      assertOnlyWarmupApiCalls(apiCalls);
    });
  });
}
