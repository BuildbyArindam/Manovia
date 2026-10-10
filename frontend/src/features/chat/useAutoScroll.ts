/**
 * Auto-scroll that does not hijack reading.
 *
 * The rule: **follow the reply only while the person is already at the bottom.**
 * Scroll up to re-read something and the view stops moving, no matter how many
 * tokens arrive; a "Jump to latest" button appears so coming back is one press.
 * A chat that yanks the viewport while someone reads is unusable, and for a
 * safety product that is not a nicety.
 *
 * Jumping to the latest sets `scrollTop` rather than calling `scrollTo({behavior:
 * "smooth"})`: the smoothness belongs in CSS (`scroll-behavior` in index.css),
 * which already collapses itself to `auto` under `prefers-reduced-motion`. One
 * mechanism instead of two, and it works everywhere a scroll container does.
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** How close to the bottom still counts as "following". */
export const PIN_THRESHOLD_PX = 64;

export interface AutoScroll {
  containerRef: React.RefObject<HTMLElement>;
  /** True while the view is following the conversation. */
  pinned: boolean;
  scrollToLatest: () => void;
}

export function useAutoScroll(follow: unknown): AutoScroll {
  const containerRef = useRef<HTMLElement>(null);
  const [pinned, setPinned] = useState(true);

  const isAtBottom = useCallback((): boolean => {
    const node = containerRef.current;
    if (node === null) {
      return true;
    }
    // jsdom reports no layout: treat "no measurable height" as pinned so the
    // behaviour under test is the real one, not an accident of the environment.
    if (node.scrollHeight <= node.clientHeight) {
      return true;
    }
    return node.scrollHeight - node.scrollTop - node.clientHeight <= PIN_THRESHOLD_PX;
  }, []);

  // Track the reader's own scrolling. A programmatic scroll also lands here,
  // and it lands at the bottom, so it keeps `pinned` true rather than fighting.
  useEffect(() => {
    const node = containerRef.current;
    if (node === null) {
      return;
    }
    const onScroll = (): void => {
      setPinned(isAtBottom());
    };
    node.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      node.removeEventListener("scroll", onScroll);
    };
  }, [isAtBottom]);

  const scrollToLatest = useCallback((): void => {
    const node = containerRef.current;
    if (node === null) {
      return;
    }
    node.scrollTop = node.scrollHeight;
    setPinned(true);
  }, []);

  // Move only when following. `follow` is whatever the caller considers "new
  // content" — in the chat view, the last turn's text.
  useEffect(() => {
    if (!pinned) {
      return;
    }
    const node = containerRef.current;
    if (node === null) {
      return;
    }
    node.scrollTop = node.scrollHeight;
  }, [follow, pinned]);

  return { containerRef, pinned, scrollToLatest };
}
