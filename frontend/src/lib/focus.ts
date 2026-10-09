/** Focus helpers shared by the modal and anything else that traps focus. */

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "summary",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/**
 * The elements a keyboard user can reach inside `container`, in DOM order.
 *
 * Hidden subtrees are dropped so a trap never sends focus somewhere invisible.
 * Deliberately avoids `offsetParent` checks: jsdom does not implement layout,
 * and a filter that works in a browser but empties the list under test is worse
 * than no filter at all.
 */
export function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) =>
      element.closest("[hidden]") === null &&
      element.closest("[aria-hidden='true']") === null &&
      element.getAttribute("aria-hidden") !== "true",
  );
}
