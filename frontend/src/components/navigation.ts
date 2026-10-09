import type { IconName } from "@/components/icons";

/** The six surfaces of the app, in the order they appear in both navigations. */
export interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  /** One line of what this surface is for, used by the desktop navigation. */
  hint: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/chat", label: "Chat", icon: "chat", hint: "Talk it through" },
  { to: "/mood", label: "Mood", icon: "mood", hint: "Check in" },
  { to: "/journal", label: "Journal", icon: "journal", hint: "Write freely" },
  { to: "/exercises", label: "Exercises", icon: "exercises", hint: "Short practices" },
  { to: "/insights", label: "Insights", icon: "insights", hint: "Patterns over time" },
  { to: "/settings", label: "Settings", icon: "settings", hint: "Your choices" },
];
