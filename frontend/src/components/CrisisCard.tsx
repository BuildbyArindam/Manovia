/**
 * The crisis card: pre-written words plus the helplines for one region.
 *
 * This is the visual half of AGENTS.md safety rule 2. At high risk the reply is
 * deterministic, and this component is what renders that reply — the copy arrives
 * already written and already localised from `content/i18n/*.json`, so nothing
 * here generates, reorders or paraphrases a word of it. What this component does
 * own is whether the card is *usable* by somebody in distress:
 *
 * - **Large tap targets.** Every link is at least 48px tall with generous
 *   padding, because hands that shake miss small buttons.
 * - **The emergency number first, and loud.** Somebody in immediate danger
 *   should not have to read a list to find it.
 * - **Keyboard reachable and announced.** Native anchors (no click handlers on
 *   divs), the global `:focus-visible` ring, and `role="status"` so a screen
 *   reader reads the card when it appears mid-conversation.
 * - **Never empty.** With no message and no resources it renders nothing rather
 *   than a blank box; the caller decides what to show instead.
 * - **Honest.** Entries the content file flags as needing re-verification say so,
 *   in plain words, instead of presenting a possibly stale number as certain.
 */

import { useId, type ReactElement } from "react";

import type { CrisisMessage, CrisisResource } from "@/lib/endpoints";

export interface CrisisCardProps {
  /** The pre-written message, when the assessment produced one. */
  message?: CrisisMessage | null;
  /** Helplines for the region, already in display order. */
  resources?: readonly CrisisResource[];
  /** The region's emergency entry, shown above everything else. */
  emergency?: CrisisResource | null;
  /** ISO date the content was last checked by a person. */
  lastVerified?: string | null;
  /** The "not therapy, not a crisis service" line. */
  disclaimer?: string | null;
  /**
   * Announce the card to assistive technology when it appears. On by default:
   * the card usually replaces a reply the person is waiting for.
   */
  announce?: boolean;
  /**
   * Heading for a card with no message. Pass `null` when the surrounding surface
   * already has one (the dialog does), so the list is not headed twice — the
   * section then carries an `aria-label` instead, and stays labelled either way.
   */
  heading?: string | null;
  testId?: string;
}

/** The tap-target minimum: 48px, per the accessibility rules in AGENTS.md. */
const TARGET_CLASSES = "inline-flex min-h-12 items-center justify-center rounded-xl px-5 py-3";

function ContactLinks({ resource }: { resource: CrisisResource }): ReactElement | null {
  const links: ReactElement[] = [];

  if (resource.tel_href !== null && resource.number !== null) {
    links.push(
      <a
        key="call"
        href={resource.tel_href}
        className={`${TARGET_CLASSES} bg-accent-bg font-semibold text-accent-fg`}
      >
        Call {resource.number}
      </a>,
    );
  }

  if (resource.sms_href !== null && resource.number !== null) {
    links.push(
      <a
        key="text"
        href={resource.sms_href}
        className={`${TARGET_CLASSES} border-2 border-border-strong font-semibold`}
      >
        Text {resource.number}
        {resource.text_keyword !== null && resource.text_keyword !== ""
          ? ` “${resource.text_keyword}”`
          : ""}
      </a>,
    );
  }

  if (resource.url !== null) {
    links.push(
      <a
        key="web"
        href={resource.url}
        target="_blank"
        rel="noreferrer noopener"
        className={`${TARGET_CLASSES} border-2 border-border-strong font-semibold text-accent`}
      >
        {resource.type === "chat" ? "Open chat" : "Visit website"}
      </a>,
    );
  }

  if (links.length === 0) {
    return null;
  }
  return <div className="mt-3 flex flex-wrap items-center gap-3">{links}</div>;
}

