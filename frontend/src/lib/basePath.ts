/**
 * Turning Vite's base path into the values the runtime needs.
 *
 * Vite exposes the build's public path as `import.meta.env.BASE_URL`, always
 * with a trailing slash: `"/"` for a site at the origin root (Docker/nginx,
 * Netlify, Cloudflare Pages, a custom domain) and `"/Manovia/"` for a GitHub
 * Pages *project* site served from `https://<user>.github.io/Manovia/`.
 *
 * Two consumers care:
 *
 * * React Router wants a basename **without** the trailing slash, and it must
 *   match the path the assets are served under. Without it, a page loaded from
 *   `/Manovia/` is matched as the route `"/Manovia/"` — nothing in the route
 *   table matches, and every deep link lands on the not-found page.
 * * The service worker matches precache keys by absolute URL, so the shell
 *   document has to be looked up relative to the worker's own location rather
 *   than as a hardcoded `/index.html`.
 *
 * Both helpers are pure and total, so the unit tests pin the behaviour instead
 * of the test having to run a real build.
 */

/**
 * React Router's `basename` for a Vite `base`.
 *
 * `"/"` stays `"/"` (the router treats it as no basename); `"/Manovia/"` and
 * `"/a/b/"` lose the trailing slash. An absolute base (`https://cdn.example/app/`)
 * is reduced to its pathname, because that is what the browser puts in
 * `location.pathname` for this page.
 */
export function routerBasename(baseUrl: string): string {
  const path = toPathname(baseUrl).replace(/\/+$/, "");
  return path === "" ? "/" : path;
}

/**
 * The absolute URL of the app shell document, resolved against the worker's
 * own location (`…/sw.js` → `…/index.html`).
 *
 * `createHandlerBoundToURL()` matches exact precache keys, and the precache
 * manifest that `vite-plugin-pwa` injects is base-relative (`index.html`,
 * `assets/…`). A hardcoded `"/index.html"` therefore only ever worked when the
 * app was served from the origin root.
 */
export function appShellUrl(workerLocation: { href: string } | string): string {
  const href = typeof workerLocation === "string" ? workerLocation : workerLocation.href;
  return new URL("index.html", href).href;
}

/** Pathname of a base path, tolerating a full URL and a missing leading slash. */
function toPathname(baseUrl: string): string {
  let path = baseUrl.trim();
  if (path === "") {
    return "/";
  }
  // A full URL: keep only the path.
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(path)) {
    path = new URL(path).pathname;
  }
  return path.startsWith("/") ? path : `/${path}`;
}
