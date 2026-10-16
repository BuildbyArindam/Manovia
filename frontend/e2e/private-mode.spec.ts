/**
 * Day 41 — the network proof: Private Mode completes a conversation, including a
 * crisis message, with every request blocked.
 *
 * This is the spec the whole feature is judged by, and it is deliberately blunt:
 * after the page has loaded and Private Mode has read its two same-origin static
 * assets, *all* networking is turned off twice over — Chromium's offline
 * emulation (`context.setOffline`) and a catch-all route that aborts everything
 * and records what was attempted. Then a person types.
 *
 * What is asserted, and why a unit test cannot do it:
 *
 * 1. an ordinary message is answered, and the answer carries the badge that says
 *    it was written on this device;
 * 2. a HIGH-risk message produces the pre-written crisis reply *and* the
 *    CrisisCard, with the safety gate running before any model (AGENTS.md rule 1)
 *    — in a browser, with no network to fall back on;
 * 3. nothing was attempted that could carry the message text: every recorded
 *    request is same-origin, no request body contains the message, and no chat
 *    endpoint was ever called;
 * 4. with a passphrase set and storage on, the transcript is written to IndexedDB
 *    as ciphertext — the record holds no plaintext field, and the message text
 *    appears nowhere in the database dump.
 *
 * Backend settings this spec expects (the same as `chat.spec.ts`):
 * `LLM_PROVIDER=fake`, `SAFETY_ML_ENABLED=false`. The backend is needed only to
 * walk onboarding and to prove that Private Mode does *not* use it: the moment
 * Private Mode is on, the network goes away and the conversation continues.
 */

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { apiReachable, completeGuestOnboarding } from "./day25/helpers";

/** Classified HIGH by the rules engine (see `backend/app/content/safety`). */
const HIGH_RISK = "I have a plan to kill myself tonight and I have the pills";
/** An ordinary message: answered on device, not by the crisis gate. */
const ORDINARY = "I had a bad day and I cannot switch my brain off";
const PASSPHRASE = "the monsoon reached kolkata early this year";

interface Attempted {
  url: string;
  method: string;
  postData: string | null;
}

test.beforeEach(async ({ page }) => {
  test.skip(!(await apiReachable(page)), "the API is not reachable at /api/v1/ready");
});

/** Onboarding lands on home (Day 21); this spec is about the chat surface. */
async function openChat(page: Page): Promise<void> {
  await completeGuestOnboarding(page);
  await page.goto("/chat");
  await expect(page.getByTestId("chat-view")).toBeVisible();
}

/**
 * Switch a React controlled checkbox on.
 *
 * `.check()` is the wrong tool here: React re-renders the input from state, so
 * between the click and the async settings write the DOM property goes back to
 * false and Playwright reports "did not change its state". Click, then wait for
 * the state the app actually reached.
 */
async function turnOn(page: Page, testId: string): Promise<void> {
  const control = page.getByTestId(testId);
  await expect(control).toBeEnabled();
  if (!(await control.isChecked())) {
    await control.click();
  }
  await expect(control).toBeChecked();
}

/** Turn Private Mode on, wait for the boot, and report the capability verdict. */
async function enablePrivateMode(page: Page): Promise<void> {
  await turnOn(page, "private-mode-toggle");
  // The boot is finished when the verdict is on screen: the tier sentence is
  // rendered only once the bundle, the manifest and the device probe are in.
  await expect(page.getByTestId("private-tier")).toBeVisible();
  await expect(page.getByTestId("private-smaller-model")).toBeVisible();
  await expect(page.getByTestId("private-boot-error")).toHaveCount(0);
}

/** Block everything, twice, and record every attempt from here on. */
async function blockAllNetwork(page: Page, context: BrowserContext): Promise<Attempted[]> {
  const attempted: Attempted[] = [];
  page.on("request", (request) => {
    attempted.push({
      url: request.url(),
      method: request.method(),
      postData: request.postData(),
    });
  });
  await page.route("**/*", (route) => route.abort());
  await context.setOffline(true);
  return attempted;
}

async function send(page: Page, text: string): Promise<void> {
  const box = page.getByRole("textbox", { name: /what is on your mind/i });
  await box.fill(text);
  await box.press("Enter");
}

/** The latest assistant bubble, and the badge underneath it. */
async function latestReply(page: Page): Promise<string> {
  const bubbles = page.getByTestId("assistant-bubble");
  await expect(bubbles.last()).toBeVisible();
  return (await bubbles.last().innerText()).trim();
}

