/**
 * Wire shapes for the privacy screen.
 *
 * These mirror `app/api/v1/privacy.py` exactly. They are declared here rather
 * than inferred so a backend rename is a compile error, which matters more on
 * this screen than anywhere else: a silently-wrong type here could show
 * somebody the wrong answer about their own data.
 */

/** Mirrors `app.models.enums.ConsentKind`. */
export type PrivacyConsentKind = "terms" | "privacy" | "ai_disclosure" | "store_chat";

/** The retention windows the UI offers. `null` means "keep until I delete it". */
export type RetentionDays = 30 | 90 | 180 | 365;

export interface PrivacySettings {
  /** May a chat be saved at all? An agreement is still required on top. */
  store_chat: boolean;
  /** May Manovia keep sentiment/emotion scores derived from your writing? */
  store_sentiment: boolean;
  /** Delete personal data older than this. `null` keeps everything. */
  retention_days: number | null;
  /** Windows the UI offers (from the backend, so the two cannot drift). */
  retention_choices: number[];
  retention_min_days: number;
  retention_max_days: number;
  /** False until a choice has actually been saved; the defaults are shown then. */
  been_saved: boolean;
  updated_at: string | null;
}

export interface PrivacySettingsPatch {
  store_chat?: boolean;
  store_sentiment?: boolean;
  retention_days?: number | null;
}

export interface ConsentHistoryItem {
  kind: PrivacyConsentKind;
  version: string;
  granted: boolean;
  created_at: string;
}

export interface ConsentHistory {
  items: ConsentHistoryItem[];
  /** The version of each document that is current today. */
  current_versions: Record<string, string>;
}

export interface DeletionReceipt {
  receipt_id: string;
  /** A keyed hash, not an identifier: it cannot be turned back into a user. */
  account_ref: string;
  requested_via: string;
  rows_deleted: Record<string, number>;
  total_rows: number;
  created_at: string;
  irreversible: boolean;
}

/** Everything the export can contain, as the manifest reports it. */
export interface ExportManifest {
  format_version: string;
  generated_at: string;
  contents_are_decrypted: boolean;
  counts: Record<string, number>;
  truncated_tables: Record<string, number>;
  unreadable_rows: Record<string, number>;
}
