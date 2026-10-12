/**
 * Locale parity guard (Day 23).
 *
 * A missing key in `hi` or `bn` would surface as an English fallback string in
 * the middle of a Hindi screen — easy to miss in review, jarring for a reader.
 * This test fails instead: every key in `en.json` must exist in every other
 * locale, with the same `{{placeholder}}` names, and vice versa (no orphan
 * keys). Structural drift between the trees is caught here at test time.
 */

import { describe, expect, it } from "vitest";

import bn from "./bn.json";
import en from "./en.json";
import hi from "./hi.json";

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix === "" ? key : `${prefix}.${key}`;
    if (typeof value === "string") {
      out.set(path, value);
    } else {
      for (const [innerKey, innerValue] of flatten(value, path)) {
        out.set(innerKey, innerValue);
      }
    }
  }
  return out;
}

function placeholders(value: string): string[] {
  return [...value.matchAll(/\{\{(\w+)\}\}/g)].map((match) => match[1] ?? "").sort();
}

const EN = flatten(en as Tree);
const LOCALES: Array<[string, Map<string, string>]> = [
  ["hi", flatten(hi as Tree)],
  ["bn", flatten(bn as Tree)],
];

describe("locale trees stay in lockstep with en", () => {
  for (const [name, tree] of LOCALES) {
    it(`${name} has exactly the keys en has`, () => {
      const missing = [...EN.keys()].filter((key) => !tree.has(key));
      const orphan = [...tree.keys()].filter((key) => !EN.has(key));
      expect(missing, `${name} is missing these en keys`).toEqual([]);
      expect(orphan, `${name} has keys en does not`).toEqual([]);
    });

    it(`${name} keeps the same interpolation placeholders`, () => {
      const mismatched: string[] = [];
      for (const [key, value] of EN) {
        const other = tree.get(key);
        if (other === undefined) {
          continue; // reported by the key-set test above
        }
        if (placeholders(value).join(",") !== placeholders(other).join(",")) {
          mismatched.push(key);
        }
      }
      expect(mismatched, `${name} placeholder mismatches`).toEqual([]);
    });

    it(`${name} never ships an empty string`, () => {
      const empty = [...tree.entries()].filter(([, value]) => value.trim() === "");
      expect(empty.map(([key]) => key)).toEqual([]);
    });
  }
});
