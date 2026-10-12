import { useEffect, useRef, type ReactElement } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { NAV_ITEMS } from "@/components/navigation";
import { NavIcon } from "@/components/icons";
import { t } from "@/lib/i18n/i18n";

/**
 * Mobile navigation: a fixed bar at the bottom, inside thumb reach.
 *
 * The extra bottom padding keeps the last row clear of the iOS home indicator
 * (`env(safe-area-inset-bottom)`), and every target is at least 44px tall.
 *
 * Day 25: nine destinations do not fit a 360px screen at 44px each (they were
 * squeezed to 36–43px wide), so the bar scrolls sideways instead of shrinking
 * its targets. Every item is at least 4.5rem wide, the next item peeks in at
 * the edge so the reader can see there is more, and the current page is
 * scrolled into view *inside the bar* on every navigation (see
 * {@link revealInStrip}). Each link is focusable, so the scrolling region is
 * reachable from the keyboard.
 */

/**
 * Scroll `strip` sideways just enough to show `item` — and touch nothing else.
 *
 * `item.scrollIntoView({ inline: "nearest" })` would do this in one line, but
 * it scrolls *every* scrollable ancestor, the page included. While the bar is
 * fixed to the bottom of the screen that is harmless. On short screens (200%
 * zoom, a phone held sideways) the bar scrolls with the page instead
 * (`index.css`, "Short viewports"), and `scrollIntoView` then dragged every
 * freshly opened page down to the bottom navigation: the reader landed at the
 * end of the page, and their first Tab went to the bar instead of "Skip to
 * main content" (found in Day 25 verification at 640x400).
 */
export function revealInStrip(strip: HTMLElement, item: HTMLElement): void {
  const stripBox = strip.getBoundingClientRect();
  const itemBox = item.getBoundingClientRect();
  if (itemBox.left < stripBox.left) {
    strip.scrollLeft -= stripBox.left - itemBox.left;
  } else if (itemBox.right > stripBox.right) {
    strip.scrollLeft += itemBox.right - stripBox.right;
  }
}

export function BottomNav(): ReactElement {
  const listRef = useRef<HTMLUListElement | null>(null);
  const { pathname } = useLocation();

  useEffect(() => {
    const list = listRef.current;
    const current = list?.querySelector<HTMLElement>('[aria-current="page"]');
    if (list !== null && current !== null && current !== undefined) {
      revealInStrip(list, current.closest("li") ?? current);
    }
  }, [pathname]);

  return (
    <nav
      aria-label={t("nav.primary")}
      data-print="hide"
      className="app-bottom-nav fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface pb-[max(0.5rem,env(safe-area-inset-bottom))]"
    >
      <ul
        ref={listRef}
        className="mx-auto flex max-w-2xl snap-x items-stretch gap-1 overflow-x-auto px-2 pt-1"
      >
        {NAV_ITEMS.map((item) => (
          <li key={item.to} className="min-w-[4.5rem] flex-1 snap-start">
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  "flex min-h-[3rem] flex-col items-center justify-center gap-1 whitespace-nowrap rounded-xl px-1 py-1.5 text-xs font-medium",
                  isActive ? "text-accent" : "text-ink-muted",
                ].join(" ")
              }
            >
              <NavIcon name={item.icon} className="h-6 w-6" />
              {item.label()}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
