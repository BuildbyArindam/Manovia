import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/App";
import "@/index.css";
// Initialises i18next synchronously (bundled resources) before the first
// render, so every component's first paint is already in the saved language.
import "@/lib/i18n/i18n";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("Manovia: #root element is missing from index.html");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
