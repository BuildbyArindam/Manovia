/**
 * The model manifest: what may be downloaded, from where, and how it is verified.
 *
 * `public/private/model-manifest.json` is fetched, never imported, so it stays out
 * of the JS bundle and out of the service worker's precache (same rule as the safety
 * bundle). Parsing is strict and fails loudly: a manifest that silently lost a hash
 * would turn the integrity check into decoration.
 *
 * The load-bearing rule is `isInstallable()`. A model is installable only when it is
 * `available` **and** either its runtime manages its own artifacts (WebLLM does) or
 * every file carries a sha256. Nothing hashes a file it did not download, so an
 * unpinned entry means "nobody has verified this artifact" and the manager refuses.
 */

import type { ModelCapability, ModelId } from "./types";

export type ModelRuntimeKind = "transformers" | "webllm";
export type ModelStatus = "available" | "planned";
export type ModelFileKind = "config" | "tokenizer" | "weights" | "vocab";

export const MODEL_IDS: readonly ModelId[] = ["emotion", "safety", "reply"];

export class ManifestError extends Error {
  constructor(message: string) {
    super(`model manifest: ${message}`);
    this.name = "ManifestError";
  }
}

export interface ModelFileEntry {
  path: string;
  kind: ModelFileKind;
  url: string;
  /** 0 until `npm run models:pin` has fetched the file. */
  bytes: number;
  /** null until pinned. A null hash makes the model non-installable. */
  sha256: string | null;
}

export interface ModelEntry {
  id: ModelId;
  kind: string;
  runtime: ModelRuntimeKind;
  capability: ModelCapability;
  status: ModelStatus;
  /** What the UI calls it. */
  label: string;
  /** The honest caveat shown next to it: smaller, slower, less capable. */
  honest_note: string;
  upstream: string | null;
  licence: string | null;
  /** True when the runtime fetches and caches its own artifacts (WebLLM). */
  managed_by_runtime: boolean;
  /** Classifier labels, empty for a language model. */
  labels: string[];
  files: ModelFileEntry[];
  total_bytes: number;
}

export interface ModelManifest {
  format: string;
  generated_by: string;
  description: string;
  checksum_algorithm: "sha256";
  pinned: boolean;
  models: ModelEntry[];
}

const FILE_KINDS: readonly ModelFileKind[] = ["config", "tokenizer", "weights", "vocab"];
const RUNTIMES: readonly ModelRuntimeKind[] = ["transformers", "webllm"];
const STATUSES: readonly ModelStatus[] = ["available", "planned"];

function asRecord(value: unknown, where: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ManifestError(`${where} must be an object`);
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown, where: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new ManifestError(`${where} must be a non-empty string`);
  }
  return value;
}

function asOptionalString(value: unknown, where: string): string | null {
  if (value === null) {
    return null;
  }
  return asString(value, where);
}

function asBoolean(value: unknown, where: string): boolean {
  if (typeof value !== "boolean") {
    throw new ManifestError(`${where} must be a boolean`);
  }
  return value;
}

function asNumber(value: unknown, where: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new ManifestError(`${where} must be a non-negative number`);
  }
  return value;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], where: string): T {
  if (typeof value === "string" && (allowed as readonly string[]).includes(value)) {
    return value as T;
  }
  throw new ManifestError(`${where} must be one of ${allowed.join(", ")}`);
}

function parseFile(value: unknown, where: string): ModelFileEntry {
  const raw = asRecord(value, where);
  const sha256 = raw.sha256;
  if (sha256 !== null && typeof sha256 !== "string") {
    throw new ManifestError(`${where}.sha256 must be a hex string or null`);
  }
  if (typeof sha256 === "string" && !/^[0-9a-f]{64}$/.test(sha256)) {
    throw new ManifestError(`${where}.sha256 is not a sha256 hex digest`);
  }
  return {
    path: asString(raw.path, `${where}.path`),
    kind: oneOf(raw.kind, FILE_KINDS, `${where}.kind`),
    url: asString(raw.url, `${where}.url`),
    bytes: asNumber(raw.bytes, `${where}.bytes`),
    sha256,
  };
}

