/**
 * The per-reply honesty badge.
 *
 * Every reply written on this device carries it, and it says three things the
 * reader is owed (scope item 5, "honest UX"):
 *
 *   1. **who wrote this** — the runtime label, which for the template writer
 *      reads as a template and for a real model reads as a small on-device
 *      model, never as "Manovia's AI" in the abstract;
 *   2. **whether it was stored** — `metadata.persisted` is the truth from the
 *      session, so "kept, encrypted, on this device" and "memory only" are
 *      reported rather than assumed from a toggle;
 *   3. **whether a model wrote it at all** — a crisis reply is pre-written copy
 *      shown with helplines, and a guard refusal is a pre-written answer that
 *      replaced a draft. Both are said plainly.
 *
 * It is a `<p>` inside the bubble, after the reply and before the crisis card,
 * so reading order stays: what was said, how it was produced, who to call.
 */

import type { ReactElement } from "react";

import type { ChatMetadata } from "@/features/chat/types";
import { useSensory } from "@/features/sensory/SensoryProvider";

import { PRIVATE_COPY } from "./copy";

/** The timings worth putting on the element, for tests and for a diagnostics view. */
export interface PrivateReplyTimings {
  assessedInMs: number;
  repliedInMs: number;
  totalMs: number;
  tokensPerSecond: number | null;
  tokens: number | null;
}

export interface PrivateReplyBadgeProps {
  metadata: ChatMetadata | null;
  /** From `PrivateDiagnostics.runtimeLabel`; null while nothing has answered. */
  runtimeLabel?: string | null;
  /** True when the output guard replaced what the model wrote. */
  guardReplaced?: boolean;
  /**
   * Carried as `data-*`, never as visible copy: milliseconds and tokens/second are
   * for the person who goes looking (an e2e measurement, a future diagnostics
   * line), not for somebody who is having a hard day.
   */
  timings?: PrivateReplyTimings | null;
}

export function PrivateReplyBadge({
  metadata,
  runtimeLabel = null,
  guardReplaced = false,
  timings = null,
}: PrivateReplyBadgeProps): ReactElement | null {
  const { overwhelmed } = useSensory();
  const crisis = metadata?.response_type === "crisis";
  // When someone has asked for the simplest view, provenance metadata is one
  // more line of chrome. It returns when the mode is turned off.
  if (overwhelmed) {
    return null;
  }
  const stored = metadata?.persisted === true;

  return (
    <p
      data-testid="private-reply-badge"
      data-stored={stored ? "true" : "false"}
      data-crisis={crisis ? "true" : "false"}
      data-guard-replaced={guardReplaced ? "true" : "false"}
      data-assessed-ms={timings === null ? undefined : timings.assessedInMs}
      data-replied-ms={timings === null ? undefined : timings.repliedInMs}
      data-total-ms={timings === null ? undefined : timings.totalMs}
      data-tokens={timings?.tokens ?? undefined}
      data-tokens-per-second={timings?.tokensPerSecond ?? undefined}
      className="mt-2 border-t border-border pt-2 text-xs text-ink-muted"
    >
      <span className="block font-semibold">
        {PRIVATE_COPY.answeredBy(runtimeLabel ?? "the on-device rules")}
      </span>
      <span className="block">
        {stored ? PRIVATE_COPY.storedBadge : PRIVATE_COPY.notStoredBadge}
      </span>
      {crisis ? <span className="block">{PRIVATE_COPY.crisisNoModel}</span> : null}
      {guardReplaced ? <span className="block">{PRIVATE_COPY.guardReplaced}</span> : null}
    </p>
  );
}
