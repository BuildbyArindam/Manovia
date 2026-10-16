#!/usr/bin/env node
/**
 * Build the on-device model manifest (Private Mode).
 *
 * One JSON file lists everything the browser may download: which runtime, which
 * upstream artifact, how big, and its sha256. It is generated rather than
 * hand-written so `total_bytes` and `pinned` cannot drift from the file list, and
 * it lives in `public/private/` so it is *fetched*, never bundled into a JS chunk
 * (the same rule as the safety bundle: the service worker must not precache it).
 *
 * INTEGRITY — READ THIS
 * Every entry is emitted with `sha256: null`, `bytes: 0` and `pinned: false`. The
 * download manager refuses to install an unpinned model, so nothing runs on an
 * artifact nobody has hashed. On a machine with network access:
 *
 *     npm run models:pin        # fetches each file, writes real bytes + sha256
 *
 * That script is the only thing that fills the hashes, and it prints what it
 * fetched so the values can be checked against the upstream repo. Inventing a
 * hash here would make the integrity check theatre, which is worse than no check.
 *
 * WHAT IS AND IS NOT PINNED
 * - emotion: `Xenova/distilbert-base-uncased-emotion`, ONNX quantised, transformers.js.
 * - safety:  **planned, no artifact**. A general toxicity classifier is not a
 *   self-harm detector and shipping one under the name "safety" would mislabel the
 *   risk. The deterministic rules engine is the safety layer (AGENTS.md rule 1);
 *   a purpose-built distilled classifier is `ml/` work and sits in the parking lot.
 *   The interface, the Fake and the adapter exist, so pinning an artifact later is
 *   a manifest change, not a code change.
 * - reply:   WebLLM's `Qwen2.5-1.5B-Instruct-q4f16_1-MLC`. WebLLM fetches and
 *   caches its own artifacts (params + wasm) through the Cache API and reports its
 *   own progress, so this entry is `managed_by_runtime`: the manager records consent
 *   and relays progress instead of duplicating the download.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUTPUT = join(HERE, "..", "public", "private", "model-manifest.json");

const HF = "https://huggingface.co";

const EMOTION_REPO = "Xenova/distilbert-base-uncased-emotion";
const EMOTION_FILES = [
  { path: "config.json", kind: "config" },
  { path: "tokenizer.json", kind: "tokenizer" },
  { path: "tokenizer_config.json", kind: "config" },
  { path: "onnx/model_quantized.onnx", kind: "weights" },
];

const REPLY_MODEL = "Qwen2.5-1.5B-Instruct-q4f16_1-MLC";

const models = [
  {
    id: "emotion",
    kind: "sequence-classification",
    runtime: "transformers",
    capability: "classifiers",
    status: "available",
    label: "Distilled emotion classifier (on-device)",
    honest_note:
      "A distilled ~66M-parameter classifier. It is less accurate than the server model it stands in for, it labels tone rather than diagnosis, and it never decides safety.",
    upstream: `${HF}/${EMOTION_REPO}`,
    licence: "MIT",
    managed_by_runtime: false,
    labels: ["neutral", "joy", "sadness", "anger", "fear", "surprise"],
    files: EMOTION_FILES.map((file) => ({
      path: file.path,
      kind: file.kind,
      url: `${HF}/${EMOTION_REPO}/resolve/main/${file.path}`,
      bytes: 0,
      sha256: null,
    })),
  },
  {
    id: "safety",
    kind: "sequence-classification",
    runtime: "transformers",
    capability: "classifiers",
    status: "planned",
    label: "Distilled crisis-signal classifier (not shipped yet)",
    honest_note:
      "No distilled self-harm classifier is pinned. A general toxicity model is not a substitute and would mislabel risk, so this slot stays empty: the deterministic rules engine is the safety layer, and models may only ever raise caution.",
    upstream: null,
    licence: null,
    managed_by_runtime: false,
    labels: [],
    files: [],
  },
  {
    id: "reply",
    kind: "causal-lm",
    runtime: "webllm",
    capability: "replies",
    status: "available",
    label: "Small on-device language model (WebGPU)",
    honest_note:
      "A 1.5B-parameter instruct model, quantised. It is much smaller than the server model: replies are shorter, plainer and less flexible, and every one of them still passes the on-device output guard.",
    upstream: `https://huggingface.co/mlc-ai/${REPLY_MODEL}`,
    licence: "Apache-2.0",
    managed_by_runtime: true,
    labels: [],
    files: [],
  },
];

const manifest = {
  format: "manovia-model-manifest/1",
  generated_by: "frontend/scripts/build-model-manifest.mjs",
  description:
    "What Private Mode may download, from where, and how it is verified. sha256 values are filled by `npm run models:pin` on a machine with network access; until then the manager refuses to install.",
  checksum_algorithm: "sha256",
  pinned: models.every((model) => model.files.every((file) => file.sha256 !== null)),
  models: models.map((model) => ({
    ...model,
    total_bytes: model.files.reduce((sum, file) => sum + file.bytes, 0),
  })),
};

const rendered = `${JSON.stringify(manifest, null, 2)}\n`;
const target = relative(join(HERE, ".."), OUTPUT);
const total = manifest.models.reduce((sum, model) => sum + model.total_bytes, 0);
const allFiles = manifest.models.flatMap((model) => model.files);
const pinnedFiles = allFiles.filter((file) => file.sha256).length;

// `--check` is the drift gate used by `npm run private:check`: the committed
// manifest must be exactly what this generator produces.
if (process.argv.includes("--check")) {
  let current = null;
  try {
    current = readFileSync(OUTPUT, "utf8");
  } catch {
    process.stderr.write(`MISSING ${target} — run \`npm run models:manifest\`\n`);
    process.exit(1);
  }
  if (current !== rendered) {
    process.stderr.write(`DRIFT ${target} — run \`npm run models:manifest\`\n`);
    process.exit(1);
  }
  process.stdout.write(
    `OK ${target}: ${manifest.models.length} models, ${allFiles.length} files, ` +
      `${pinnedFiles} pinned\n`,
  );
  process.exit(0);
}

mkdirSync(dirname(OUTPUT), { recursive: true });
writeFileSync(OUTPUT, rendered, "utf8");
process.stdout.write(
  `wrote ${target}\n` +
    `  ${manifest.models.length} models, ${allFiles.length} files, ${total} bytes declared\n` +
    `  pinned: ${pinnedFiles}/${allFiles.length} files (run \`npm run models:pin\` with network access)\n`,
);
