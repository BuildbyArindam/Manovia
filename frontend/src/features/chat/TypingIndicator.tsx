/**
 * The typing indicator: three dots that breathe.
 *
 * "Calm" is a real constraint here, not a taste. A fast blink reads as urgency
 * and a bouncing loader reads as a machine being busy; both are wrong for
 * somebody who is already anxious. So: slow (1.6s), low amplitude, muted
 * colour, and gone entirely when the person has asked for reduced motion.
 *
 * It is decorative for assistive technology on purpose — the announcement that
 * a reply is on its way belongs to the message's live region, and two regions
 * saying the same thing is noise, not accessibility.
 */

import type { ReactElement } from "react";

import { usePrefersReducedMotion } from "@/lib/usePrefersReducedMotion";

export function TypingIndicator(): ReactElement {
  const reduceMotion = usePrefersReducedMotion();

  return (
    <span aria-hidden="true" data-testid="typing-indicator" className="inline-flex gap-1.5 py-1">
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          className={
            reduceMotion
              ? "h-2 w-2 rounded-full bg-ink-muted"
              : "manovia-dot h-2 w-2 rounded-full bg-ink-muted"
          }
          style={reduceMotion ? undefined : { animationDelay: `${index * 0.2}s` }}
        />
      ))}
    </span>
  );
}
