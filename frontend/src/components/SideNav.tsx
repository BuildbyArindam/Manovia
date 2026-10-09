import { NavLink } from "react-router-dom";

import type { ReactElement } from "react";

import { NAV_ITEMS } from "@/components/navigation";
import { NavIcon } from "@/components/icons";

/** Desktop navigation: a vertical rail that stays with the page. */
export function SideNav(): ReactElement {
  return (
    <nav
      aria-label="Primary"
      className="sticky top-20 hidden h-fit w-56 shrink-0 self-start md:block"
    >
      <ul className="space-y-1">
        {NAV_ITEMS.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
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
                {item.label}
                <span className="block text-sm font-normal text-ink-muted">{item.hint}</span>
              </span>
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
