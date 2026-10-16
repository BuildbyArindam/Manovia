/**
 * The Private Mode session: the server's chat pipeline, minus the server.
 *
 * Same order of operations as `backend/app/services/chat/orchestrator.py`, because
 * the order *is* the safety design (AGENTS.md rule 1):
 *
 *    1. rules engine        deterministic assessment, before anything else
 *    2. escalation plan     policy for the level, rendered crisis copy, helplines
 *    3. crisis?             then stop: the template answers, no model is involved
 *    4. classifiers         emotion label, and a safety signal that may only raise
 *    5. reply model         or the template writer when there is no model
 *    6. output guard        every model reply, before it is shown
 *    7. check-in suffix     the MEDIUM line the server appends after a model reply
 *    8. local storage       encrypted with the passphrase-derived key, or not at all
 *
 * Nothing here touches the network. There is no `fetch`, no API client and no
 * telemetry: `private-session-no-network.test.ts` asserts that by reading this
 * module's source, and the Playwright test asserts it by blocking every request and
 * completing a conversation that includes a crisis message.
 *
 * What is *not* ported, and why:
 *   - the session-risk accumulator (Day 24) and rolling memory: both need a stored
 *     history the browser only has while unlocked, and neither can lower a level.
 *     Parking lot.
 *   - retrieval: the knowledge index is server-side and private mode has no copy.
 *   - the ML ensemble: replaced by the two classifier slots, which may only raise
 *     caution (`raiseOnCaution`), never lower a level.
 */

import type { ChatMetadata, ResponseType } from "@/features/chat/types";
import type { CrisisMessage } from "@/lib/endpoints";

import type { Escalator, EscalationPlan } from "../crisis/escalation";
import { LAST_RESORT_CRISIS_TEXT, plainText } from "../crisis/escalation";
import type {
  EmotionModel,
  EmotionResult,
  ReplyModel,
  ReplyOutcome,
  ReplyTurn,
  SafetySignal,
  SafetySignalModel,
} from "../models/types";
import { ModelUnavailable } from "../models/types";
import type { RuleBasedOutputGuard } from "../rules/outputGuard";
import type { RiskAssessment, RuleEngine } from "../rules/ruleEngine";
import type { SettingsStore, TranscriptStore } from "../store/localStore";

/** How many prior turns the reply model is given. Small on purpose: a 1.5B model
 * with a long history is slow, and the rules engine reads one message at a time. */
export const HISTORY_TURNS = 6;

/** What the reply model may emit before the guard's own limit applies. */
export const DEFAULT_MAX_REPLY_TOKENS = 160;

export interface PrivateSessionOptions {
  engine: RuleEngine;
  escalator: Escalator;
  guard: RuleBasedOutputGuard;
  /** The reply runtime: a real model, or the template writer. */
  replies: ReplyModel;
  /** Used when `replies` fails, so a broken model degrades instead of erroring. */
  fallbackReplies?: ReplyModel | null;
  emotion?: EmotionModel | null;
  safety?: SafetySignalModel | null;
  transcript: TranscriptStore;
  settings: SettingsStore;
  conversationId: string;
  /**
   * Let a crisis-adjacent classifier signal raise NONE/LOW to MEDIUM. It can never
   * lower a level (AGENTS.md ML ground rules), and the deterministic assessment is
   * the floor either way.
   */
  raiseOnCaution?: boolean;
  maxReplyTokens?: number;
  isoNow?: () => string;
  monotonic?: () => number;
}

export interface PrivateDiagnostics {
  /** Milliseconds for the rules engine alone — the part that must stay fast. */
  assessedInMs: number;
  repliedInMs: number;
  totalMs: number;
  /** Which runtime wrote the reply, in words a person can understand. */
  runtimeLabel: string;
  /** True when the reply was not written by a model. */
  degraded: boolean;
  tokensPerSecond: number | null;
  tokens: number | null;
  /** The guard's verdict on the model's text. Null on the crisis path: no model. */
  guard: { passed: boolean; reason: string | null; categories: string[] } | null;
  emotion: EmotionResult | null;
  safetySignal: SafetySignal | null;
  /** True when the safety signal raised the level. */
  raisedByModel: boolean;
  /** True when the turn was encrypted and written locally. */
  stored: boolean;
  /** Non-fatal problems worth showing on the diagnostics line. */
  warnings: string[];
}

