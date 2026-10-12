/**
 * The install offer — deliberately quiet (Day 25).
 *
 * Chromium fires `beforeinstallprompt` when the app is installable and, left
 * alone, may show its own mini-infobar. We capture the event and suppress that
 * banner, and then do **nothing** with it until the reader goes looking: the
 * only place Manovia offers installation is a small section in Settings. No
 * modal, no banner on the home screen, no reminder after a dismissal, and never
 * on a first visit. "Not now" is remembered for 90 days.
 *
 * Someone opening a wellbeing app on a hard day does not need an advert for
 * the app they are already using.
 */

import { useSyncExternalStore } from "react";

/** The non-standard event Chromium dispatches; typed here because TS has no lib for it. */
export interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
}

export const INSTALL_DISMISSED_KEY = "manovia.install.dismissedAt.v1";
export const INSTALL_DISMISS_DAYS = 90;

export type InstallAvailability =
  /** Running as an installed app already. */
  | "installed"
  /** The browser offered installation and the reader has not declined it recently. */
  | "available"
  /** Declined within the last 90 days: stay quiet. */
  | "dismissed"
  /** iPhone/iPad Safari: installation is a manual Share → Add to Home Screen. */
  | "ios"
  /** No offer from this browser (yet); its own menu may still install. */
  | "unsupported";

interface InstallState {
  deferred: BeforeInstallPromptEvent | null;
  installedThisSession: boolean;
}

let installState: InstallState = { deferred: null, installedThisSession: false };
const listeners = new Set<() => void>();
let initialised = false;

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

/** Start listening as early as possible (main.tsx), so the event is not missed. */
export function initInstallCapture(): void {
  if (initialised || typeof window === "undefined") {
    return;
  }
  initialised = true;
  window.addEventListener("beforeinstallprompt", (event) => {
    // Suppress the browser's own banner: the offer lives in Settings only.
    event.preventDefault();
    installState = { ...installState, deferred: event as BeforeInstallPromptEvent };
    emit();
  });
  window.addEventListener("appinstalled", () => {
    installState = { deferred: null, installedThisSession: true };
    emit();
  });
}

function readDismissedAt(): number | null {
  try {
    const raw = window.localStorage.getItem(INSTALL_DISMISSED_KEY);
    if (raw === null) {
      return null;
    }
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

export function isStandalone(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  const media =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(display-mode: standalone)").matches;
  const iosStandalone = (navigator as Navigator & { standalone?: boolean }).standalone === true;
  return media || iosStandalone;
}

export function isIosSafari(userAgent: string = navigator.userAgent): boolean {
  const ios = /iPad|iPhone|iPod/.test(userAgent);
  const otherBrowser = /CriOS|FxiOS|EdgiOS/.test(userAgent);
  return ios && !otherBrowser;
}

/** What the Settings section should say right now. */
export function installAvailability(now: number = Date.now()): InstallAvailability {
  if (installState.installedThisSession || isStandalone()) {
    return "installed";
  }
  const dismissedAt = readDismissedAt();
  const recentlyDismissed =
    dismissedAt !== null && now - dismissedAt < INSTALL_DISMISS_DAYS * 24 * 60 * 60 * 1000;
  if (installState.deferred !== null) {
    return recentlyDismissed ? "dismissed" : "available";
  }
  if (isIosSafari()) {
    return recentlyDismissed ? "dismissed" : "ios";
  }
  return "unsupported";
}

/** Show the browser's install dialog. Only ever called from a button press. */
export async function promptInstall(): Promise<"accepted" | "dismissed" | "unavailable"> {
  const event = installState.deferred;
  if (event === null) {
    return "unavailable";
  }
  // The event can be used once; drop it whatever the answer.
  installState = { ...installState, deferred: null };
  emit();
  await event.prompt();
  const choice = await event.userChoice;
  if (choice.outcome === "dismissed") {
    rememberInstallDismissed();
  }
  return choice.outcome;
}

export function rememberInstallDismissed(now: number = Date.now()): void {
  try {
    window.localStorage.setItem(INSTALL_DISMISSED_KEY, String(now));
  } catch {
    // Without storage the offer simply comes back next visit — still only in Settings.
  }
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

let snapshotKey = "";
let snapshot: InstallAvailability = "unsupported";

function getSnapshot(): InstallAvailability {
  const next = installAvailability();
  const key = `${next}`;
  if (key !== snapshotKey) {
    snapshotKey = key;
    snapshot = next;
  }
  return snapshot;
}

export function useInstallAvailability(): InstallAvailability {
  return useSyncExternalStore(subscribe, getSnapshot, () => "unsupported");
}

/** Test seam. */
export function __setDeferredPromptForTests(event: BeforeInstallPromptEvent | null): void {
  installState = { deferred: event, installedThisSession: false };
  emit();
}
