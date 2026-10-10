/**
 * The permanent line: "AI companion — not a therapist. Need help now?"
 *
 * It is always on screen — before the first message, during a crisis, after an
 * error — because the one thing a self-help companion must never let a person
 * forget is what it is. AGENTS.md rule 3 requires the AI disclosure to be
 * reachable rather than merely shown once; this is the reachable copy, and the
 * button beside it opens the helpline dialog without navigating anywhere, so
 * nobody loses their place in the conversation.
 */

import { useState, type ReactElement } from "react";

import { CrisisResourcesModal } from "@/components/CrisisResourcesModal";

import { CHAT_COPY } from "./copy";

export function CompanionNotice(): ReactElement {
  const [open, setOpen] = useState(false);

  return (
    <>
      <p
        data-testid="companion-notice"
        className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-muted"
      >
        <span>{CHAT_COPY.companionNotice}</span>
        <button
          type="button"
          data-testid="companion-notice-help"
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => {
            setOpen(true);
          }}
          className="font-semibold text-accent underline underline-offset-2"
        >
          {CHAT_COPY.companionNoticeAction}
        </button>
      </p>
      {open ? (
        <CrisisResourcesModal
          onClose={() => {
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}