export interface PrivateTurn {
  reply: string;
  metadata: ChatMetadata;
  diagnostics: PrivateDiagnostics;
  /** The assessment behind it, for tests and for the diagnostics drawer. */
  assessment: RiskAssessment;
  plan: EscalationPlan;
}

export type PrivateSessionErrorCode = "message_empty" | "storage_unavailable" | "no_reply_runtime";

export class PrivateSessionError extends Error {
  readonly code: PrivateSessionErrorCode;

  constructor(code: PrivateSessionErrorCode, message: string) {
    super(message);
    this.name = "PrivateSessionError";
    this.code = code;
  }
}

export interface SendHooks {
  /** Streamed text, as it arrives. The UI dispatches these as `token` actions. */
  onToken?: (text: string) => void;
}

export class PrivateSession {
  private readonly engine: RuleEngine;
  private readonly escalator: Escalator;
  private readonly guard: RuleBasedOutputGuard;
  private readonly replies: ReplyModel;
  private readonly fallbackReplies: ReplyModel | null;
  private readonly emotion: EmotionModel | null;
  private readonly safety: SafetySignalModel | null;
  private readonly transcript: TranscriptStore;
  private readonly settings: SettingsStore;
  private readonly conversationId: string;
  private readonly raiseOnCaution: boolean;
  private readonly maxReplyTokens: number;
  private readonly isoNow: () => string;
  private readonly monotonic: () => number;
  /** In-memory history, so a conversation works even when nothing is stored. */
  private readonly history: ReplyTurn[] = [];

  constructor(options: PrivateSessionOptions) {
    this.engine = options.engine;
    this.escalator = options.escalator;
    this.guard = options.guard;
    this.replies = options.replies;
    this.fallbackReplies = options.fallbackReplies ?? null;
    this.emotion = options.emotion ?? null;
    this.safety = options.safety ?? null;
    this.transcript = options.transcript;
    this.settings = options.settings;
    this.conversationId = options.conversationId;
    this.raiseOnCaution = options.raiseOnCaution ?? true;
    this.maxReplyTokens = options.maxReplyTokens ?? DEFAULT_MAX_REPLY_TOKENS;
    this.isoNow = options.isoNow ?? (() => new Date().toISOString());
    this.monotonic = options.monotonic ?? (() => performance.now());
  }

  /** True when this conversation can be read back later. */
  get storesTranscript(): boolean {
    return !this.transcript.locked;
  }

