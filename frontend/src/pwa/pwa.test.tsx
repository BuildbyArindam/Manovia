/**
 * Day 25: the install offer, the offline banner and the update notice.
 *
 * The install offer must stay quiet: it lives in Settings only, the browser's
 * own banner is suppressed, and "Not now" is remembered.
 */

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resetOnline, setOnline } from "@/test/network";

import {
  INSTALL_DISMISSED_KEY,
  __setDeferredPromptForTests,
  initInstallCapture,
  installAvailability,
  isIosSafari,
  promptInstall,
  type BeforeInstallPromptEvent,
} from "./installPrompt";
import { InstallSection } from "./InstallSection";
import { OfflineBanner } from "./OfflineBanner";
import { __resetPwaStateForTests } from "./registerServiceWorker";
import { UpdateNotice } from "./UpdateNotice";

function fakePrompt(outcome: "accepted" | "dismissed"): BeforeInstallPromptEvent {
  const event = new Event("beforeinstallprompt") as BeforeInstallPromptEvent;
  Object.assign(event, {
    prompt: vi.fn(() => Promise.resolve()),
    userChoice: Promise.resolve({ outcome, platform: "web" }),
  });
  return event;
}

afterEach(() => {
  __setDeferredPromptForTests(null);
  __resetPwaStateForTests();
  resetOnline();
});

describe("the install offer", () => {
  it("captures the browser's offer and suppresses its own banner", () => {
    initInstallCapture();
    const event = new Event("beforeinstallprompt", { cancelable: true });
    Object.assign(event, {
      prompt: vi.fn(),
      userChoice: Promise.resolve({ outcome: "dismissed" }),
    });
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    expect(installAvailability()).toBe("available");
  });

  it("remembers 'Not now' for 90 days, then offers again", () => {
    __setDeferredPromptForTests(fakePrompt("dismissed"));
    const now = Date.parse("2026-10-12T09:00:00Z");
    window.localStorage.setItem(INSTALL_DISMISSED_KEY, String(now - 89 * 86_400_000));
    expect(installAvailability(now)).toBe("dismissed");
    window.localStorage.setItem(INSTALL_DISMISSED_KEY, String(now - 91 * 86_400_000));
    expect(installAvailability(now)).toBe("available");
  });

  it("shows the browser dialog only on request, and remembers a dismissal", async () => {
    const event = fakePrompt("dismissed");
    __setDeferredPromptForTests(event);
    expect(await promptInstall()).toBe("dismissed");
    expect(event.prompt).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(INSTALL_DISMISSED_KEY)).not.toBeNull();
    expect(await promptInstall()).toBe("unavailable");
  });

  it("knows iPhone Safari installs by hand, and other iOS browsers are not Safari", () => {
    expect(isIosSafari("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1")).toBe(
      true,
    );
    expect(isIosSafari("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) CriOS/120 Safari/604.1")).toBe(
      false,
    );
    expect(isIosSafari("Mozilla/5.0 (Linux; Android 14) Chrome/120")).toBe(false);
  });

  it("Settings offers one button and a 'Not now' when the browser allows it", async () => {
    __setDeferredPromptForTests(fakePrompt("accepted"));
    const { container } = render(<InstallSection />);
    expect(screen.getByRole("heading", { name: "Install and offline use" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install Manovia" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.getByTestId("install-state")).toHaveAttribute("data-availability", "dismissed");
    expect(screen.queryByRole("button", { name: "Install Manovia" })).not.toBeInTheDocument();
    expect(screen.getByTestId("offline-data-note")).toHaveTextContent(
      "Your journal, check-ins and chats are not stored for offline reading.",
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it("says whether offline use is ready", () => {
    __resetPwaStateForTests({ offlineReady: true });
    render(<InstallSection />);
    expect(screen.getByTestId("offline-ready")).toHaveTextContent("Offline use: ready.");
  });
});

describe("the offline banner", () => {
  it("appears while offline with what still works, and announces the return", async () => {
    const { container } = render(<OfflineBanner />);
    expect(screen.queryByTestId("offline-banner")).not.toBeInTheDocument();

    setOnline(false);
    const banner = screen.getByTestId("offline-banner");
    expect(banner).toHaveTextContent("You're offline.");
    expect(banner).toHaveTextContent("Breathing exercises and the helplines");
    expect(screen.getByTestId("offline-region")).toHaveAttribute("role", "status");
    expect(await axe(container)).toHaveNoViolations();

    setOnline(true);
    expect(screen.queryByTestId("offline-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("back-online")).toHaveTextContent("You're back online.");
  });
});

describe("the update notice", () => {
  it("waits for the reader: reload or later, never a forced reload", async () => {
    render(<UpdateNotice />);
    expect(screen.queryByTestId("update-notice")).not.toBeInTheDocument();
    act(() => {
      __resetPwaStateForTests({ needRefresh: true });
    });
    expect(screen.getByTestId("update-notice")).toHaveTextContent(
      "A new version of Manovia is ready.",
    );
    expect(screen.getByRole("button", { name: "Reload now" })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Later" }));
    expect(screen.queryByTestId("update-notice")).not.toBeInTheDocument();
  });
});
