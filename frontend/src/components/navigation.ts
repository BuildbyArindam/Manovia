import type { IconName } from "@/components/icons";

/** The app's surfaces, in the order they appear in both navigations. */
export interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  /** One line of what this surface is for, used by the desktop navigation. */
  hint: string;
  /** Exact match only. Required for `/`, which is a prefix of every route. */
  end?: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/", label: "Home", icon: "home", hint: "Today's plan", end: true },
  { to: "/chat", label: "Chat", icon: "chat", hint: "Talk it through" },
  { to: "/mood", label: "Mood", icon: "mood", hint: "Check in" },
  { to: "/journal", label: "Journal", icon: "journal", hint: "Write freely" },
  { to: "/cbt", label: "Thought record", icon: "journal", hint: "Reflect on a thought" },
  { to: "/exercises", label: "Exercises", icon: "exercises", hint: "Short practices" },
  { to: "/assessments", label: "Screenings", icon: "assessments", hint: "PHQ-9 and GAD-7" },
  { to: "/insights", label: "Insights", icon: "insights", hint: "Patterns over time" },
  { to: "/settings", label: "Settings", icon: "settings", hint: "Your choices" },
];
