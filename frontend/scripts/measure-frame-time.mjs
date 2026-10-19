#!/usr/bin/env node
/**
 * Frame-time measurement for the 2D calm-space fallback (Day 46).
 *
 * Two modes, because they answer different questions:
 *
 *   node scripts/measure-frame-time.mjs
 *       Headless CPU cost. Bundles the *real* painter with esbuild, drives it
 *       against a recording canvas stub and reports per-frame wall time and the
 *       FPS ceiling those times imply. This is the part of frame time our code
 *       controls, and the number to watch for regressions. It is **not** a GPU
 *       or vsync measurement.
 *
 *   node scripts/measure-frame-time.mjs --browser
 *       The real thing: opens a blank page in Chromium (Playwright), injects the
 *       same bundle, runs the painter inside `requestAnimationFrame` for N
 *       frames and reports rAF interval p50/p5. Needs a Playwright browser.
 *
 * Output (either mode):
 *   frames, p50 ms, p5 ms, p95 ms, FPS at p50, FPS at p5.
 *
 * p5 is the *slow* tail: 5% of frames took at least this long. Reporting "FPS
 * p5" as the typical worst case is deliberately pessimistic about the device.
 */

import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { performance } from "node:perf_hooks";

import { build } from "esbuild";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const ENTRY = join(ROOT, "src/features/calm/render2d.ts");

function arg(name, fallback) {
  const index = process.argv.indexOf(name);
  return index === -1 ? fallback : process.argv[index + 1];
}

const FRAMES = Number(arg("--frames", "1200"));
const WARMUP = 120;
const WIDTH = Number(arg("--width", "800"));
const HEIGHT = Number(arg("--height", "480"));

function percentile(sorted, fraction) {
  if (sorted.length === 0) return Number.NaN;
  const index = Math.min(
    sorted.length - 1,
    Math.max(0, Math.floor((sorted.length - 1) * fraction)),
  );
  return sorted[index];
}

function summary(times) {
  const sorted = [...times].sort((a, b) => a - b);
  const p50 = percentile(sorted, 0.5);
  const p5 = percentile(sorted, 0.05);
  const p95 = percentile(sorted, 0.95);
  return {
    frames: times.length,
    p50Ms: Number(p50.toFixed(3)),
    p5Ms: Number(p5.toFixed(3)),
    p95Ms: Number(p95.toFixed(3)),
    fpsAtP50: Number((1000 / p50).toFixed(1)),
    fpsAtP5: Number((1000 / p5).toFixed(1)),
  };
}

function stubContext() {
  const gradient = { addColorStop() {} };
  return {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    globalAlpha: 1,
    save() {},
    restore() {},
    clearRect() {},
    fillRect() {},
    beginPath() {},
    moveTo() {},
    lineTo() {},
    closePath() {},
    arc() {},
    fill() {},
    createLinearGradient() {
      return gradient;
    },
    createRadialGradient() {
      return gradient;
    },
  };
}

async function bundle(format, outfile) {
  await build({
    entryPoints: [ENTRY],
    bundle: true,
    format,
    globalName: format === "iife" ? "CalmScene" : undefined,
    platform: "neutral",
    target: "es2020",
    outfile,
    logLevel: "silent",
  });
  return outfile;
}

async function headless() {
  const directory = mkdtempSync(join(tmpdir(), "manovia-frame-"));
  const outfile = await bundle("esm", join(directory, "render2d.mjs"));
  const module = await import(pathToFileURL(outfile).href);
  const ctx = stubContext();
  const frame = {
    width: WIDTH,
    height: HEIGHT,
    elapsedMs: 0,
    animateOrb: true,
  };

  for (let i = 0; i < WARMUP; i += 1) {
    frame.elapsedMs = i * 16.67;
    module.drawCalmFrame(ctx, frame);
  }

  const times = [];
  for (let i = 0; i < FRAMES; i += 1) {
    frame.elapsedMs = (i + WARMUP) * 16.67;
    const started = performance.now();
    module.drawCalmFrame(ctx, frame);
    times.push(performance.now() - started);
  }
  return { mode: "headless-cpu", ...summary(times) };
}

async function browser() {
  let chromium;
  try {
    ({ chromium } = await import("playwright"));
  } catch {
    throw new Error("Playwright is not installed; run `npm ci` first.");
  }
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  const browserInstance = await chromium.launch({
    executablePath: executablePath ? executablePath : undefined,
    args: ["--no-sandbox"],
  });
  try {
    const page = await browserInstance.newPage({ viewport: { width: WIDTH, height: HEIGHT } });
    await page.setContent('<canvas id="scene" width="800" height="480"></canvas>');
    const directory = mkdtempSync(join(tmpdir(), "manovia-frame-"));
    const outfile = await bundle("iife", join(directory, "render2d.iife.js"));
    await page.addScriptTag({ content: readFileSync(outfile, "utf8") });
    const times = await page.evaluate(
      async ({ frames, width, height }) => {
        const canvas = document.getElementById("scene");
        const ctx = canvas.getContext("2d");
        const frame = { width, height, elapsedMs: 0, animateOrb: true };
        const samples = [];
        await new Promise((resolve) => {
          let previous = performance.now();
          const tick = (now) => {
            const delta = now - previous;
            previous = now;
            if (samples.length > 0) samples.push(delta);
            frame.elapsedMs = now;
            window.CalmScene.drawCalmFrame(ctx, frame);
            if (samples.length < frames) {
              requestAnimationFrame(tick);
            } else {
              resolve();
            }
          };
          requestAnimationFrame(tick);
        });
        return samples;
      },
      { frames: FRAMES, width: WIDTH, height: HEIGHT },
    );
    return { mode: "browser-raf", ...summary(times) };
  } finally {
    await browserInstance.close();
  }
}

async function main() {
  const result = process.argv.includes("--browser") ? await browser() : await headless();
  if (process.argv.includes("--json")) {
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    return;
  }
  console.log(
    `Calm-space 2D fallback — ${result.mode} (${result.frames} frames, ${WIDTH}x${HEIGHT})`,
  );
  console.log(`  frame p50 : ${result.p50Ms} ms  ->  ${result.fpsAtP50} FPS`);
  console.log(
    `  frame p5  : ${result.p5Ms} ms  ->  ${result.fpsAtP5} FPS (5% of frames were slower)`,
  );
  console.log(`  frame p95 : ${result.p95Ms} ms`);
  console.log(
    result.mode === "headless-cpu"
      ? "  Note: CPU cost of issuing draw calls on a recording context; not a GPU/vsync FPS.\n" +
          "  Run `npm run measure:frames -- --browser` on a real Chromium for rAF intervals."
      : "  Measured inside requestAnimationFrame in Chromium (vsync-limited to the display).",
  );
}

main().catch((error) => {
  console.error(`frame-time measurement failed: ${error.message}`);
  process.exit(1);
});
