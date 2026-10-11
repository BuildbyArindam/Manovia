/**
 * The daily-plan wire shapes, mirroring `app/api/v1/plan.py` and
 * `app/api/v1/settings.py`.
 *
 * A plan holds at most three suggestions, each carrying the "why this?"
 * sentence the planner generated it with. The kinds are a closed set the
 * planner can emit; `support` is the only kind an assessment answer may ever
 * produce (a positive PHQ-9 item 9 shows support options and nothing else).
 */

/** What a suggestion asks for. Closed set, owned by the planner. */
export type PlanItemKind = "check_in" | "exercise" | "thought_record" | "rest" | "support";

/** Lifecycle of one suggestion. */
export type PlanItemStatus = "pending" | "done" | "skipped";

/** One suggestion, as its owner sees it. */
export interface PlanItem {
  id: string;
  kind: PlanItemKind;
  /** Exercise id for exercise suggestions; `null` for the other kinds. */
  ref: string | null;
  title: string;
  /** The "why this?" explanation. Always present. */
  why: string;
  status: PlanItemStatus;
  created_at: string;
}

/** Today's plan: at most three items, never a nag. */
export interface PlanTodayResponse {
  /** The local calendar day this plan belongs to (YYYY-MM-DD). */
  date: string;
  timezone: string;
  timezone_default_used: boolean;
  /** True while quiet hours cover this request: the plan is empty because of it. */
  quiet_hours_active: boolean;
  /** True when only the support-options suggestion shows after item 9. */
  support_only: boolean;
  items: PlanItem[];
  count: number;
}

/** Planner preferences, as stored by `PUT /settings`. */
export interface PlanSettings {
  preferred_exercise_tags: string[];
  quiet_hours_enabled: boolean;
  /** 24-hour `HH:MM`. */
  quiet_start: string;
  /** 24-hour `HH:MM`. */
  quiet_end: string;
  /** The exercise catalogue's tags — the only valid preference choices. */
  known_exercise_tags: string[];
}

/** The body `PUT /settings` accepts. */
export interface PlanSettingsPayload {
  preferred_exercise_tags: string[];
  quiet_hours_enabled: boolean;
  quiet_start: string;
  quiet_end: string;
}
