/**
 * The template reply writer: what Private Mode says when no model is running.
 *
 * This is not a fallback that pretends to be a model. It is the honest answer for
 * three real situations: nothing has been downloaded yet, the device cannot run the
 * reply model (no WebGPU), or the model failed to load. In all three the user still
 * gets a reply, still gets the safety layer, and still gets told which of those is
 * happening (`degraded: true`, `runtimeLabel`).
 *
 * It writes **no new copy**. Every paragraph comes from the reviewed, translated
 * content the server already uses — `check_in.low`, `check_in.medium` and the guard's
 * `trimmed_closings` in the bundle — so a template reply has been through the same
 * review as a crisis card, and it cannot drift into a diagnosis or a promise because
 * it has no vocabulary of its own. Paragraphs are chosen by a hash of the message, so
 * the same message always gets the same reply: predictable in tests and predictable
 * for a person who reads it twice.
 *
 * One deliberate difference from the server: the orchestrator appends the MEDIUM
 * check-in paragraph *after* a model's text, because a model may ignore the hint. A
 * template reply includes that paragraph itself, so `PrivateSession` does not append
 * it again (`ReplyOutcome.source === "template"`); repeating the sentence would read
 * like a stutter.
 */

import type { PrivateBundle } from "../rules/bundleSchema";
import type { RuleBasedOutputGuard } from "../rules/outputGuard";
import type { ProgressListener, ReplyModel, ReplyOutcome, ReplyRequest } from "./types";
import { guardLocale } from "../rules/outputGuard";
import type { RiskLevel } from "@/lib/endpoints";

/** Paragraphs a template reply may be built from, per locale, in preference order. */
interface ReplyPool {
  acknowledgement: string[];
  question: string[];
  closing: string[];
  /** The review'd check-in paragraph the server appends at MEDIUM. */
  mediumCheckIn: string | null;
  /** The guard's short closing, used when nothing else carries a check-in. */
  trimmedClosing: string;
}

export interface TemplateReplyOptions {
  bundle: PrivateBundle;
  /** Used to prove the composed reply satisfies the check-in rule. */
  guard: Pick<RuleBasedOutputGuard, "hasCheckIn" | "safeReply">;
  /** Shown in the UI beside the reply. Never implies a model wrote it. */
  label?: string;
}

export const TEMPLATE_REPLY_LABEL = "Template replies — no model on this device";

/** A reply is at most this many paragraphs; shortness is a safety property here. */
const MAX_PARAGRAPHS = 3;

export class TemplateReplyModel implements ReplyModel {
  readonly id = "reply" as const;
  readonly capability = "replies" as const;
  readonly label: string;

  private readonly bundle: PrivateBundle;
  private readonly guard: Pick<RuleBasedOutputGuard, "hasCheckIn" | "safeReply">;
  private loaded = false;

  constructor(options: TemplateReplyOptions) {
    this.bundle = options.bundle;
    this.guard = options.guard;
    this.label = options.label ?? TEMPLATE_REPLY_LABEL;
  }

  /** Always ready: there is nothing to download and nothing to load. */
  get ready(): boolean {
    return this.loaded;
  }

  get tokensPerSecond(): null {
    return null;
  }

  async init(listener?: ProgressListener): Promise<void> {
    this.loaded = true;
    listener?.({
      modelId: "reply",
      phase: "ready",
      receivedBytes: 0,
      totalBytes: 0,
      bytesPerSecond: null,
      detail: "template writer needs no download",
    });
  }

  async dispose(): Promise<void> {
    this.loaded = false;
  }

  async generate(request: ReplyRequest, onChunk?: (text: string) => void): Promise<ReplyOutcome> {
    if (!this.loaded) {
      await this.init();
    }
    const locale = guardLocale(request.locale);
    const pool = this.poolFor(locale);
    const level = request.level as RiskLevel;

    // The session never calls a reply model at HIGH or IMMINENT — `allow_llm` is
    // false there — but if something does, the deterministic crisis copy wins.
    if (level === "high" || level === "imminent") {
      const text = request.crisisMessage ?? this.guard.safeReply(locale);
      return this.finish(text, onChunk, locale, "crisis copy");
    }

    const paragraphs = this.compose(request.text, level, pool);
    return this.finish(paragraphs.join("\n\n"), onChunk, locale, "reviewed templates");
  }