function ResourceEntry({ resource }: { resource: CrisisResource }): ReactElement {
  return (
    <li
      data-testid={`crisis-resource-${resource.id}`}
      className="rounded-xl border border-border bg-surface-muted p-4"
    >
      <h3 className="text-lg font-semibold">{resource.name}</h3>
      <p className="text-sm text-ink-muted">
        {resource.region} · {resource.hours}
        {resource.audience !== null && resource.audience !== "" ? ` · ${resource.audience}` : ""}
      </p>
      <p className="mt-2">{resource.description}</p>
      {resource.instructions !== null && resource.instructions !== "" ? (
        <p className="mt-2 text-sm text-ink-muted">{resource.instructions}</p>
      ) : null}
      {resource.needs_verification ? (
        <p className="mt-2 text-sm text-ink-muted">
          We could not fully confirm this entry, so please check it still applies before you rely on
          it.
        </p>
      ) : null}
      <ContactLinks resource={resource} />
    </li>
  );
}

export function CrisisCard({
  message = null,
  resources = [],
  emergency = null,
  lastVerified = null,
  disclaimer = null,
  announce = true,
  heading = "Helplines",
  testId = "crisis-card",
}: CrisisCardProps): ReactElement | null {
  const headingId = useId();
  const listId = useId();

  if (message === null && resources.length === 0 && emergency === null) {
    return null;
  }

  const others =
    emergency === null ? resources : resources.filter((item) => item.id !== emergency.id);

  const showsHeading = message !== null || heading !== null;

  return (
    <section
      data-testid={testId}
      {...(showsHeading ? { "aria-labelledby": headingId } : { "aria-label": "Helplines" })}
      {...(announce ? { role: "status", "aria-live": "polite" } : {})}
      className="rounded-2xl border-2 border-border-strong bg-surface p-4 sm:p-6"
    >
      {message !== null ? (
        <div>
          <h2 id={headingId} className="text-xl font-semibold sm:text-2xl">
            {message.title}
          </h2>
          {message.body.map((paragraph) => (
            <p key={paragraph} className="mt-3">
              {paragraph}
            </p>
          ))}

          {message.emergency_instruction !== null ? (
            <p
              data-testid="crisis-emergency-instruction"
              className="mt-4 rounded-xl bg-danger-soft p-4 font-semibold text-danger"
            >
              {message.emergency_instruction}
            </p>
          ) : null}

          {message.trusted_person !== null ? (
            <p className="mt-3">{message.trusted_person}</p>
          ) : null}

          {message.safety_steps.length > 0 ? (
            <ol className="mt-4 list-decimal space-y-2 pl-5">
              {message.safety_steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          ) : null}

          {message.closing !== null ? (
            <p className="mt-4 font-semibold">{message.closing}</p>
          ) : null}
        </div>
      ) : heading !== null ? (
        <h2 id={headingId} className="text-xl font-semibold sm:text-2xl">
          {heading}
        </h2>
      ) : null}

      {emergency !== null ? (
        <div
          data-testid="crisis-emergency"
          className="mt-5 rounded-xl border-2 border-danger bg-danger-soft p-4"
        >
          <h3 className="text-lg font-semibold text-danger">{emergency.name}</h3>
          {emergency.description !== "" ? (
            <p className="mt-1 text-sm text-ink-muted">{emergency.description}</p>
          ) : null}
          <ContactLinks resource={emergency} />
        </div>
      ) : null}

      {others.length > 0 ? (
        <div className="mt-5">
          {message?.helpline_intro !== null && message?.helpline_intro !== undefined ? (
            <p className="font-semibold">{message.helpline_intro}</p>
          ) : null}
          <ul id={listId} className="mt-3 space-y-4">
            {others.map((resource) => (
              <ResourceEntry key={resource.id} resource={resource} />
            ))}
          </ul>
        </div>
      ) : null}

      {lastVerified !== null || disclaimer !== null ? (
        <p className="mt-6 text-sm text-ink-muted">
          {lastVerified !== null ? `Last checked by a person on ${lastVerified}. ` : ""}
          {disclaimer ?? ""}
        </p>
      ) : null}
    </section>
  );
}
