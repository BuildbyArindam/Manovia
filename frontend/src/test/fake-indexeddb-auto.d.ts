/**
 * `fake-indexeddb/auto` installs globals as a side effect and its package.json
 * `exports` map does not expose the `auto.d.ts` that sits next to it, so TypeScript
 * cannot resolve the types through the subpath. The import in `vitest.setup.ts` is
 * for side effects only, which is all this declaration needs to say.
 */
declare module "fake-indexeddb/auto";
