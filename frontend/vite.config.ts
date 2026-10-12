import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import { defineConfig } from "vitest/config";

/**
 * Single Vite/Vitest configuration.
 *
 * The dev server proxies `/api` to the FastAPI backend so the browser only ever
 * talks to its own origin (the sandbox preview is served through a proxy host,
 * so nothing may call `localhost` from the client).
 */
/**
 * Content-Security-Policy for the *dev* server.
 *
 * `script-src` needs `'unsafe-inline'` here and only here: `@vitejs/plugin-react`
 * injects an inline Refresh preamble into the page, and `'unsafe-eval'` is what
 * the HMR client needs for source-map evaluation. Production is served by
 * `docker/nginx.conf`, which ships the strict policy (`script-src 'self'`) and
 * is asserted by `src/security/csp.test.ts` — so this dev relaxation can never
 * be what protects a real deployment.
 *
 * `connect-src` allows the HMR websocket (`ws:`) and nothing else off-origin;
 * everything else is same-origin, which is what makes the proxy-only topology
 * (the browser never calls `localhost` directly) enforceable rather than merely
 * conventional.
 *
 * The two Google Fonts origins mirror the production allowance (see
 * `docker/security-headers.conf`) so Hindi and Bengali render with Noto Sans
 * Devanagari / Bengali in development too.
 */
export const DEV_CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "img-src 'self' data:",
  "font-src 'self' https://fonts.gstatic.com",
  "connect-src 'self' ws: wss:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      injectRegister: null,
      manifest: false,
      injectManifest: {
        globPatterns: ["**/*.{js,css,html,svg,png,webmanifest}"],
      },
      devOptions: {
        enabled: true,
        type: "module",
      },
    }),
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/recharts") || id.includes("node_modules/d3-")) {
            return "vendor-charts";
          }
          if (id.includes("src/lib/i18n/locales/")) {
            return "locales";
          }
          if (id.includes("node_modules/i18next") || id.includes("node_modules/react-i18next")) {
            return "vendor-i18n";
          }
          if (
            id.includes("node_modules/@tanstack/react-query") ||
            id.includes("node_modules/@tanstack/query-core")
          ) {
            return "vendor-query";
          }
          if (
            id.includes("node_modules/react/") ||
            id.includes("node_modules/react-dom/") ||
            id.includes("node_modules/react-router") ||
            id.includes("node_modules/scheduler/")
          ) {
            return "vendor-react";
          }
          return undefined;
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    // Bind on every interface: the preview proxy reaches the dev server by host
    // name, never by 127.0.0.1.
    host: true,
    port: 5173,
    allowedHosts: true,
    headers: {
      "Content-Security-Policy": DEV_CSP,
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    },
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: true,
    port: 4173,
    allowedHosts: true,
    headers: {
      "Content-Security-Policy": DEV_CSP,
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
