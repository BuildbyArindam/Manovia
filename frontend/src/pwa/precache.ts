/**
 * Which build files the service worker precaches (Day 25).
 *
 * Kept out of `vite.config.ts` so the rule is unit-testable without loading
 * the build toolchain, and imported by the config for the real build.
 *
 * Insights is the one surface that is useless offline (it is a view of
 * server-side data) and it carries the chart library, the single largest chunk
 * in the build — so both are left out of the precache and fetched the first
 * time somebody opens Insights online, exactly as before. Day 41 adds a second
 * such chunk: the on-device Private Mode stack, which is bigger than the whole
 * /chat budget and is only useful to somebody who turns Private Mode on.
 * Everything else, every other page included, is precached so the app opens and
 * navigates with no network.
 */

import type { ManifestTransform } from "workbox-build";

export const PRECACHE_EXCLUDED_CHUNKS: readonly RegExp[] = [
  /^assets\/charts-/,
  /^assets\/InsightsPage-/,
  // Day 41: the whole on-device stack (rules engine, capability probe, download
  // manager, crypto, panel). It is larger than the /chat route budget and only
  // useful to someone who turns Private Mode on, so it is fetched the first time
  // they do — the same treatment as the chart library. The service worker keeps
  // it after that (`swRoutes.isPrivateAssetRequest` covers the compiled bundle),
  // and the browser's HTTP cache covers the chunk itself.
  /^assets\/PrivateModeProvider-/,
];

export const filterPrecacheManifest: ManifestTransform = (entries) => ({
  manifest: entries.filter(
    (entry) => !PRECACHE_EXCLUDED_CHUNKS.some((pattern) => pattern.test(entry.url)),
  ),
  warnings: [],
});
