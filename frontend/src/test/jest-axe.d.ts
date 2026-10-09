/**
 * Types for `jest-axe`, which ships none.
 *
 * A global script (no imports) so `declare module "jest-axe"` *declares* the
 * module rather than augmenting it. The vitest matcher augmentation lives in
 * `vitest-matchers.d.ts`, because it has to be a module to augment one.
 */

declare module "jest-axe" {
  export interface AxeNode {
    html: string;
    target: string[];
    failureSummary: string;
  }

  export interface AxeViolation {
    id: string;
    impact: string | null;
    help: string;
    helpUrl: string;
    nodes: AxeNode[];
  }

  export interface AxeResults {
    violations: AxeViolation[];
    passes: unknown[];
    incomplete: unknown[];
    inapplicable: unknown[];
  }

  export function axe(element: Element, options?: Record<string, unknown>): Promise<AxeResults>;

  export const toHaveNoViolations: {
    toHaveNoViolations(results: AxeResults): { pass: boolean; message: () => string };
  };
}
