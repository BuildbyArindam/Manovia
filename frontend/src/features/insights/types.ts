/**
 * The insights wire shapes, mirroring `app/api/v1/insights.py`.
 */

export type InsightsTrend = "up" | "down" | "flat" | "not_enough_data";

export interface RollingPoint {
  day: string; // YYYY-MM-DD
  valence_avg: number | null;
  energy_avg: number | null;
  count: number;
}

export interface RollingWindow {
  window_days: number;
  points: RollingPoint[];
  trend: InsightsTrend;
  trend_note: string;
}

export interface FactorAssociation {
  factor: string;
  with_factor_avg: number | null;
  without_factor_avg: number | null;
  with_factor_count: number;
  without_factor_count: number;
  effect_size: number | null;
  ci_lower: number | null;
  ci_upper: number | null;
  p_value: number | null;
  note: string;
}

export interface TimePattern {
  bucket: string;
  valence_avg: number | null;
  energy_avg: number | null;
  count: number;
  note: string;
}

export interface WeeklySummary {
  locale: string;
  period_start: string; // YYYY-MM-DD
  period_end: string; // YYYY-MM-DD
  total_check_ins: number;
  valence_avg: number | null;
  energy_avg: number | null;
  trend: InsightsTrend;
  top_factors: [string, number][];
  text: string;
}

export interface InsightsPatterns {
  rolling_7: RollingWindow;
  rolling_30: RollingWindow;
  factor_associations: FactorAssociation[];
  hourly_patterns: TimePattern[];
  weekday_patterns: TimePattern[];
  timezone: string;
  timezone_fallback_used: boolean;
  min_entries_for_trend: number;
  min_entries_per_group: number;
}

export interface ExportBundle {
  entries_csv: string;
  factor_associations_csv: string;
  time_patterns_csv: string;
}