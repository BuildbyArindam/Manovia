import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/App";
import "@/index.css";
// Initialises i18next synchronously with the bundled English tree. A Hindi or
// Bengali reader's tree is its own chunk (Day 25) and is awaited below, before
// the first render, so the first paint is still in the saved language.
import { currentAppLanguage, ensureLanguageLoaded } from "@/lib/i18n/i18n";
import { initInstallCapture } from "@/pwa/installPrompt";
import { registerServiceWorker } from "@/pwa/registerServiceWorker";

// Day 25: listen for the browser's install offer before anything renders, so
// it is not missed — and suppress the browser's own banner (the offer lives
// quietly in Settings).
initInstallCapture();

const container = document.getElementById("root");
if (container === null) {
  throw new Error("Manovia: #root element is missing from index.html");
}

function render(target: HTMLElement): void {
  createRoot(target).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}

// If the language chunk cannot load (offline before it was ever cached), the
// app still renders — in English — rather than not at all.
ensureLanguageLoaded(currentAppLanguage()).then(
  () => render(container),
  () => render(container),
);

// Day 25: the service worker registers after the page has loaded, so its
// precache download never competes with the first screen on a slow network.
// Production builds only (see registerServiceWorker).
if (document.readyState === "complete") {
  void registerServiceWorker();
} else {
  window.addEventListener(
    "load",
    () => {
      void registerServiceWorker();
    },
    { once: true },
  );
}
