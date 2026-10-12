/**
 * Which build files the service worker precaches (Day 25).
 *
 * Kept out of `vite.config.ts` so the rule is unit-testable without loading
 * the build toolchain, and imported by the config for the real build.
 *
 * Insights is the one surface that is useless offline (it is a view of
 * server-side data) and it carries the chart library, the single largest chunk
 * in the build — so both are left out of the precache and fetched the first
 * time somebody opens Insights online, exactly as before. Everything else,
 * every other page included, is precached so the app opens and navigates with
 * no network.
 */

import type { ManifestTransform } from "workbox-build";

export const PRECACHE_EXCLUDED_CHUNKS: readonly RegExp[] = [
  /^assets\/charts-/,
  /^assets\/InsightsPage-/,
];

export const filterPrecacheManifest: ManifestTransform = (entries) => ({
  manifest: entries.filter(
    (entry) => !PRECACHE_EXCLUDED_CHUNKS.some((pattern) => pattern.test(entry.url)),
  ),
  warnings: [],
});
