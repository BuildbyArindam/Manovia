import { NavLink } from "react-router-dom";

import type { ReactElement } from "react";

import { NAV_ITEMS } from "@/components/navigation";
import { NavIcon } from "@/components/icons";
import { t } from "@/lib/i18n/i18n";

/**
 * Desktop navigation: a vertical rail that stays with the page.
 *
 * Day 25: with the reader's text size at 200% the rail is taller than the
 * window, and a sticky element taller than the window can never scroll its
 * lower links into view — Tab would land on links nobody can see. The rail now
 * scrolls inside itself when it does not fit, and on short windows (high
 * zoom) it stops being sticky altogether (`.app-side-nav` in index.css).
 */
export function SideNav(): ReactElement {
  return (
    <nav
      aria-label={t("nav.primary")}
      data-print="hide"
      className="app-side-nav sticky top-20 hidden h-fit max-h-[calc(100vh-6rem)] w-56 shrink-0 self-start overflow-y-auto md:block"
    >
      <ul className="space-y-1">
        {NAV_ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  "flex items-center gap-3 rounded-xl px-3 py-2 text-base font-medium",
                  isActive
                    ? "bg-accent-soft font-semibold text-ink"
                    : "text-ink-muted hover:bg-surface-muted",
                ].join(" ")
              }
            >
              <NavIcon name={item.icon} className="h-5 w-5" />
              <span>
                {item.label()}
                <span className="block text-sm font-normal text-ink-muted">{item.hint()}</span>
              </span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
