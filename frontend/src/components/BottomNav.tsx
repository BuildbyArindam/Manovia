import { NavLink } from "react-router-dom";

import type { ReactElement } from "react";

import { NAV_ITEMS } from "@/components/navigation";
import { NavIcon } from "@/components/icons";
import { t } from "@/lib/i18n/i18n";

/**
 * Mobile navigation: a fixed bar at the bottom, inside thumb reach.
 *
 * The extra bottom padding keeps the last row clear of the iOS home indicator
 * (`env(safe-area-inset-bottom)`), and every target is at least 44px tall.
 */
export function BottomNav(): ReactElement {
  return (
    <nav
      aria-label={t("nav.primary")}
      data-print="hide"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface pb-[max(0.5rem,env(safe-area-inset-bottom))]"
    >
      <ul className="mx-auto flex max-w-2xl flex-wrap items-stretch justify-between px-1 pt-1">
        {NAV_ITEMS.map((item) => (
          <li key={item.to} className="min-w-[3.75rem] flex-1">
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                [
                  "flex min-h-[2.75rem] flex-col items-center justify-center gap-0.5 rounded-xl px-1 py-1 text-center text-[11px] font-medium leading-tight",
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