  /**
   * Answer one message. Nothing leaves the device; nothing here can throw for a
   * model being slow, unavailable or wrong — those become a degraded reply and a
   * warning, because a person mid-crisis must not be handed an error dialog.
   */
  async send(text: string, hooks: SendHooks = {}): Promise<PrivateTurn> {
    const started = this.monotonic();
    const warnings: string[] = [];
    if (text.trim() === "") {
      throw new PrivateSessionError("message_empty", "there is nothing to answer yet");
    }

    const settings = await this.settings.load();
    const region = settings.region;
    const locale = settings.locale ?? "en";

    // 1. The deterministic assessment, before any model runs.
    const assessedAt = this.monotonic();
    const assessment = this.engine.assess(text, locale);
    const assessedInMs = this.monotonic() - assessedAt;

    // 4a. The safety classifier runs before the plan is finalised, because it may
    // only ever raise caution. A failure here is a warning, never a lower level.
    let signal: SafetySignal | null = null;
    let level = assessment.level;
    let raisedByModel = false;
    if (this.safety !== null) {
      try {
        if (!this.safety.ready) {
          await this.safety.init();
        }
        signal = await this.safety.classify(text);
        if (this.raiseOnCaution && signal.raises_caution && (level === "none" || level === "low")) {
          level = "medium";
          raisedByModel = true;
          warnings.push("a classifier asked for more caution, so this turn is treated as MEDIUM");
        }
      } catch (error) {
        warnings.push(`safety classifier unavailable: ${describe(error)}`);
      }
    }
    const effective: RiskAssessment =
      level === assessment.level ? assessment : { ...assessment, level };

    // 2. The plan: policy, rendered copy, helplines, both fallback flags.
    const plan = this.escalator.plan(effective, { region, locale });

    // 3. Crisis: the template answers and no model is involved in any way.
    if (!plan.allowLlm) {
      const reply = plan.message === null ? LAST_RESORT_CRISIS_TEXT : plainText(plan.message);
      if (plan.message === null) {
        warnings.push("the crisis template was missing, so the last-resort text was shown");
      }
      const stored = await this.store(text, reply, plan, true);
      const totalMs = this.monotonic() - started;
      const metadata = this.metadata(plan, {
        responseType: "crisis",
        emotion: null,
        crisis: plan.message,
        checkIn: null,
        degraded: false,
        persisted: stored,
      });
      this.remember(text, reply);
      return {
        reply,
        metadata,
        assessment: effective,
        plan,
        diagnostics: {
          assessedInMs,
          repliedInMs: totalMs - assessedInMs,
          totalMs,
          runtimeLabel: "deterministic crisis copy (no model)",
          degraded: false,
          tokensPerSecond: null,
          tokens: null,
          guard: null,
          emotion: null,
          safetySignal: signal,
          raisedByModel,
          stored,
          warnings,
        },
      };
    }

    // 4b. Emotion, for the mood log and the reply's tone. Never for the risk level.
    let emotion: EmotionResult | null = null;
    if (this.emotion !== null) {
      try {
        if (!this.emotion.ready) {
          await this.emotion.init();
        }
        emotion = await this.emotion.classify(text);
      } catch (error) {
        warnings.push(`emotion classifier unavailable: ${describe(error)}`);
      }
    }

    // 5. The reply.
    const repliedAt = this.monotonic();
    let outcome: ReplyOutcome;
    try {
      outcome = await this.generate(plan, text, locale, hooks);
    } catch (error) {
      throw new PrivateSessionError(
        "no_reply_runtime",
        `no reply could be produced on this device: ${describe(error)}`,
      );
    }
    let reply = outcome.text;
    let degraded = outcome.degraded;
    let runtimeLabel = outcome.runtimeLabel;

    // 6. The guard, on the model's text — exactly where the server runs it.
    const verdict = this.guard.check(reply, { risk: level, expectedLanguage: locale });
    let replaced = false;
    if (!verdict.passed) {
      reply = verdict.text;
      replaced = true;
      degraded = true;
      runtimeLabel = `on-device output guard (${verdict.reason ?? "refused"})`;
      warnings.push(
        `the reply did not pass the output guard (${verdict.categories.join(", ")}), so a pre-written answer was shown instead`,
      );
    }

    // 7. The MEDIUM check-in line. The server appends it after a model's text so a
    // check-in exists even if the model ignored the hint; a template reply already
    // contains it, and repeating it would read like a stutter.
    const checkIn = level === "medium" ? plan.message : null;
    if (checkIn !== null && outcome.source !== "template" && checkIn.body.length > 0) {
      const suffix = checkIn.body[checkIn.body.length - 1];
      if (suffix !== undefined && !reply.endsWith(suffix)) {
        reply = `${reply}\n\n${suffix}`;
      }
    }

    const repliedInMs = this.monotonic() - repliedAt;
    const stored = await this.store(text, reply, plan, false);
    this.remember(text, reply);

    const responseType: ResponseType = replaced
      ? "fallback"
      : checkIn !== null
        ? "check_in"
        : "normal";

    return {
      reply,
      metadata: this.metadata(plan, {
        responseType,
        emotion: emotion?.label ?? null,
        crisis: null,
        checkIn,
        degraded,
        persisted: stored,
      }),
      assessment: effective,
      plan,
      diagnostics: {
        assessedInMs,
        repliedInMs,
        totalMs: this.monotonic() - started,
        runtimeLabel,
        degraded,
        tokensPerSecond: outcome.tokensPerSecond,
        tokens: outcome.tokens,
        guard: {
          passed: verdict.passed,
          reason: verdict.reason,
          categories: verdict.categories,
        },
        emotion,
        safetySignal: signal,
        raisedByModel,
        stored,
        warnings,
      },
    };
  }

