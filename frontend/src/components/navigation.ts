import type { IconName } from "@/components/icons";
import { t } from "@/lib/i18n/i18n";

/** The app's surfaces, in the order they appear in both navigations. */
export interface NavItem {
  to: string;
  /** Returns the label at call time, so a language switch renames the nav. */
  label(): string;
  icon: IconName;
  /** One line of what this surface is for, used by the desktop navigation. */
  hint(): string;
  /** Exact match only. Required for `/`, which is a prefix of every route. */
  end?: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    to: "/",
    label: () => t("nav.home"),
    icon: "home",
    hint: () => t("nav.homeHint"),
    end: true,
  },
  { to: "/chat", label: () => t("nav.chat"), icon: "chat", hint: () => t("nav.chatHint") },
  { to: "/mood", label: () => t("nav.mood"), icon: "mood", hint: () => t("nav.moodHint") },
  {
    to: "/journal",
    label: () => t("nav.journal"),
    icon: "journal",
    hint: () => t("nav.journalHint"),
  },
  {
    to: "/cbt",
    label: () => t("nav.cbt"),
    icon: "journal",
    hint: () => t("nav.cbtHint"),
  },
  {
    to: "/exercises",
    label: () => t("nav.exercises"),
    icon: "exercises",
    hint: () => t("nav.exercisesHint"),
  },
  {
    to: "/biofeedback",
    label: () => t("nav.biofeedback"),
    icon: "biofeedback",
    hint: () => t("nav.biofeedbackHint"),
  },
  {
    to: "/assessments",
    label: () => t("nav.assessments"),
    icon: "assessments",
    hint: () => t("nav.assessmentsHint"),
  },
  {
    to: "/insights",
    label: () => t("nav.insights"),
    icon: "insights",
    hint: () => t("nav.insightsHint"),
  },
  {
    to: "/settings",
    label: () => t("nav.settings"),
    icon: "settings",
    hint: () => t("nav.settingsHint"),
  },
];

/**
 * The document title for a route (Day 25, WCAG 2.4.2): the navigation label
 * for the app's own surfaces, plus the two pages that are not in the
 * navigation. Read at call time so a language switch retitles the tab.
 */
export function routeTitle(pathname: string): string {
  if (pathname === "/accessibility") {
    return t("a11y.title");
  }
  const match = NAV_ITEMS.find((item) =>
    item.end === true
      ? pathname === item.to
      : pathname === item.to || pathname.startsWith(`${item.to}/`),
  );
  return match?.label() ?? t("notFound.heading");
}
