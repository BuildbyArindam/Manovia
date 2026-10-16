/**
 * Loading the compiled safety bundle and the model manifest, in the browser.
 *
 * Both are static assets under `/private/`, built from this repository by
 * `npm run private:bundle` and `npm run models:manifest`. They are fetched, never
 * imported, for two reasons: they stay out of the JS bundle (and out of the service
 * worker's precache, whose glob patterns exclude JSON), and a stale asset can be
 * revalidated by the browser instead of requiring a new deploy of application code.
 *
 * This is the only module in Private Mode's core that opens a connection, and it is
 * allowed to fetch exactly two same-origin paths. It never sends anything: a GET for
 * a static file carries no message text, no user id and no region — the region is a
 * query on the *server* API in standard mode, and here it is only used to choose
 * which helplines to render from a file that is already on the device.
 *
 * `private-session-no-network.test.ts` asserts those limits by reading this file.
 */

import type { ModelManifest } from "../models/manifest";
import { parseModelManifest } from "../models/manifest";
import type { PrivateBundle } from "../rules/bundleSchema";
import { parseBundle } from "../rules/bundleSchema";

export const BUNDLE_URL = "/private/safety-bundle.json";
export const MANIFEST_URL = "/private/model-manifest.json";

export class BundleLoadError extends Error {
  readonly stage: "fetch" | "parse" | "integrity";

  constructor(stage: BundleLoadError["stage"], message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "BundleLoadError";
    this.stage = stage;
  }
}

export interface LoadedAsset<T> {
  value: T;
  /** Bytes as delivered, and their digest, for the diagnostics panel. */
  bytes: number;
  sha256: string;
  /** True when the response came from a cache rather than the network. */
  fromCache: boolean;
  loadedInMs: number;
}

export interface LoadOptions {
  /** Override for tests; defaults to the global `fetch`. */
  fetchImpl?: typeof fetch;
  /**
   * Refuse the asset unless its digest matches. The build does not inject one by
   * default — a hash baked into the app would have to be regenerated on every
   * content change, and the schema check below is what actually catches a corrupt or
   * half-written file — but a deployment that can supply one should.
   */
  expectedSha256?: string | null;
  /** `reload` in tests, so a run never reads a previous run's cache. */
  cache?: RequestCache;
}

async function digest(bytes: Uint8Array): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle === undefined) {
    throw new BundleLoadError(
      "integrity",
      "this browser has no WebCrypto to check the bundle with",
    );
  }
  const hash = await subtle.digest("SHA-256", bytes as BufferSource);
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function loadAsset<T>(
  url: string,
  parse: (value: unknown) => T,
  label: string,
  options: LoadOptions,
): Promise<LoadedAsset<T>> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const started = performance.now();
  let response: Response;
  try {
    response = await fetchImpl(url, {
      cache: options.cache ?? "force-cache",
      credentials: "omit",
      headers: { accept: "application/json" },
    });
  } catch (error) {
    throw new BundleLoadError(
      "fetch",
      `could not fetch ${label} from ${url}; Private Mode needs it before it can run`,
      { cause: error },
    );
  }
  if (!response.ok) {
    throw new BundleLoadError("fetch", `${label} responded ${response.status} at ${url}`);
  }
  const buffer = new Uint8Array(await response.arrayBuffer());
  const sha256 = await digest(buffer);
  if (options.expectedSha256 !== undefined && options.expectedSha256 !== null) {
    if (sha256 !== options.expectedSha256) {
      throw new BundleLoadError(
        "integrity",
        `${label} does not match its expected sha256 (got ${sha256.slice(0, 16)}, wanted ${options.expectedSha256.slice(0, 16)}); refusing to run safety rules from a file nobody recognises`,
      );
    }
  }
  let text: string;
  try {
    text = new TextDecoder().decode(buffer);
  } catch (error) {
    throw new BundleLoadError("parse", `${label} is not valid UTF-8`, { cause: error });
  }
  let value: T;
  try {
    value = parse(JSON.parse(text) as unknown);
  } catch (error) {
    throw new BundleLoadError(
      "parse",
      `${label} failed validation: ${error instanceof Error ? error.message : String(error)}`,
      { cause: error },
    );
  }
  return {
    value,
    bytes: buffer.byteLength,
    sha256,
    fromCache: response.status === 304 || (response.headers?.get?.("age") ?? "0") !== "0",
    loadedInMs: performance.now() - started,
  };
}

/** Fetch and validate the compiled safety bundle. */
export function loadSafetyBundle(options: LoadOptions = {}): Promise<LoadedAsset<PrivateBundle>> {
  return loadAsset(BUNDLE_URL, parseBundle, "the safety bundle", options);
}

/** Fetch and validate the on-device model manifest. */
export function loadModelManifest(options: LoadOptions = {}): Promise<LoadedAsset<ModelManifest>> {
  return loadAsset(MANIFEST_URL, parseModelManifest, "the model manifest", options);
}
