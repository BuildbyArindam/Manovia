/**
 * The wire shapes of the screening API.
 *
 * Mirrors `app/api/v1/assessments.py` field for field. Two of them are worth
 * naming: `provenance` is passed through as an opaque record because the UI only
 * ever needs to *show* it (the source and licence statement), never to branch on
 * it; and `crisis` is the same shape the chat, mood and CBT surfaces return, so
 * the crisis card renders one contract rather than four.
 */

import type { CrisisMessage, CrisisResource } from "@/lib/endpoints";

/** Mirrors `app.models.enums.AssessmentInstrument`. */
export type AssessmentInstrument = "phq9" | "gad7";

/** Mirrors `app.models.enums.AssessmentBand`. GAD-7 never uses `moderately_severe`. */
export type AssessmentBand = "minimal" | "mild" | "moderate" | "moderately_severe" | "severe";

export interface InstrumentSummary {
  instrument: AssessmentInstrument;
  name: string;
  short_name: string;
  measures: string;
  intro: string;
  item_count: number;
  timeframe_label: string;
  total_max: number;
}

export interface ResponseOption {
  value: number;
  label: string;
  detail?: string | null;
}

export interface AssessmentItem {
  id: string;
  position: number;
  text: string;
}

export interface BandSummary {
  id: AssessmentBand;
  label: string;
  min: number;
  max: number;
}

export interface SafetyCopy {
  item_id: string;
  rule: string;
  level: string;
  explanation: string;
  encouragement: string;
  paired_with_score: string;
}

export interface AssessmentDefinition {
  instrument: AssessmentInstrument;
  name: string;
  short_name: string;
  measures: string;
  intro: string;
  timeframe_days: number;
  timeframe_label: string;
  response_options: ResponseOption[];
  items: AssessmentItem[];
  bands: BandSummary[];
  total_min: number;
  total_max: number;
  screening_not_diagnosis: string;
  how_to_read: string;
  safety: SafetyCopy | null;
  cadence_notice: string;
  recommended_interval_days: number;
  provenance: Record<string, unknown>;
}

export interface AssessmentAnswer {
  item_id: string;
  value: number;
}

export interface AssessmentResultView {
  id: string;
  instrument: AssessmentInstrument;
  created_at: string;
  answers: AssessmentAnswer[];
  total: number;
  max_total: number;
  band: AssessmentBand;
  band_label: string;
  band_min: number;
  band_max: number;
  band_explanation: string;
  what_helps: string[];
  when_to_seek_help: string;
  screening_not_diagnosis: string;
  how_to_read: string;
}

export interface HistoryEntry {
  id: string;
  created_at: string;
  total: number;
  max_total: number;
  band: AssessmentBand;
  band_label: string;
}

export interface CadenceView {
  recommended_interval_days: number;
  is_first: boolean;
  days_since_previous: number | null;
  due: boolean;
  notice: string;
}

export interface AssessmentCrisis {
  item_id: string;
  level: string;
  stored_level: string;
  /** `self_report` — nothing detected this; the person answered item 9. */
  source: string;
  matched_categories: string[];
  rationale_codes: string[];
  locale: string;
  region: string;
  explanation: string;
  encouragement: string;
  paired_with_score: string;
  message: CrisisMessage | null;
  emergency: CrisisResource | null;
  resources: CrisisResource[];
  result_saved: boolean;
}

export interface SubmitResponse {
  result: AssessmentResultView;
  crisis: AssessmentCrisis | null;
  cadence: CadenceView;
}

export interface HistoryResponse {
  instrument: AssessmentInstrument;
  short_name: string;
  total_max: number;
  bands: BandSummary[];
  entries: HistoryEntry[];
  count: number;
  limit: number;
  offset: number;
  cadence: CadenceView;
}
