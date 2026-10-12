/**
 * The mood wire shapes, mirroring `app/api/v1/mood.py`.
 *
 * These live here — not in `lib/endpoints.ts` — because they are one feature's
 * contract, and the feature is where they are read. The crisis types are shared
 * with the chat surface instead of being redeclared: a helpline is a helpline,
 * and two local copies of `CrisisResource` would drift the day one of them
 * changes.
 */

import type { CrisisMessage, CrisisResource } from "@/lib/endpoints";

/** `StreakState` in `app/services/mood/streak.py`. */
export type MoodStreakState = "never" | "today" | "open" | "returning";

/** One stored check-in, as its owner sees it. */
export interface MoodEntry {
  id: string;
  /** ISO timestamp (UTC). */
  created_at: string;
  /** The calendar day this belongs to in `timezone` (YYYY-MM-DD). */
  local_date: string;
  valence: number;
  energy: number;
  emotions: string[];
  factors: string[];
  /** The owner's own note, decrypted for them. */
  note: string | null;
  timezone: string;
  /** True when the entry is queued on this device waiting to sync. */
  queued_offline?: boolean;
}

/** Help offered alongside a check-in whose note read at HIGH or worse. */
export interface MoodCrisis {
  level: "high" | "imminent";
  stored_level: string;
  source: string;
  matched_categories: string[];
  rationale_codes: string[];
  about_someone_else: boolean;
  locale: string;
  region: string;
  message: CrisisMessage | null;
  emergency: CrisisResource | null;
  resources: CrisisResource[];
  /** Always true: the check-in is saved even when this is present. */
  check_in_saved: boolean;
}

/** The forgiving streak, in the caller's timezone. */
export interface MoodStreak {
  state: MoodStreakState;
  timezone: string;
  timezone_fallback_used: boolean;
  today: string;
  current_streak: number;
  longest_streak: number;
  checked_in_today: boolean;
  last_check_in: string | null;
  days_since_last_check_in: number | null;
  total_check_ins: number;
  freezes_used: number;
  freeze_available: boolean;
}

export interface MoodCheckInResponse {
  entry: MoodEntry;
  streak: MoodStreak;
  crisis: MoodCrisis | null;
  queued_offline?: boolean;
}

export interface MoodListResponse {
  entries: MoodEntry[];
  count: number;
  from: string | null;
  to: string | null;
  timezone: string;
  timezone_fallback_used: boolean;
  limit: number;
}

export interface MoodDeleteResponse {
  deleted: boolean;
  id: string;
  remaining_streak: MoodStreak;
}

/** What the check-in form sends. `tz` is filled in by `endpoints.ts`. */
export interface MoodCheckInPayload {
  valence: number;
  energy: number;
  emotions: string[];
  factors: string[];
  note?: string | null;
}
