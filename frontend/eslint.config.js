import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import prettier from "eslint-config-prettier";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    // Generated Playwright reports contain vendored/minified third-party JS,
    // not application source. Running e2e must not make the next lint fail.
    ignores: [
      "dist",
      "coverage",
      "node_modules",
      "*.tsbuildinfo",
      "playwright-report/**",
      "test-results/**",
      ".playwright/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // Everything TypeScript in the project, config files included: a lint rule
    // that only covers src/ would let the build config rot.
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.es2022,
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/consistent-type-imports": [
        "error",
        { prefer: "type-imports", fixStyle: "separate-type-imports" },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-console": ["error", { allow: ["warn", "error"] }],
      "object-shorthand": ["error", "always"],
    },
  },
  {
    // The e2e specs run in Node (Playwright), even though they drive a browser.
    files: ["e2e/**/*.ts"],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
  {
    // Config files run in Node, not the browser.
    files: ["*.config.{ts,js}", "vitest.setup.ts"],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
  {
    // `scripts/*.mjs` are operator tools (serve the built bundle with the
    // production headers and check the CSP in a real browser). They run in
    // Node, print to the console, and exit non-zero on failure — so `console`
    // and `process` are the point of the file, not a mistake in it.
    //
    // They also declare *browser* globals because the file spans two runtimes:
    // the Playwright driver is Node, while the callbacks it hands to
    // `page.evaluate()` and `page.addInitScript()` are serialised and executed
    // inside the page. Without these, a legitimate `document.addEventListener`
    // in injected code reads as an undefined-variable error and the honest fix
    // would look like a suppression comment instead.
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.node,
        ...globals.browser,
      },
    },
    rules: {
      "no-console": "off",
    },
  },
  prettier,
);
