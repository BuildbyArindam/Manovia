/**
 * The seam between Private Mode and the heavy runtimes.
 *
 * `@huggingface/transformers`, `onnxruntime-web` and `@mlc-ai/web-llm` are not
 * dependencies of this repository. That is deliberate:
 *
 *   - they are large (tens of MB of JS plus a WASM build), and installing them would
 *     change every build for every user, including the ones whose devices cannot run
 *     them;
 *   - they cannot be exercised offline, so a test that imported them would be a test
 *     of a network fetch;
 *   - AGENTS.md requires every external dependency to sit behind an interface with a
 *     Fake, so the app must be able to run — and be tested — without them.
 *
 * So the adapters load them through this loader, by specifier, at the moment the user
 * consents to a model. In a deployment that installs them, the real `import()` runs.
 * In this repository it rejects, and the adapter turns that into `ModelUnavailable`,
 * which the session degrades from rather than throwing at the user.
 *
 * Tests inject a `ModuleLoader` that returns a stub module, so the adapter logic —
 * progress mapping, label parsing, honest labelling, failure handling — is covered
 * without the library being present.
 */

import { ModelUnavailable } from "../types";
import type { ModelId } from "../types";

export type ModuleLoader = (specifier: string) => Promise<unknown>;

/** The real loader. `@vite-ignore` keeps the specifier out of the build graph. */
export const dynamicImport: ModuleLoader = (specifier) =>
  import(/* @vite-ignore */ specifier) as Promise<unknown>;

export interface RuntimeModuleOptions {
  modelId: ModelId;
  specifier: string;
  loader?: ModuleLoader;
}

/**
 * Load a runtime module, or explain why it is not there.
 *
 * The message distinguishes "not installed in this build" from "installed but it
 * failed to initialise", because the first is a deployment fact and the second is a
 * bug worth reporting — and because the UI shows them differently.
 */
export async function loadRuntimeModule(
  options: RuntimeModuleOptions,
): Promise<Record<string, unknown>> {
  const loader = options.loader ?? dynamicImport;
  let loaded: unknown;
  try {
    loaded = await loader(options.specifier);
  } catch (error) {
    throw new ModelUnavailable(
      options.modelId,
      `${options.specifier} is not available in this build (${
        error instanceof Error ? error.message : String(error)
      })`,
    );
  }
  const module = (loaded ?? {}) as Record<string, unknown>;
  // ESM namespaces and transpiled CJS both show up in the wild; accept either.
  const inner = module.default;
  if (inner !== undefined && typeof inner === "object" && inner !== null) {
    return { ...module, ...(inner as Record<string, unknown>) };
  }
  return module;
}

/** A named export, or a clear ModelUnavailable naming what was missing. */
export function requireExport<T>(
  module: Record<string, unknown>,
  names: readonly string[],
  modelId: ModelId,
  specifier: string,
): T {
  for (const name of names) {
    const value = module[name];
    if (typeof value === "function") {
      return value as T;
    }
  }
  throw new ModelUnavailable(
    modelId,
    `${specifier} exported none of ${names.join(", ")}; the runtime API has changed and this adapter needs updating`,
  );
}
