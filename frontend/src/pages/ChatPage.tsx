import type { ReactElement } from "react";

import { PageHeader } from "@/components/PageHeader";

/**
 * Chat — placeholder for Day 5.
 *
 * The composer is intentionally inert: the message pipeline cannot ship before
 * the deterministic crisis gate that must run ahead of any model call
 * (AGENTS.md rule 1), so it arrives with the safety rules rather than after.
 */
export function ChatPage(): ReactElement {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Chat"
        lede="A quiet conversation about how you are doing. Take your time; nothing is timed."
      />

      <section
        aria-labelledby="chat-composer-heading"
        className="rounded-2xl border border-border bg-surface p-6 shadow-soft"
      >
        <h2 id="chat-composer-heading" className="text-lg">
          Say anything, or nothing at all
        </h2>
        <label htmlFor="chat-input" className="mt-3 block text-ink-muted">
          What is on your mind today?
        </label>
        <textarea
          id="chat-input"
          rows={4}
          disabled
          placeholder="Not available yet — the safety checks come first."
          className="mt-2 w-full rounded-xl border border-border-strong bg-surface-muted p-4 text-ink"
        />
        <button
          type="button"
          disabled
          className="mt-3 rounded-xl bg-accent-bg px-5 py-2 font-semibold text-accent-fg disabled:opacity-60"
        >
          Send
        </button>
        <p className="mt-3 text-sm text-ink-muted">
          Manovia is building the crisis detection that has to run before any AI reply. Until that
          is tested, there is nothing here that could answer you.
        </p>
      </section>
    </div>
  );
}