test("answers an ordinary and a crisis message with every request blocked", async ({
  page,
  context,
}) => {
  await openChat(page);
  await enablePrivateMode(page);

  // Voice is honestly unavailable: there is no on-device speech pipeline.
  await expect(page.getByTestId("private-voice-notice")).toBeVisible();

  const attempted = await blockAllNetwork(page, context);

  // ---- an ordinary message ------------------------------------------------
  await send(page, ORDINARY);
  await expect(page.getByTestId("private-reply-badge").first()).toBeVisible();
  const ordinaryReply = await latestReply(page);
  expect(ordinaryReply.length).toBeGreaterThan(20);
  // No crisis card for an ordinary message: the gate must not fire on distress.
  await expect(page.getByTestId("chat-crisis-card")).toHaveCount(0);
  const ordinaryBadge = page.getByTestId("private-reply-badge").first();
  await expect(ordinaryBadge).toContainText("Answered on this device");
  await expect(ordinaryBadge).toHaveAttribute("data-stored", "false");

  // ---- a HIGH-risk message ------------------------------------------------
  await send(page, HIGH_RISK);
  await expect(page.getByTestId("chat-crisis-card")).toBeVisible();
  // Softened mode: the crisis reply drops the decorative surface.
  await expect(page.getByTestId("chat-view")).toHaveAttribute("data-softened", "true");
  const crisisReply = await latestReply(page);
  expect(crisisReply.length).toBeGreaterThan(40);
  // The crisis card carries real helplines, read from the bundle on this device.
  const card = await page.getByTestId("chat-crisis-card").innerText();
  expect(card.length).toBeGreaterThan(40);
  await expect(page.getByTestId("private-reply-badge").last()).toContainText("No model writes it");

  // ---- what the blocked network saw --------------------------------------
  // Nothing left the origin, and nothing carried what the person typed.
  const origin = new URL(page.url()).origin;
  for (const request of attempted) {
    expect(request.url.startsWith(origin), `off-origin request: ${request.url}`).toBe(true);
    expect(request.url).not.toContain(encodeURIComponent(ORDINARY));
    expect(request.url).not.toContain(encodeURIComponent(HIGH_RISK));
    expect(request.postData ?? "").not.toContain(ORDINARY);
    expect(request.postData ?? "").not.toContain(HIGH_RISK);
    expect(request.url).not.toMatch(/\/api\/v1\/chat/);
  }
  // Aborted requests are still attempts: the point is that the private path made
  // none of them. Whatever is listed here is the app's own background traffic
  // (session refresh, analytics-free as it is), never the message.
  const chatAttempts = attempted.filter((request) => /\/api\/v1\/chat/.test(request.url));
  expect(chatAttempts).toEqual([]);
});

test("stores the transcript encrypted, and only on this device", async ({ page, context }) => {
  await openChat(page);
  await enablePrivateMode(page);

  // A passphrase: the key is derived from it and the passphrase is never stored.
  await page.getByTestId("private-passphrase").fill(PASSPHRASE);
  await page.getByTestId("private-create-key").click();
  await expect(page.getByTestId("private-lock")).toBeVisible();
  await turnOn(page, "private-store-transcript");

  const attempted = await blockAllNetwork(page, context);

  await send(page, ORDINARY);
  await expect(page.getByTestId("private-reply-badge").first()).toBeVisible();
  await expect(page.getByTestId("private-reply-badge").first()).toHaveAttribute(
    "data-stored",
    "true",
  );
  await expect(page.getByTestId("private-reply-badge").first()).toContainText(
    "Stored encrypted on this device",
  );

  // Read the database the way an attacker with the device would: every record,
  // every field. The message text must not be in any of them.
  const dump = await page.evaluate(async () => {
    const database = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("manovia-private");
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    const stores = Array.from(database.objectStoreNames);
    const rows: Record<string, unknown[]> = {};
    for (const store of stores) {
      rows[store] = await new Promise<unknown[]>((resolve, reject) => {
        const transaction = database.transaction(store, "readonly");
        const request = transaction.objectStore(store).getAll();
        request.onsuccess = () => resolve(request.result as unknown[]);
        request.onerror = () => reject(request.error);
      });
    }
    database.close();
    return { stores, rows };
  });

  expect(dump.stores).toEqual(expect.arrayContaining(["state", "blobs", "messages"]));
  const messages = dump.rows["messages"] as Record<string, unknown>[];
  expect(messages.length).toBeGreaterThan(0);
  for (const record of messages) {
    // The cleared-field contract from store/idb.ts: id, conversationId, seq,
    // format, ciphertext, iv, keyId — and nothing that could hold text.
    expect(Object.keys(record).sort()).toEqual(
      ["ciphertext", "conversationId", "format", "id", "iv", "keyId", "seq"].sort(),
    );
    expect(record["format"]).toBe("manovia-private-record/1");
  }

  // No plaintext anywhere in the database, in any store.
  const serialised = JSON.stringify(dump.rows);
  expect(serialised).not.toContain(ORDINARY);
  expect(serialised).not.toContain("cannot switch my brain off");

  // And the passphrase never reached storage, or the network.
  expect(serialised).not.toContain(PASSPHRASE);
  for (const request of attempted) {
    expect(request.postData ?? "").not.toContain(ORDINARY);
    expect(request.postData ?? "").not.toContain(PASSPHRASE);
    // Background traffic (a session refresh) may be attempted and will fail; what
    // must never happen is a chat call, or a body carrying the message.
    expect(request.url).not.toMatch(/\/api\/v1\/chat/);
  }

  // Locking is instant and local: the transcript is unreadable without the key.
  await page.getByTestId("private-lock").click();
  await expect(page.getByTestId("private-unlock")).toBeVisible();
  await page.getByTestId("private-passphrase").fill("not the passphrase at all, honestly");
  await page.getByTestId("private-unlock").click();
  await expect(page.getByTestId("private-passphrase-note")).toContainText("wrong-passphrase");
});
