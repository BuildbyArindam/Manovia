#!/usr/bin/env node
/**
 * Pin the on-device model artifacts: fetch each file, record its size and sha256.
 *
 * This is the only script allowed to fill in integrity values, and it fills them
 * from bytes it actually downloaded — never from a guess. Run it on a machine with
 * network access, then commit `public/private/model-manifest.json`:
 *
 *     npm run models:pin            # fetch + hash every unpinned file
 *     npm run models:pin -- --dry-run   # show what would be fetched
 *     npm run models:pin -- --model emotion
 *
 * The download manager refuses to install a model whose files are not pinned, so
 * until this has run, Private Mode offers the rules engine and template replies and
 * says plainly that no model is downloaded. That is deliberate: an integrity check
 * against an invented hash is worse than no check, because it looks like a
 * guarantee.
 *
 * Check the printed digests against the upstream repository before committing.
 */

import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..");
const MANIFEST = join(ROOT, "public", "private", "model-manifest.json");

const args = process.argv.slice(2);
const dryRun = args.includes("--dry-run");
const onlyModel = args.includes("--model") ? args[args.indexOf("--model") + 1] : null;

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

/** Fetch a URL and hash it as it streams, so a 2 GB file never lands in memory. */
async function hashUrl(url) {
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status} for ${url}`);
  }
  const hash = createHash("sha256");
  let bytes = 0;
  for await (const chunk of response.body) {
    const buffer = Buffer.from(chunk);
    hash.update(buffer);
    bytes += buffer.byteLength;
  }
  return { sha256: hash.digest("hex"), bytes };
}

async function main() {
  let manifest;
  try {
    manifest = JSON.parse(readFileSync(MANIFEST, "utf8"));
  } catch (error) {
    fail(`cannot read ${relative(ROOT, MANIFEST)}: ${error.message}`);
  }

  const targets = manifest.models.filter(
    (model) => (onlyModel === null || model.id === onlyModel) && !model.managed_by_runtime,
  );
  const files = targets.flatMap((model) => model.files.map((file) => ({ model, file })));
  if (files.length === 0) {
    process.stdout.write("nothing to pin\n");
    return 0;
  }

  if (dryRun) {
    for (const { model, file } of files) {
      process.stdout.write(`${model.id}/${file.path}  ${file.url}\n`);
    }
    return 0;
  }

  let pinned = 0;
  for (const { model, file } of files) {
    process.stdout.write(`fetching ${model.id}/${file.path} ... `);
    try {
      const result = await hashUrl(file.url);
      file.sha256 = result.sha256;
      file.bytes = result.bytes;
      pinned += 1;
      process.stdout.write(`${result.bytes} bytes  sha256 ${result.sha256}\n`);
    } catch (error) {
      process.stdout.write("FAILED\n");
      process.stderr.write(`  ${error.message}\n`);
      process.stderr.write(
        "  Leaving this file unpinned: the manager will refuse to install the model\n" +
          "  rather than accept an artifact nobody has hashed.\n",
      );
    }
  }

  manifest.pinned = manifest.models.every(
    (model) => model.managed_by_runtime || model.files.every((file) => file.sha256 !== null),
  );
  for (const model of manifest.models) {
    model.total_bytes = model.files.reduce((sum, file) => sum + (file.bytes ?? 0), 0);
  }
  writeFileSync(MANIFEST, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");

  const totalBytes = manifest.models.reduce((sum, model) => sum + model.total_bytes, 0);
  process.stdout.write(
    `pinned ${pinned}/${files.length} files; declared total ${totalBytes} bytes; ` +
      `manifest pinned=${manifest.pinned}\n`,
  );
  return pinned === files.length ? 0 : 1;
}

process.exitCode = await main();
