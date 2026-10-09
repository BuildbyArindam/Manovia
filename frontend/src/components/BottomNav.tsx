import { NavLink } from "react-router-dom";

import type { ReactElement } from "react";

import { NAV_ITEMS } from "@/components/navigation";
import { NavIcon } from "@/components/icons";

/**
 * Mobile navigation: a fixed bar at the bottom, inside thumb reach.
 *
 * The extra bottom padding keeps the last row clear of the iOS home indicator
 * (`env(safe-area-inset-bottom)`), and every target is at least 44px tall.
 */
export function BottomNav(): ReactElement {
  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-surface pb-[max(0.5rem,env(safe-area-inset-bottom))]"
    >
      <ul className="mx-auto flex max-w-2xl items-stretch justify-between px-2 pt-1">
        {NAV_ITEMS.map((item) => (
          <li key={item.to} className="flex-1">
            <NavLink
              to={item.to}
              className={({ isActive }) =>
                [
                  "flex min-h-[3rem] flex-col items-center justify-center gap-1 rounded-xl px-1 py-1.5 text-xs font-medium",
                  isActive ? "text-accent" : "text-ink-muted",
                ].join(" ")
              }
            >
              <NavIcon name={item.icon} className="h-6 w-6" />
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
