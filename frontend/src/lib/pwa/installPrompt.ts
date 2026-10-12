/**
 * Non-pushy PWA install prompt state (Day 25).
 *
 * Principles:
 * - Never auto-open a modal or interrupt onboarding.
 * - Capture `beforeinstallprompt`, suppress the browser's default mini-infobar,
 *   and surface a quiet inline card only when the person is using the app and
 *   has not dismissed it.
 * - A single tap on "Not now" records `manovia.pwa.installDismissed.v1` in
 *   `localStorage` and hides the banner permanently; the option remains
 *   available inside Settings for anyone who wants it later.
 */

import { useCallback, useEffect, useState } from "react";

export const INSTALL_DISMISSED_KEY = "manovia.pwa.installDismissed.v1";
export const INSTALL_STATE_EVENT = "manovia:pwa-install-state";

export interface BeforeInstallPromptEvent extends Event {
  readonly platforms?: string[];
  readonly userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
  prompt(): Promise<void>;
}

let deferredPrompt: BeforeInstallPromptEvent | null = null;
let installed = false;
let listenerAttached = false;

function notifyInstallListeners(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(INSTALL_STATE_EVENT));
  }
}

export function attachInstallPromptListeners(): void {
  if (typeof window === "undefined" || listenerAttached) {
    return;
  }
  listenerAttached = true;

  window.addEventListener("beforeinstallprompt", (event: Event) => {
    event.preventDefault();
    deferredPrompt = event as BeforeInstallPromptEvent;
    notifyInstallListeners();
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    installed = true;
    notifyInstallListeners();
  });
}

/** Test helper to reset or inject a synthetic `beforeinstallprompt` event. */
export function setDeferredInstallPromptForTests(
  event: BeforeInstallPromptEvent | null,
): void {
  deferredPrompt = event;
  installed = false;
  notifyInstallListeners();
}

export function isInstallDismissed(storage: Storage = window.localStorage): boolean {
  try {
    return storage.getItem(INSTALL_DISMISSED_KEY) === "true";
  } catch {
    return false;
  }
}

export function dismissInstallPrompt(storage: Storage = window.localStorage): void {
  try {
    storage.setItem(INSTALL_DISMISSED_KEY, "true");
  } catch {
    // Ignore storage errors in private browsing.
  }
  notifyInstallListeners();
}

function isStandaloneDisplay(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(display-mode: standalone)").matches;
}

export interface UseInstallPromptResult {
  /** True when the browser has offered installation and the app is not already installed. */
  canInstall: boolean;
  /** True when `canInstall` is true and the user has not dismissed the banner. */
  shouldShowBanner: boolean;
  /** Trigger the native install prompt. */
  promptInstall: () => Promise<"accepted" | "dismissed" | "unavailable">;
  /** Permanently dismiss the inline banner (Settings still keeps the button). */
  dismiss: () => void;
}

export function useInstallPrompt(): UseInstallPromptResult {
  attachInstallPromptListeners();

  const [, setTick] = useState(0);

  useEffect(() => {
    const refresh = (): void => {
      setTick((value) => value + 1);
    };
    window.addEventListener(INSTALL_STATE_EVENT, refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener(INSTALL_STATE_EVENT, refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);

  const standalone = isStandaloneDisplay() || installed;
  const canInstall = !standalone && deferredPrompt !== null;
  const shouldShowBanner = canInstall && !isInstallDismissed();

  const promptInstall = useCallback(async (): Promise<
    "accepted" | "dismissed" | "unavailable"
  > => {
    const current = deferredPrompt;
    if (current === null) {
      return "unavailable";
    }
    deferredPrompt = null;
    notifyInstallListeners();
    try {
      await current.prompt();
      const choice = await current.userChoice;
      if (choice.outcome === "dismissed") {
        dismissInstallPrompt();
      } else {
        installed = true;
      }
      notifyInstallListeners();
      return choice.outcome;
    } catch {
      return "unavailable";
    }
  }, []);

  const dismiss = useCallback((): void => {
    dismissInstallPrompt();
  }, []);

  return {
    canInstall,
    shouldShowBanner,
    promptInstall,
    dismiss,
  };
}
