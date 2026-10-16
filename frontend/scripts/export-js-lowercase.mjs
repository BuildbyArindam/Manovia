#!/usr/bin/env node
/**
 * Day 41: export the code points this JavaScript runtime lowercases.
 *
 * Python 3.11 folds with Unicode 14; a current browser folds with Unicode 17.
 * The characters encoded in between (Garay, Vithkuqi, the Gurung Khema block and
 * a handful of Latin extensions) lowercase in the browser and are *unassigned* —
 * therefore identity — in Python. Left alone, that is a real divergence: the same
 * message would normalise differently on the device and on the server, and the
 * two rules engines could disagree about it.
 *
 * The fix is not to guess on either side. This script writes the browser's own
 * answer to a fixture:
 *
 *     frontend/src/features/private/unicode/js-lowercase-codepoints.json
 *
 * `backend/scripts/export_casefold_cases.py` reads it and adds an identity
 * override for every listed code point, so the compiled bundle tells the browser
 * to leave those characters exactly where Python leaves them. Both fixtures are
 * regenerated and compared by tests (`jsLowercase.test.ts` here,
 * `test_private_bundle.py` there), and `verifyUnicodeClaim()` in the bundle
 * compiler re-checks the whole claim exhaustively on every verification run.
 *
 * USAGE
 *   node scripts/export-js-lowercase.mjs           # write the fixture
 *   node scripts/export-js-lowercase.mjs --check    # exit 1 on drift
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(HERE, "..");
const REPO = join(FRONTEND, "..");
export const OUTPUT_PATH = join(
  FRONTEND,
  "src",
  "features",
  "private",
  "unicode",
  "js-lowercase-codepoints.json",
);

export const FORMAT = "manovia-js-lowercase-codepoints/1";

/** Every code point whose `toLowerCase()` is not the identity, in ascending order. */
export function lowercaseCodepoints() {
  const found = [];
  for (let codepoint = 0; codepoint <= 0x10ffff; codepoint += 1) {
    if (codepoint >= 0xd800 && codepoint <= 0xdfff) continue;
    const char = String.fromCodePoint(codepoint);
    if (char.toLowerCase() !== char) {
      found.push(codepoint);
    }
  }
  return found;
}

export function buildPayload() {
  const codepoints = lowercaseCodepoints();
  const canonical = JSON.stringify(codepoints);
  return {
    format: FORMAT,
    generated_by: "frontend/scripts/export-js-lowercase.mjs",
    source: "String.prototype.toLowerCase() over every code point (surrogates excluded)",
    runtime: {
      node: process.versions.node,
      icu: process.versions.icu,
      unicode: process.versions.unicode,
    },
    count: codepoints.length,
    sha256: createHash("sha256").update(canonical).digest("hex"),
    codepoints,
  };
}

export function render(payload) {
  return `${JSON.stringify(payload, null, 2)}\n`;
}

function main(argv) {
  const payload = buildPayload();
  const rendered = render(payload);
  const here = relative(REPO, OUTPUT_PATH);

  if (argv.includes("--check")) {
    if (!existsSync(OUTPUT_PATH)) {
      process.stderr.write(`MISSING ${here}\n`);
      return 1;
    }
    const current = readFileSync(OUTPUT_PATH, "utf8");
    // The runtime block names the Node/ICU version that produced the list; a
    // different Node with the same Unicode version must not fail the build, so
    // the comparison is over the code points, not the provenance lines.
    const sameList =
      current === rendered ||
      (JSON.parse(current).sha256 === payload.sha256 &&
        JSON.stringify(JSON.parse(current).codepoints) === JSON.stringify(payload.codepoints));
    if (!sameList) {
      process.stderr.write(`DRIFT ${here} — re-run without --check\n`);
      return 1;
    }
    process.stdout.write(
      `OK ${here}: ${payload.count} code points, sha256 ${payload.sha256.slice(0, 16)}\n`,
    );
    return 0;
  }

  mkdirSync(dirname(OUTPUT_PATH), { recursive: true });
  writeFileSync(OUTPUT_PATH, rendered, "utf8");
  process.stdout.write(
    `wrote ${here}: ${payload.count} code points ` +
      `(Unicode ${payload.runtime.unicode}, ICU ${payload.runtime.icu}, sha256 ${payload.sha256.slice(0, 16)})\n`,
  );
  return 0;
}

if (process.argv[1] !== undefined && fileURLToPath(import.meta.url) === process.argv[1]) {
  process.exitCode = main(process.argv.slice(2));
}
