/**
 * Registers the `toHaveNoViolations` matcher's type.
 *
 * The `import "vitest"` makes this a module, so `declare module "vitest"`
 * augments the real one instead of replacing it — the same trick
 * `@testing-library/jest-dom` uses for its own matchers. The unused type
 * parameter mirrors the interfaces being extended.
 */

import "vitest";

declare module "vitest" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface Assertion<T = unknown> {
    toHaveNoViolations(): void;
  }
  interface AsymmetricMatchersContaining {
    toHaveNoViolations(): void;
  }
}
