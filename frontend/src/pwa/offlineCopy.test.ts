/**
 * The offline copy has to describe what the code does (Day 25 verification).
 *
 * Queued check-ins are sent by the page (`features/mood/OutboxSync.tsx`), not
 * by the service worker: there is deliberately no background sync (ADR 0025).
 * So a check-in made offline is sent once the device is online **and Manovia
 * is open** — not "when you're back online", which is what five strings said
 * until verification caught it. Somebody who closes the tab on the train and
 * reconnects at home would have been told "you don't need to do anything"
 * while their check-in sat on the phone.
 *
 * This test pins both halves together: the copy must name the condition, and
 * the worker must still have no sync handler. If background sync is ever
 * added, the second assertion fails first, and the copy gets revisited in the
 * same diff instead of drifting.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import bn from "@/lib/i18n/locales/bn.json";
import en from "@/lib/i18n/locales/en.json";
import hi from "@/lib/i18n/locales/hi.json";

type Tree = { [key: string]: string | Tree };

function lookup(tree: Tree, path: string): string {
  let node: string | Tree | undefined = tree;
  for (const part of path.split(".")) {
    if (typeof node !== "object") {
      break;
    }
    node = node[part];
  }
  if (typeof node !== "string") {
    throw new Error(`missing string ${path}`);
  }
  return node;
}

function allStrings(tree: Tree, prefix = ""): Array<[string, string]> {
  return Object.entries(tree).flatMap(([key, value]) => {
    const path = prefix === "" ? key : `${prefix}.${key}`;
    return typeof value === "string"
      ? [[path, value] as [string, string]]
      : allStrings(value, path);
  });
}

/** Every string that promises when a queued check-in will be sent. */
const SEND_PROMISES = [
  "mood.offlineHint",
  "mood.queuedBody",
  "mood.pendingOffline",
  "pwa.offlineBody",
  "home.quickQueued",
] as const;

const HERE = dirname(fileURLToPath(import.meta.url));

describe("offline copy matches the sync behaviour", () => {
  it.each(SEND_PROMISES)("English %s names both conditions: online and Manovia open", (key) => {
    const text = lookup(en as Tree, key);
    expect(text).toMatch(/online/);
    expect(text).toMatch(/Manovia (is )?open/);
  });

  it.each(SEND_PROMISES)("Hindi %s names Manovia being open", (key) => {
    const text = lookup(hi as Tree, key);
    expect(text).toContain("Manovia");
    expect(text).toContain("खुला");
  });

  it.each(SEND_PROMISES)("Bengali %s names Manovia being open", (key) => {
    const text = lookup(bn as Tree, key);
    expect(text).toContain("Manovia");
    expect(text).toContain("খোলা");
  });

  it("no English string promises a send on reconnection alone", () => {
    const overPromises = allStrings(en as Tree).filter(([, text]) =>
      /sent (by itself )?when you're back online/i.test(text),
    );
    expect(overPromises).toEqual([]);
  });

  it("the service worker still has no background sync (or the copy above must change)", () => {
    const worker = readFileSync(join(HERE, "..", "sw", "sw.ts"), "utf8");
    expect(worker).not.toMatch(/addEventListener\(\s*["'](sync|periodicsync)["']/);
    expect(worker).not.toMatch(/BackgroundSync|workbox-background-sync/);
  });
});
