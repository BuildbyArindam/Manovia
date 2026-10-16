/**
 * Two localStorage pointers, and nothing else.
 *
 * The truth about Private Mode lives in IndexedDB (`settings.privateMode`) and in
 * the panel. These flags exist only so the chat page can decide, synchronously
 * and without importing any of the on-device stack, whether to mount the lazy
 * Private Mode chunk:
 *
 *   panel  — the reader opened Private Mode at least once, so the panel (and the
 *            code behind it) is worth loading on the next visit;
 *   enabled — Private Mode was on when they left, so the boot should start
 *            immediately instead of waiting for the switch.
 *
 * A stale flag is harmless: if `enabled` says yes but the settings say no, the
 * panel simply shows the switch off. Keeping this module free of imports is the
 * point — it is on the critical path of the /chat bundle budget.
 */

const PANEL_KEY = "manovia.private.panel.v1";
const ENABLED_KEY = "manovia.private.enabled.v1";

function read(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "1";
  } catch {
    // Private browsing, a blocked storage API, or a quota error: assume "no" and
    // let the reader turn Private Mode on again in this session.
    return false;
  }
}

function write(key: string, value: boolean): void {
  try {
    if (value) {
      window.localStorage.setItem(key, "1");
    } else {
      window.localStorage.removeItem(key);
    }
  } catch {
    // Not worth an error the reader cannot act on.
  }
}

export function readPrivatePanelOpen(): boolean {
  return read(PANEL_KEY);
}

export function writePrivatePanelOpen(value: boolean): void {
  write(PANEL_KEY, value);
}

export function readPrivateEnabled(): boolean {
  return read(ENABLED_KEY);
}

export function writePrivateEnabled(value: boolean): void {
  write(ENABLED_KEY, value);
}
