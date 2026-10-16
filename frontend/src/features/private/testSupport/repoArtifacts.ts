/**
 * Reading the committed Private Mode artifacts from the repository.
 *
 * Node-only: tests and build scripts. The app never imports this — in a browser
 * the bundle arrives by `fetch` from `/private/safety-bundle.json` (see
 * `bundle/load.ts`), not from the filesystem.
 *
 * Keeping the paths and the JSONL/JSON reading in one place means the parity test
 * and the drift test cannot disagree about *which* artifact they are checking.
 */

import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { parseBundle, type PrivateBundle } from "../rules/bundleSchema";
import { parseModelManifest, type ModelManifest } from "../models/manifest";

const HERE = dirname(fileURLToPath(import.meta.url));

/** The repository root, resolved from this file rather than from `process.cwd()`. */
export const REPO_ROOT = join(HERE, "..", "..", "..", "..", "..");
export const FRONTEND_ROOT = join(REPO_ROOT, "frontend");
export const PARITY_DIR = join(REPO_ROOT, "evals", "parity");
export const CORPUS_PATH = join(PARITY_DIR, "corpus.jsonl");
export const EXPECTATIONS_PATH = join(PARITY_DIR, "python-expected.json");
export const NORMALISATION_PATH = join(PARITY_DIR, "python-normalisation.json");
export const OUTPUT_GUARD_PATH = join(PARITY_DIR, "python-output-guard.json");
export const CRISIS_PLANS_PATH = join(PARITY_DIR, "python-crisis-plans.json");
export const MODEL_MANIFEST_PATH = join(FRONTEND_ROOT, "public", "private", "model-manifest.json");
export const PROMPTS_DIR = join(REPO_ROOT, "backend", "app", "content", "prompts");
export const BUNDLE_PATH = join(FRONTEND_ROOT, "public", "private", "safety-bundle.json");

export function readText(path: string): string {
  return readFileSync(path, "utf8");
}

/** The committed on-device model manifest, parsed and validated. */
export function readModelManifest(): ModelManifest {
  return parseModelManifest(JSON.parse(readText(MODEL_MANIFEST_PATH)) as unknown);
}

export function sha256(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

export function sha256File(path: string): string {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

/** One message in the parity corpus. */
export interface CorpusEntry {
  id: string;
  text?: string;
  filler?: string;
  repeat?: number;
  append?: string;
  lang?: string;
  source: string;
  group: string;
}

/** The message text for a corpus entry, expanding a `{filler, repeat, append}` recipe. */
export function expand(entry: CorpusEntry): string {
  if (entry.text !== undefined) {
    return entry.text;
  }
  if (entry.filler === undefined || entry.repeat === undefined || entry.append === undefined) {
    throw new Error(`corpus entry ${entry.id} has neither text nor a complete repeat recipe`);
  }
  return entry.filler.repeat(entry.repeat) + entry.append;
}

/**
 * The corpus: one JSON object per line, plus a header line.
 *
 * The file is ASCII-escaped on purpose — it contains U+2028, which Python's
 * `str.splitlines` treats as a line break, so a literal copy would split one
 * record into two.
 */
export function readCorpus(): CorpusEntry[] {
  const [header, ...lines] = readText(CORPUS_PATH).split("\n");
  const meta = JSON.parse(header ?? "{}") as { format?: string; count?: number };
  if (meta.format !== "manovia-parity-corpus/1") {
    throw new Error(`corpus format ${JSON.stringify(meta.format)} is not supported`);
  }
  const entries = lines
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line) as CorpusEntry);
  if (entries.length !== meta.count) {
    throw new Error(`corpus has ${entries.length} rows but its header claims ${meta.count}`);
  }
  return entries;
}

/** What Python answered for one corpus message. */
export interface PythonExpectation {
  id: string;
  level: string;
  categories: string[];
  codes: string[];
  context: Record<string, unknown>;
}

export interface PythonExpectations {
  format: string;
  count: number;
  corpus_sha256: string;
  bundle_sha256: string | null;
  runtime: { python: string; unicodedata: string; seconds: number };
  levels: Record<string, number>;
  cases: PythonExpectation[];
}

export function readExpectations(): PythonExpectations {
  return JSON.parse(readText(EXPECTATIONS_PATH)) as PythonExpectations;
}

/**
 * The shipped system prompts, in sorted order.
 *
 * The output guard's prompt-echo check needs the same text Python's
 * `load_prompt_shingles` reads (`content/prompts/system_v*.md`), so the parity test
 * builds its shingles from these files rather than from a copy.
 */
export function readPromptTexts(): string[] {
  return readdirSync(PROMPTS_DIR)
    .filter((name) => name.startsWith("system_v") && name.endsWith(".md"))
    .sort()
    .map((name) => readText(join(PROMPTS_DIR, name)));
}

/** The compiled browser bundle, parsed and validated. */
export function readBundle(): PrivateBundle {
  return parseBundle(JSON.parse(readText(BUNDLE_PATH)) as unknown);
}
