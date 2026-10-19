#!/usr/bin/env node
/**
 * Render a preview of the calm space from the app's own painter (Day 46).
 *
 * This is deliberately NOT a screenshot. The sandbox has no browser that can
 * run, so the README image is produced by executing the real `drawCalmFrame`
 * against a recording context that writes SVG instead of pixels. The layout,
 * colours and breath position therefore come from the same code the product
 * runs; the only thing missing is the browser's own rasterisation.
 *
 *   node scripts/render-calm-preview.mjs [--out ../docs/assets/day46-calm-space.svg]
 *
 * Convert to PNG with ImageMagick (as the README's file does):
 *   convert -background none day46-calm-space.svg day46-calm-space.png
 */

import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const ENTRY = join(ROOT, "src/features/calm/render2d.ts");
const WIDTH = 960;
const HEIGHT = 540;

function escape(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function svgContext() {
  const body = [];
  const defs = [];
  let gradientCount = 0;
  let fill = "#000000";
  let alpha = 1;

  const style = () => {
    const value = typeof fill === "string" ? fill : `url(#${fill.id})`;
    return `fill="${value}"${alpha < 1 ? ` fill-opacity="${alpha}"` : ""}`;
  };

  const ctx = {
    get fillStyle() {
      return fill;
    },
    set fillStyle(value) {
      fill = value;
    },
    strokeStyle: "",
    lineWidth: 1,
    get globalAlpha() {
      return alpha;
    },
    set globalAlpha(value) {
      alpha = value;
    },
    save() {},
    restore() {
      alpha = 1;
    },
    clearRect() {},
    fillRect(x, y, width, height) {
      body.push(`<rect x="${x}" y="${y}" width="${width}" height="${height}" ${style()}/>`);
    },
    beginPath() {
      ctx._path = [];
    },
    moveTo(x, y) {
      ctx._path = [`M ${x} ${y}`];
    },
    lineTo(x, y) {
      ctx._path.push(`L ${x} ${y}`);
    },
    closePath() {
      ctx._path.push("Z");
    },
    arc(x, y, radius) {
      ctx._arc = { x, y, radius };
    },
    fill() {
      if (ctx._arc !== undefined) {
        body.push(
          `<circle cx="${ctx._arc.x}" cy="${ctx._arc.y}" r="${ctx._arc.radius}" ${style()}/>`,
        );
        ctx._arc = undefined;
      }
      if (ctx._path !== undefined) {
        body.push(`<path d="${ctx._path.join(" ")}" ${style()}/>`);
        ctx._path = undefined;
      }
    },
    createLinearGradient(x0, y0, x1, y1) {
      const id = `linear-${(gradientCount += 1)}`;
      const gradient = {
        id,
        stops: [],
        addColorStop(offset, color) {
          gradient.stops.push({ offset, color });
        },
      };
      defs.push({ kind: "linear", id, x0, y0, x1, y1, gradient });
      return gradient;
    },
    createRadialGradient(x0, y0, r0, x1, y1, r1) {
      const id = `radial-${(gradientCount += 1)}`;
      const gradient = {
        id,
        stops: [],
        addColorStop(offset, color) {
          gradient.stops.push({ offset, color });
        },
      };
      defs.push({ kind: "radial", id, x0, y0, r0, x1, y1, r1, gradient });
      return gradient;
    },
  };
  return { ctx, body, defs };
}

function gradientMarkup(entry) {
  const stops = entry.gradient.stops
    .map((stop) => `<stop offset="${stop.offset}" stop-color="${escape(stop.color)}"/>`)
    .join("");
  if (entry.kind === "linear") {
    return `<linearGradient id="${entry.id}" gradientUnits="userSpaceOnUse" x1="${entry.x0}" y1="${entry.y0}" x2="${entry.x1}" y2="${entry.y1}">${stops}</linearGradient>`;
  }
  return `<radialGradient id="${entry.id}" gradientUnits="userSpaceOnUse" cx="${entry.x0}" cy="${entry.y0}" r="${entry.r1}">${stops}</radialGradient>`;
}

async function main() {
  const outIndex = process.argv.indexOf("--out");
  const out =
    outIndex === -1
      ? resolve(ROOT, "..", "docs", "assets", "day46-calm-space.svg")
      : resolve(process.argv[outIndex + 1]);

  const directory = mkdtempSync(join(tmpdir(), "manovia-preview-"));
  const bundled = join(directory, "render2d.mjs");
  await build({
    entryPoints: [ENTRY],
    bundle: true,
    format: "esm",
    platform: "neutral",
    target: "es2020",
    outfile: bundled,
    logLevel: "silent",
  });
  const { drawCalmFrame } = await import(pathToFileURL(bundled).href);
  const { ctx, body, defs } = svgContext();
  // Mid-inhale, where the orb is at its clearest.
  drawCalmFrame(ctx, {
    width: WIDTH,
    height: HEIGHT,
    elapsedMs: 3800,
    animateOrb: true,
  });

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img" aria-label="The Manovia calm space: a still evening lake, low hills, four firs and a soft breathing orb.">
<title>Manovia calm space — generated from the app's 2D painter</title>
<desc>This vector image was produced by running the product's drawCalmFrame painter against an SVG recording context. It is not a browser screenshot.</desc>
<defs>${defs.map(gradientMarkup).join("")}</defs>
<rect width="${WIDTH}" height="${HEIGHT}" fill="#26384a"/>
${body.join("\n")}
</svg>
`;
  writeFileSync(out, svg);
  console.log(`wrote ${out} (${(svg.length / 1024).toFixed(1)} KB, ${body.length} shapes)`);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
