import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { act, fireEvent, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InstallPromptBanner } from "@/components/InstallPromptBanner";
import { fetchExercises } from "@/features/exercises/endpoints";
import { ApiClient, MemoryTokenStore } from "@/lib/api";
import { fetchCrisisResources } from "@/lib/endpoints";
import {
  INSTALL_DISMISSED_KEY,
  setDeferredInstallPromptForTests,
  type BeforeInstallPromptEvent,
} from "@/lib/pwa/installPrompt";
import {
  readCachedCrisisResources,
  readCachedExercises,
  warmOfflineResources,
  writeCachedCrisisResources,
} from "@/lib/pwa/offlineCache";
import { createQueryClient } from "@/lib/queryClient";
import { TEST_RESPONSE } from "@/test/crisisFixtures";
import { makeExercisesFake } from "@/test/exerciseFakes";
import { renderWithProviders } from "@/test/utils";

const HERE = dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = resolve(HERE, "../../../public");
const SW_SOURCE = readFileSync(resolve(HERE, "../../sw.ts"), "utf8");

describe("PWA web manifest and generated icons", () => {
  it("ships a valid manifest with standalone display and original PNG icons", () => {
    const manifestRaw = readFileSync(resolve(PUBLIC_DIR, "manifest.webmanifest"), "utf8");
    const manifest = JSON.parse(manifestRaw) as {
      name: string;
      short_name: string;
      display: string;
      start_url: string;
      theme_color: string;
      background_color: string;
      icons: Array<{ src: string; sizes: string; type: string; purpose: string }>;
    };

    expect(manifest.short_name).toBe("Manovia");
    expect(manifest.display).toBe("standalone");
    expect(manifest.start_url).toBe("/?source=pwa");
    expect(manifest.theme_color).toBe("#2e6a4e");
    expect(manifest.background_color).toBe("#faf6ef");
    expect(manifest.icons.length).toBeGreaterThanOrEqual(3);

    for (const icon of manifest.icons) {
      const filePath = resolve(PUBLIC_DIR, icon.src.replace(/^\//, ""));
      expect(existsSync(filePath), `missing icon ${icon.src}`).toBe(true);
      const bytes = readFileSync(filePath);
      // PNG magic bytes: 89 50 4E 47 0D 0A 1A 0A
      expect([...bytes.subarray(0, 8)]).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
      const width = bytes.readUInt32BE(16);
      const height = bytes.readUInt32BE(20);
      expect(`${width}x${height}`).toBe(icon.sizes);
    }
  });

  it("declares Workbox caching for helplines and exercises while excluding personal API routes", () => {
    expect(SW_SOURCE).toContain("precacheAndRoute");
    expect(SW_SOURCE).toContain("/api/v1/crisis/resources");
    expect(SW_SOURCE).toContain("/api/v1/exercises");
    expect(SW_SOURCE).toContain("!/^\\/api\\//".replace("!", ""));
    expect(SW_SOURCE).toContain('!url.pathname.startsWith("/api/v1/exercises/history")');
  });
});

describe("Offline cache for helplines and exercises", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("serves cached helplines and exercises when a subsequent network request fails", async () => {
    writeCachedCrisisResources(TEST_RESPONSE);
    const fake = makeExercisesFake();
    const loadedCatalog = await fetchExercises(fake.client);
    expect(readCachedCrisisResources()?.resources.length).toBeGreaterThan(0);
    expect(readCachedExercises()?.exercises.length).toBeGreaterThan(0);

    // Simulate going offline: the transport rejects, and both endpoints fall
    // back to the warmed local snapshot.
    const failingClient = new ApiClient({
      tokens: new MemoryTokenStore(),
      fetchImpl: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    fake.failCatalog = new TypeError("Failed to fetch");

    const offlineCrisis = await fetchCrisisResources(failingClient);
    const offlineExercises = await fetchExercises(fake.client);

    expect(offlineCrisis.resources).toEqual(TEST_RESPONSE.resources);
    expect(offlineExercises.exercises).toEqual(loadedCatalog.exercises);
  });

  it("warms both helplines and exercises into QueryClient when online", async () => {
    const queryClient = createQueryClient();
    const crisisSpy = vi.fn(async () => TEST_RESPONSE);
    const fake = makeExercisesFake();
    const exercisesSpy = vi.fn(() => fetchExercises(fake.client));

    await warmOfflineResources(queryClient, crisisSpy, exercisesSpy);

    expect(crisisSpy).toHaveBeenCalledTimes(1);
    expect(exercisesSpy).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(["crisis-resources", null])).toEqual(TEST_RESPONSE);
  });
});

describe("InstallPromptBanner (non-pushy install prompt)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setDeferredInstallPromptForTests(null);
  });

  it("stays hidden when no install prompt event has fired", () => {
    renderWithProviders(<InstallPromptBanner />);
    expect(screen.queryByTestId("pwa-install-banner")).not.toBeInTheDocument();
  });

  it("renders a calm inline banner when installable, and remembers dismissal permanently", async () => {
    const promptSpy = vi.fn(async () => undefined);
    const fakeEvent = {
      prompt: promptSpy,
      userChoice: Promise.resolve({ outcome: "accepted" as const, platform: "web" }),
    } as unknown as BeforeInstallPromptEvent;

    const { container } = renderWithProviders(<InstallPromptBanner />);

    act(() => {
      setDeferredInstallPromptForTests(fakeEvent);
    });

    const banner = await screen.findByTestId("pwa-install-banner");
    expect(banner).toBeInTheDocument();
    expect(await axe(container)).toHaveNoViolations();

    fireEvent.click(screen.getByTestId("pwa-install-dismiss"));
    expect(screen.queryByTestId("pwa-install-banner")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(INSTALL_DISMISSED_KEY)).toBe("true");
  });
});
