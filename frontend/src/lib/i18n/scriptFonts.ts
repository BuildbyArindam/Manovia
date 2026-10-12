/**
 * Indic web fonts, only for the readers who need them (Day 25).
 *
 * Day 23 linked Noto Sans Devanagari and Noto Sans Bengali from `index.html`,
 * which made a cross-origin, render-blocking stylesheet part of *every* first
 * paint — the costliest request on a weak connection, for an English reader who
 * will never render a Devanagari glyph. Now the stylesheet is added only when
 * the interface language is Hindi or Bengali, and only the family that
 * language uses. `display=swap` still means text paints at once in a fallback
 * face. The CSP allowance (docker/security-headers.conf) is unchanged: exactly
 * these two origins.
 */

const STYLESHEETS: Record<string, string> = {
  hi: "https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;500;600;700&display=swap",
  bn: "https://fonts.googleapis.com/css2?family=Noto+Sans+Bengali:wght@400;500;600;700&display=swap",
};

const injected = new Set<string>();

function addLink(attributes: Record<string, string>): void {
  const link = document.createElement("link");
  for (const [name, value] of Object.entries(attributes)) {
    link.setAttribute(name, value);
  }
  document.head.append(link);
}

/** Add the web font for `language` once; a no-op for English. */
export function ensureScriptFonts(language: string): void {
  const href = STYLESHEETS[language];
  if (href === undefined || injected.has(language) || typeof document === "undefined") {
    return;
  }
  if (injected.size === 0) {
    addLink({ rel: "preconnect", href: "https://fonts.googleapis.com" });
    addLink({ rel: "preconnect", href: "https://fonts.gstatic.com", crossorigin: "" });
  }
  injected.add(language);
  addLink({ rel: "stylesheet", href, "data-script-font": language });
}