function parseModel(value: unknown, where: string): ModelEntry {
  const raw = asRecord(value, where);
  const files = Array.isArray(raw.files) ? raw.files : [];
  const managedByRuntime = asBoolean(raw.managed_by_runtime, `${where}.managed_by_runtime`);
  if (
    !managedByRuntime &&
    files.length === 0 &&
    oneOf(raw.status, STATUSES, `${where}.status`) === "available"
  ) {
    throw new ManifestError(`${where} is available but lists no files`);
  }
  const parsedFiles = files.map((file, index) => parseFile(file, `${where}.files[${index}]`));
  const totalBytes = asNumber(raw.total_bytes, `${where}.total_bytes`);
  const declared = parsedFiles.reduce((sum, file) => sum + file.bytes, 0);
  if (totalBytes !== declared) {
    throw new ManifestError(
      `${where}.total_bytes is ${totalBytes} but its files declare ${declared}; ` +
        "re-run `npm run models:manifest` or `models:pin`",
    );
  }
  return {
    id: oneOf(raw.id, MODEL_IDS, `${where}.id`),
    kind: asString(raw.kind, `${where}.kind`),
    runtime: oneOf(raw.runtime, RUNTIMES, `${where}.runtime`),
    capability: oneOf(raw.capability, ["classifiers", "replies"] as const, `${where}.capability`),
    status: oneOf(raw.status, STATUSES, `${where}.status`),
    label: asString(raw.label, `${where}.label`),
    honest_note: asString(raw.honest_note, `${where}.honest_note`),
    upstream: asOptionalString(raw.upstream, `${where}.upstream`),
    licence: asOptionalString(raw.licence, `${where}.licence`),
    managed_by_runtime: managedByRuntime,
    labels: (Array.isArray(raw.labels) ? raw.labels : []).map((label, index) =>
      asString(label, `${where}.labels[${index}]`),
    ),
    files: parsedFiles,
    total_bytes: totalBytes,
  };
}

export function parseModelManifest(value: unknown): ModelManifest {
  const raw = asRecord(value, "manifest");
  const format = asString(raw.format, "format");
  if (format !== "manovia-model-manifest/1") {
    throw new ManifestError(`unsupported format ${format}`);
  }
  if (raw.checksum_algorithm !== "sha256") {
    throw new ManifestError("only sha256 is supported");
  }
  const models = Array.isArray(raw.models) ? raw.models : [];
  if (models.length === 0) {
    throw new ManifestError("no models listed");
  }
  const parsed = models.map((model, index) => parseModel(model, `models[${index}]`));
  const ids = parsed.map((model) => model.id);
  if (new Set(ids).size !== ids.length) {
    throw new ManifestError("duplicate model ids");
  }
  return {
    format,
    generated_by: asString(raw.generated_by, "generated_by"),
    description: asString(raw.description, "description"),
    checksum_algorithm: "sha256",
    pinned: asBoolean(raw.pinned, "pinned"),
    models: parsed,
  };
}

/** Why a model cannot be installed, or null when it can. */
export function installBlocker(entry: ModelEntry): string | null {
  if (entry.status !== "available") {
    return "not shipped yet";
  }
  if (entry.managed_by_runtime) {
    return null;
  }
  if (entry.files.length === 0) {
    return "no files declared";
  }
  const unpinned = entry.files.filter((file) => file.sha256 === null);
  if (unpinned.length > 0) {
    return `integrity hash missing for ${unpinned.map((file) => file.path).join(", ")} (run \`npm run models:pin\`)`;
  }
  return null;
}

export function isInstallable(entry: ModelEntry): boolean {
  return installBlocker(entry) === null;
}

export function modelById(manifest: ModelManifest, id: ModelId): ModelEntry | undefined {
  return manifest.models.find((model) => model.id === id);
}

/** Bytes the user is agreeing to download for these models. */
export function declaredBytes(manifest: ModelManifest, ids?: readonly ModelId[]): number {
  const wanted =
    ids === undefined ? manifest.models : manifest.models.filter((m) => ids.includes(m.id));
  return wanted.reduce((sum, model) => sum + model.total_bytes, 0);
}