  /** The turns this session remembers, oldest first, for the next prompt. */
  turns(): readonly ReplyTurn[] {
    return this.history;
  }

  /** Drop the in-memory history. The stored transcript is the store's business. */
  reset(): void {
    this.history.length = 0;
  }

  // ---------------------------------------------------------------- internals

  private async generate(
    plan: EscalationPlan,
    text: string,
    locale: string,
    hooks: SendHooks,
  ): Promise<ReplyOutcome> {
    const request = {
      text,
      level: plan.level,
      crisisMessage: null,
      locale: plan.locale ?? locale,
      history: [...this.history].slice(-HISTORY_TURNS),
      maxTokens: this.maxReplyTokens,
    };
    if (!this.replies.ready) {
      await this.replies.init();
    }
    try {
      return await this.replies.generate(request, hooks.onToken);
    } catch (error) {
      if (this.fallbackReplies === null) {
        throw error instanceof Error ? error : new ModelUnavailable("reply", describe(error));
      }
      // A model that will not run is not a reason to leave somebody without an
      // answer: fall back to the template writer and say so in the label.
      if (!this.fallbackReplies.ready) {
        await this.fallbackReplies.init();
      }
      return this.fallbackReplies.generate(request, hooks.onToken);
    }
  }

  private metadata(
    plan: EscalationPlan,
    fields: {
      responseType: ResponseType;
      emotion: string | null;
      crisis: CrisisMessage | null;
      checkIn: CrisisMessage | null;
      degraded: boolean;
      persisted: boolean;
    },
  ): ChatMetadata {
    return {
      risk_level: plan.level,
      emotion: fields.emotion,
      response_type: fields.responseType,
      resources: plan.resources,
      emergency: plan.emergency,
      crisis: fields.crisis,
      check_in: fields.checkIn,
      degraded: fields.degraded,
      persisted: fields.persisted,
      about_someone_else: plan.aboutSomeoneElse,
      locale: plan.locale,
      region: plan.region,
    };
  }

  /**
   * Write both turns locally, encrypted, or not at all.
   *
   * A storage failure never costs the person their reply — the same rule the server
   * follows on the crisis path — but it is reported, so the UI can show "not stored"
   * instead of implying the conversation was saved.
   */
  private async store(
    userText: string,
    reply: string,
    plan: EscalationPlan,
    crisis: boolean,
  ): Promise<boolean> {
    const createdAt = this.isoNow();
    try {
      const userRecord = await this.transcript.append(this.conversationId, {
        role: "user",
        text: userText,
        storedLevel: plan.storedLevel,
        createdAt,
        crisis,
      });
      if (userRecord === null) {
        return false;
      }
      const assistantRecord = await this.transcript.append(this.conversationId, {
        role: "assistant",
        text: reply,
        storedLevel: plan.storedLevel,
        createdAt: this.isoNow(),
        crisis,
      });
      return assistantRecord !== null;
    } catch {
      // The reply exists; losing it because a local write failed would punish the
      // person for a storage problem. The UI shows "not stored" instead.
      return false;
    }
  }

  private remember(userText: string, reply: string): void {
    this.history.push({ role: "user", text: userText });
    this.history.push({ role: "assistant", text: reply });
    while (this.history.length > HISTORY_TURNS * 2) {
      this.history.shift();
    }
  }
}

function describe(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
