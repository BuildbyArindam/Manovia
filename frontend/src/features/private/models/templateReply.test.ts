/**
 * The template reply writer.
 *
 * The point of these tests is not that the reply is pleasant — it is that a reply
 * written with no model on the device still satisfies the same deterministic checks
 * as one written by a model: it passes the output guard in its own language, it
 * checks in at MEDIUM, it never invents copy, and it says plainly that it is not a
 * model.
 */

import { describe, expect, it } from "vitest";

import { readBundle } from "../testSupport/repoArtifacts";
import { buildOutputGuard } from "../rules/outputGuard";
import { compilePatternSet } from "../rules/bundleSchema";
import { TEMPLATE_REPLY_LABEL, TemplateReplyModel, hashText } from "./templateReply";
import type { ReplyRequest } from "./types";

const bundle = readBundle();
const patterns = compilePatternSet(bundle);
// No prompt text: a template reply cannot echo a prompt it was never given. The
// echo check is exercised in rules/outputGuard.test.ts against Python's verdicts.
const guard = buildOutputGuard(bundle, patterns);
const model = new TemplateReplyModel({ bundle, guard });

const LOCALES = ["en", "hi", "bn"] as const;
const LEVELS = ["none", "low", "medium"] as const;

function request(text: string, level: string, locale: string): ReplyRequest {
  return { text, level, locale, history: [], maxTokens: 160 };
}

describe("the template reply writer", () => {
  it("is ready without a download and says it is not a model", async () => {
    await model.init();
    expect(model.ready).toBe(true);
    expect(model.tokensPerSecond).toBeNull();
    const outcome = await model.generate(request("today was hard", "low", "en"));
    expect(outcome.degraded).toBe(true);
    expect(outcome.source).toBe("template");
    expect(outcome.runtimeLabel).toContain(TEMPLATE_REPLY_LABEL);
    expect(outcome.runtimeLabel).not.toMatch(/llm|gpt|neural/i);
  });

  it("streams the whole reply in one chunk, because there is nothing to wait for", async () => {
    const chunks: string[] = [];
    const outcome = await model.generate(
      request("I keep crying at night", "medium", "en"),
      (text) => chunks.push(text),
    );
    expect(chunks).toEqual([outcome.text]);
  });

  it.each(LOCALES)(
    "writes a reply in %s that the guard accepts at every non-crisis level",
    async (locale) => {
      for (const level of LEVELS) {
        const outcome = await model.generate(request("मुझे कुछ समझ नहीं आ रहा", level, locale));
        const verdict = guard.check(outcome.text, { risk: level, expectedLanguage: locale });
        expect(verdict.categories, `${locale}/${level}: ${outcome.text}`).toEqual([]);
        expect(verdict.passed, `${locale}/${level}`).toBe(true);
        expect(outcome.text.trim().length).toBeGreaterThan(20);
      }
    },
  );

  it("checks in at MEDIUM in every locale", async () => {
    for (const locale of LOCALES) {
      const outcome = await model.generate(request("nothing feels real lately", "medium", locale));
      expect(guard.hasCheckIn(outcome.text), locale).toBe(true);
      expect(
        guard.check(outcome.text, { risk: "medium", expectedLanguage: locale }).categories,
        locale,
      ).not.toContain("missing_check_in");
    }
  });

  it("stays short — a template reply has no business being an essay", async () => {
    for (const locale of LOCALES) {
      for (const level of LEVELS) {
        const outcome = await model.generate(request("tell me something", level, locale));
        const words = outcome.text.split(/\s+/).filter(Boolean);
        expect(words.length, `${locale}/${level}`).toBeLessThanOrEqual(90);
        expect(outcome.text.split("\n\n").length).toBeLessThanOrEqual(3);
      }
    }
  });

  it("only ever uses reviewed bundle content, never new copy", async () => {
    const reviewed = new Set<string>();
    for (const locale of LOCALES) {
      const content = bundle.crisis.locales[locale];
      for (const template of Object.values(content?.templates ?? {})) {
        for (const paragraph of [...template.body, template.title, template.closing]) {
          if (typeof paragraph === "string") {
            reviewed.add(paragraph);
          }
        }
      }
      reviewed.add(bundle.output_guard.trimmed_closings[locale] ?? "");
      reviewed.add(bundle.output_guard.safe_replies[locale] ?? "");
    }
    for (const locale of LOCALES) {
      for (const level of [...LEVELS, "high", "imminent"] as const) {
        const outcome = await model.generate(request(`a message about ${level}`, level, locale));
        for (const paragraph of outcome.text.split("\n\n")) {
          expect(reviewed, `${locale}/${level}: ${paragraph.slice(0, 40)}`).toContain(paragraph);
        }
      }
    }
  });

  it("returns the deterministic crisis copy if it is ever asked at HIGH or IMMINENT", async () => {
    const crisis = "Please call 112 now. Someone can help you through this.";
    const outcome = await model.generate({
      ...request("I have a plan and I want to die", "imminent", "en"),
      crisisMessage: crisis,
    });
    expect(outcome.text).toBe(crisis);
    // And with no crisis copy supplied, the reviewed safe reply rather than silence.
    const bare = await model.generate(request("I have a plan", "high", "en"));
    expect(bare.text).toBe(bundle.output_guard.safe_replies.en);
  });

  it("gives the same message the same reply, and different messages some variety", async () => {
    const first = await model.generate(request("I could not sleep at all", "low", "en"));
    const again = await model.generate(request("I could not sleep at all", "low", "en"));
    expect(again.text).toBe(first.text);

    const replies = new Set<string>();
    for (let index = 0; index < 12; index += 1) {
      const outcome = await model.generate(
        request(`a different day, number ${index}`, "low", "en"),
      );
      replies.add(outcome.text);
    }
    expect(replies.size).toBeGreaterThan(1);
  });

  it("falls back to a shipped locale for an unknown one", async () => {
    const outcome = await model.generate(request("hello", "low", "fr"));
    expect(outcome.runtimeLabel).toContain("en");
    expect(outcome.text.trim().length).toBeGreaterThan(20);
  });

  it("hashes stably, because the paragraph order depends on it", () => {
    expect(hashText("today was hard")).toBe(hashText("today was hard"));
    expect(hashText("today was hard")).not.toBe(hashText("today was hard!"));
    expect(hashText("")).toBe(2166136261);
    expect(Number.isInteger(hashText("x"))).toBe(true);
  });
});
