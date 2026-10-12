#!/usr/bin/env node
/**
 * Day 23 hard-coded-string scanner, part 1: SOURCE scan.
 *
 * Finds user-facing English text that is NOT routed through the translation
 * layer, so nothing ships half-localised. Two passes:
 *
 * 1. JSX text nodes — any literal run of words between tags in a .tsx file
 *    (``<p>Loading…</p>`` is the canonical leak).
 * 2. i18n-relevant string attributes — aria-label, placeholder, title, alt —
 *    whose value is a plain English string literal.
 *
 * The scanner is deliberately dumb about semantics and conservative about
 * noise: it skips test files, imports, comments, class names and anything in
 * the allow-list. Leftovers are the work list; when the list is empty the UI
 * tree speaks only through `src/lib/i18n/locales`.
 *
 * Usage: node frontend/scripts/scan-ui-strings.mjs [--json]
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("../src/", import.meta.url).pathname;

/** Fragments that look like English but are not UI copy. */
const ALLOW_PATTERNS = [
  /^Manovia$/,
  /^[a-z-]+$/, // kebab-case ids, routes, test ids
  /^[A-Z_]+$/, // constants, event names
  /[/{}<>()[\]#@&=+*|^~`\\]/, // URLs, interpolations, markup residue
  /^\s*$/,
  /^[0-9:.\-\s]+$/,
  /^[a-z]+(:|,)?$/,
];

function looksLikeEnglish(text) {
  const trimmed = text.trim();
  if (trimmed.length < 3) return false;
  if (!/[a-zA-Z]/.test(trimmed)) return false;
  if (ALLOW_PATTERNS.some((pattern) => pattern.test(trimmed))) return false;
  // At least one lowercase word of 3+ letters: filters class lists, enums.
  if (!/[a-z]{3}/.test(trimmed)) return false;
  // A space, or an apostrophe in a word, marks prose rather than an identifier.
  return /\s/.test(trimmed) || /[a-z]'[a-z]/.test(trimmed) || /[.!?]$/.test(trimmed);
}

function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/^\s*\*.*$/gm, "");
}

function* walk(dir) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    const stat = statSync(path);
    if (stat.isDirectory()) {
      yield* walk(path);
    } else if (path.endsWith(".tsx") && !path.endsWith(".test.tsx")) {
      yield path;
    }
  }
}

const findings = [];

for (const file of walk(ROOT)) {
  const source = stripComments(readFileSync(file, "utf8"));

  // Pass 1 — JSX text nodes: `>` … `<` with prose between.
  const jsxText = />([^\n<>{}]+)</g;
  for (const match of source.matchAll(jsxText)) {
    const text = match[1].replace(/&[a-z]+;/g, " ").trim();
    if (looksLikeEnglish(text)) {
      findings.push({ file: relative(ROOT, file), kind: "jsx-text", text });
    }
  }

  // Pass 2 — i18n-relevant attribute literals.
  const attrs = /\b(aria-label|placeholder|title|alt|legend|hint|label)="([^"\n]+)"/g;
  for (const match of source.matchAll(attrs)) {
    const [, name, value] = match;
    if (looksLikeEnglish(value)) {
      findings.push({ file: relative(ROOT, file), kind: `attr:${name}`, text: value });
    }
  }
}

const byFile = new Map();
for (const finding of findings) {
  if (!byFile.has(finding.file)) byFile.set(finding.file, []);
  byFile.get(finding.file).push(finding);
}

if (process.argv.includes("--json")) {
  console.log(JSON.stringify(findings, null, 2));
} else {
  for (const [file, items] of [...byFile.entries()].sort()) {
    console.log(`\n${file} (${items.length})`);
    for (const item of items) {
      console.log(`  [${item.kind}] ${JSON.stringify(item.text)}`);
    }
  }
  console.log(`\nTotal: ${findings.length} candidate hard-coded strings in ${byFile.size} files`);
}