  private finish(
    text: string,
    onChunk: ((text: string) => void) | undefined,
    locale: string,
    detail: string,
  ): ReplyOutcome {
    onChunk?.(text);
    return {
      text,
      // Word count, not tokens: there is no tokenizer here and inventing one would
      // make the tokens/second figure on the diagnostics panel meaningless.
      tokens: text.split(/\s+/).filter(Boolean).length,
      tokensPerSecond: null,
      runtimeLabel: `${this.label} (${locale}, ${detail})`,
      degraded: true,
      source: "template",
    };
  }

  /**
   * Pick two or three reviewed paragraphs.
   *
   * The rotation is a hash of the message, so replies vary between messages but are
   * stable for the same one. At MEDIUM the check-in paragraph is always included, and
   * if the chosen paragraphs do not already carry a check-in phrase the composition
   * adds one — the guard decides, in the reply's own language, so this works for
   * Hindi and Bengali without a hard-coded English question.
   */
  private compose(text: string, level: RiskLevel, pool: ReplyPool): string[] {
    const rotation = hashText(text);
    const chosen: string[] = [];

    const acknowledgement = pick(pool.acknowledgement, rotation);
    if (acknowledgement !== null) {
      chosen.push(acknowledgement);
    }
    const question = pick(pool.question, rotation + 1);
    if (question !== null && !chosen.includes(question)) {
      chosen.push(question);
    }

    const needsCheckIn = level === "medium" || level === "high" || level === "imminent";
    if (needsCheckIn && !chosen.some((paragraph) => this.guard.hasCheckIn(paragraph))) {
      const carrier =
        pool.mediumCheckIn !== null && this.guard.hasCheckIn(pool.mediumCheckIn)
          ? pool.mediumCheckIn
          : this.guard.hasCheckIn(pool.trimmedClosing)
            ? pool.trimmedClosing
            : (pool.mediumCheckIn ?? pool.trimmedClosing);
      if (!chosen.includes(carrier)) {
        chosen.push(carrier);
      }
    } else if (level === "medium" && pool.mediumCheckIn !== null) {
      // The check-in is already covered, but at MEDIUM the server always shows this
      // paragraph; keeping it makes the two modes read the same.
      if (!chosen.includes(pool.mediumCheckIn)) {
        chosen.push(pool.mediumCheckIn);
      }
    }

    const closing = pick(pool.closing, rotation + 2);
    if (closing !== null && chosen.length < MAX_PARAGRAPHS && !chosen.includes(closing)) {
      chosen.push(closing);
    }

    if (chosen.length === 0) {
      // No usable content in this locale: the guard's safe reply is the last resort
      // and it is reviewed too.
      return [this.guard.safeReply("en")];
    }
    return chosen.slice(0, MAX_PARAGRAPHS);
  }

  private poolFor(locale: string): ReplyPool {
    const locales = this.bundle.crisis.locales;
    const content = locales[locale] ?? locales.en;
    const low = content?.templates["check_in.low"];
    const medium = content?.templates["check_in.medium"];
    const trimmed = this.bundle.output_guard.trimmed_closings;
    const lowBody = low?.body ?? [];
    const mediumBody = medium?.body ?? [];
    return {
      acknowledgement: [lowBody[0], mediumBody[0]].filter(isParagraph),
      question: [lowBody[1], low?.closing].filter(isParagraph),
      closing: [low?.closing, medium?.closing].filter(isParagraph),
      // The last MEDIUM paragraph is the one the server appends as its guaranteed
      // check-in line.
      mediumCheckIn: mediumBody.length > 0 ? (mediumBody[mediumBody.length - 1] ?? null) : null,
      trimmedClosing:
        trimmed[locale as keyof typeof trimmed] ?? trimmed.en ?? "Would you like to tell me more?",
    };
  }
}

function isParagraph(value: string | undefined | null): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function pick(pool: string[], rotation: number): string | null {
  if (pool.length === 0) {
    return null;
  }
  const index = ((rotation % pool.length) + pool.length) % pool.length;
  return pool[index] ?? null;
}

/**
 * A small stable string hash (FNV-1a).
 *
 * Not cryptographic and not meant to be: it only has to turn "the same message"
 * into "the same paragraph order" across sessions and devices.
 */
export function hashText(text: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}
