import { describe, expect, it } from "vitest";

import { getFocusableElements } from "@/lib/focus";

function containerWith(html: string): HTMLElement {
  document.body.innerHTML = `<div id="trap">${html}</div>`;
  const container = document.querySelector<HTMLElement>("#trap");
  if (container === null) {
    throw new Error("fixture missing");
  }
  return container;
}

function labelOf(element: HTMLElement): string {
  const ariaLabel = element.getAttribute("aria-label");
  if (ariaLabel !== null) {
    return `${element.tagName}:${ariaLabel}`;
  }
  return element.textContent ?? element.tagName;
}

describe("getFocusableElements", () => {
  it("returns the reachable controls in document order", () => {
    const container = containerWith(`
      <a href="#one">one</a>
      <button type="button">two</button>
      <input type="text" aria-label="three" />
      <select aria-label="four"><option>a</option></select>
      <textarea aria-label="five"></textarea>
      <div tabindex="0">six</div>
    `);

    const labels = getFocusableElements(container).map(labelOf);

    expect(labels).toEqual(["one", "two", "INPUT:three", "SELECT:four", "TEXTAREA:five", "six"]);
  });

  it("skips disabled, hidden and aria-hidden controls", () => {
    const container = containerWith(`
      <button type="button" id="live">live</button>
      <button type="button" disabled>disabled</button>
      <input type="hidden" />
      <div hidden><button type="button">in a hidden subtree</button></div>
      <div aria-hidden="true"><button type="button">aria hidden</button></div>
    `);

    const ids = getFocusableElements(container).map((element) => element.id || element.tagName);

    expect(ids).toEqual(["live"]);
  });

  it("returns nothing for an empty container", () => {
    expect(getFocusableElements(containerWith("<p>nothing here</p>"))).toEqual([]);
  });
});
