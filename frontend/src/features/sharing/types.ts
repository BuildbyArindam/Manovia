/**
 * Wire shapes for the clinician-share bridge (Day 47).
 *
 * These mirror `app/api/v1/sharing.py` and `app/api/v1/share_view.py` exactly,
 * and the document shapes mirror `app/services/sharing/items.py`. They are
 * declared rather than inferred for the same reason the privacy screen declares
 * its own: a backend rename should be a compile error here, because a
 * silently-wrong type on this screen would show somebody the wrong answer about
 * what they are about to hand to a professional.
 *
 * One deliberate absence: there is no `key`, `secret`, `token` or `passcode`
 * field on any *stored* shape. Those exist only on {@link CreatedShare}, the one
 * response that carries them, and only until the tab closes.
 */

/** Mirrors `app.models.enums.ShareItem`, in the order the UI lists them. */
export type ShareItem =
  | "mood_trend"
  | "assessments"
  | "thought_records"
  | "exercise_helpfulness"
  | "note"
  | "questions"
  | "identity";

/** Mirrors `app.models.enums.ShareFormat`. */
export type ShareFormat = "pdf" | "fhir";

/** Mirrors `app.models.enums.ShareEventKind`. */
export type ShareEventKind =
  | "created"
  | "opened"
  | "passcode_failed"
  | "locked"
  | "revoked"
  | "expired"
  | "view_limit_reached"
  | "access_after_revoke";

/** Who did it: the owner, an anonymous viewer, or the retention/expiry job. */
export type ShareActor = "owner" | "viewer" | "system";

/** Mirrors the `direction` field the mood summary carries. */
export type MoodDirection = "higher" | "lower" | "steady" | "not_enough_data";

export interface PreferenceOut {
  /** The person's own switch. Off until they turn it on. */
  enabled: boolean;
  /** Whether this deployment runs the feature at all (`SHARE_ENABLED`). */
  deployment_enabled: boolean;
  updated_at: string | null;
}

/** One selectable thought record: metadata only, never its text. */
export interface ThoughtRecordChoice {
  id: string;
  created_at: string;
  emotion: string | null;
  intensity_before: number | null;
  intensity_after: number | null;
}

export interface CatalogueItem {
  key: ShareItem;
  label: string;
  blurb: string;
  /** What ticking this puts in the document. */
  includes: string[];
  /** What ticking this still leaves out. */
  excludes: string[];
  default: boolean;
  /** True when the person must also choose *which* rows. */
  needs_selection: boolean;
}

/** `OptionsOut`: everything the builder needs before anybody decides anything. */
export interface ShareOptions {
  enabled: boolean;
  deployment_enabled: boolean;
  defaults: {
    enabled: boolean;
    default_ttl_days: number;
    min_ttl_days: number;
    max_ttl_days: number;
    default_view_limit: number;
    max_view_limit: number;
    max_active_shares: number;
    passcode_min_length: number;
    passcode_max_failures: number;
    formats: ShareFormat[];
  };
  items: CatalogueItem[];
  thought_records: ThoughtRecordChoice[];
  never_included: string[];
  disclaimer_title: string;
  disclaimer: string;
  limits: {
    note_chars: number;
    questions: number;
    question_chars: number;
    thought_records: number;
    windows: number[];
  };
}

/**
 * The selection alone, which is what `POST /shares/preview` accepts (it mirrors
 * `ShareRequest`). A note, a question list or a record list may only be present
 * when the matching item is ticked, and must then be non-empty: the server
 * refuses a selection that asks for more than it ticked.
 */
export interface ShareSelectionBody {
  items: ShareItem[];
  window_days: number;
  note?: string | null;
  questions?: string[] | null;
  thought_record_ids?: string[] | null;
}

/** `POST /shares`: the selection plus the link's own settings. */
export interface CreateShareBody extends ShareSelectionBody {
  formats: ShareFormat[];
  expires_in_days?: number | null;
  view_limit?: number | null;
  /** Omit or leave empty to let the server generate a six-digit passcode. */
  passcode?: string | null;
  /** The hash from `POST /shares/preview`, echoed back. */
  consent_sha256: string;
}

/** What `POST /shares/preview` returns: the document and the sentence about it. */
export interface PreviewOut {
  payload: SharePayload;
  consent_lines: string[];
  consent_sha256: string;
  payload_sha256: string;
}

/** The only response that ever carries the key or the passcode. */
export interface CreatedShare {
  share_id: string;
  /** The path the viewer opens, e.g. `/s/<token>`. */
  share_path: string;
  token: string;
  /** The 256-bit secret. Shown once; never stored, never fetched again. */
  secret: string;
  /** The URL fragment to append: `#k=<secret>`. */
  fragment: string;
  passcode: string;
  /** True when Manovia generated the passcode rather than taking a typed one. */
  passcode_generated: boolean;
  expires_at: string;
  view_limit: number;
  formats: string[];
  items: string[];
  consent_sha256: string;
  payload_sha256: string;
  document_bytes: Record<string, number>;
  consent_lines: string[];
  warning: string;
}

/** One link as the owner sees it. */
export interface ShareSummary {
  id: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  /** `active` | `expired` | `revoked` | `exhausted` — the server decides. */
  status: string;
  token_prefix: string;
  formats: string[];
  items: string[];
  view_count: number;
  view_limit: number;
  views_remaining: number;
  opened_count: number;
  passcode_failures: number;
  locked_until: string | null;
  consent_sha256: string;
  /** "Opened 2 times of 5 allowed." — the sentence the brief asks for. */
  summary: string;
}

export interface AuditEvent {
  kind: ShareEventKind;
  actor: ShareActor;
  created_at: string;
}

export interface AuditOut {
  share: ShareSummary;
  events: AuditEvent[];
  counts: Partial<Record<ShareEventKind, number>>;
  summary: string;
}

export interface RevokeOut {
  id: string;
  revoked_at: string;
  status: string;
}

export interface RevokeAllOut {
  revoked: number;
  ids: string[];
}

/** `PublicStatusOut`: what an anonymous viewer may see before typing anything. */
export interface PublicStatusOut {
  available: boolean;
  requires_passcode: boolean;
  token_prefix: string;
  formats: string[];
  disclaimer_title: string;
  disclaimer: string;
}

/** One encrypted document, as the server hands it over. */
export interface EncryptedDocumentOut {
  /** `payload` | `pdf` | `fhir`. */
  name: string;
  /** Unpadded base64url. */
  ciphertext: string;
  /** Unpadded base64url, 12 bytes. */
  nonce: string;
  /** Always `A256GCM`. */
  algorithm: string;
  content_type: string;
  /**
   * The Associated Data the ciphertext was authenticated with. Sent rather than
   * rebuilt in the browser: two copies of a format string are two chances to
   * disagree, and disagreement here looks like "wrong passcode".
   */
  aad: string;
}

/** `OpenOut`: the ciphertext bundle the viewer decrypts locally. */
export interface OpenOut {
  share_id: string;
  token_prefix: string;
  generated_at: string;
  expires_at: string;
  views_remaining: number;
  formats: string[];
  items: string[];
  algorithm: string;
  /** How to turn the URL key and the passcode into the content key. */
  key_derivation: string;
  disclaimer_title: string;
  disclaimer: string;
  documents: EncryptedDocumentOut[];
}

// --------------------------------------------------------------------------- //
// the decrypted document (mirrors app/services/sharing/items.py)
// --------------------------------------------------------------------------- //

export interface LabelCount {
  label: string;
  count: number;
}

export interface MoodTrendSummary {
  window_days: number;
  window_start: string;
  entries: number;
  first_check_in: string | null;
  last_check_in: string | null;
  mean_valence: number | null;
  mean_energy: number | null;
  earlier_mean_valence: number | null;
  recent_mean_valence: number | null;
  direction: MoodDirection;
  direction_note: string;
  top_emotions: LabelCount[];
  top_factors: LabelCount[];
  /** Always false: check-in notes are never in a share. */
  notes_included: boolean;
}

export interface AssessmentShare {
  instrument: "phq9" | "gad7";
  instrument_name: string;
  completed_at: string;
  total: number;
  band: string;
  band_label: string;
  loinc_code: string;
  loinc_display: string;
  loinc_source_url: string;
  ucum_unit: string;
  /** Always false: the individual answers are never in a share. */
  item_answers_included: boolean;
  /** Always false: a band label is a self-help description, not a diagnosis. */
  band_is_a_diagnosis: boolean;
}

export interface ThoughtRecordShare {
  id: string;
  created_at: string;
  situation: string;
  automatic_thought: string;
  evidence_for: string;
  evidence_against: string;
  balanced_thought: string;
  emotion: string | null;
  intensity_before: number | null;
  intensity_after: number | null;
  what_helped: string[];
}

export interface ExerciseHelpfulnessRow {
  title: string;
  times: number;
  mean_before: number | null;
  mean_after: number | null;
  mean_change: number | null;
}

export interface ExerciseHelpfulness {
  window_days: number;
  window_start: string;
  completions: number;
  rated_completions: number;
  exercises: ExerciseHelpfulnessRow[];
  /** Only ever an exercise whose average rating improved. */
  most_helpful: string | null;
  rating_scale: string;
  change_caveat: string;
}

/** The decrypted `payload` document. */
export interface SharePayload {
  kind: "manovia.appointment-summary";
  schema_version: number;
  generated_at: string;
  app: string;
  app_version: string;
  disclaimer_title: string;
  disclaimer: string;
  never_included: string[];
  window_days: number;
  items: ShareItem[];
  identity: string | null;
  mood_trend: MoodTrendSummary | null;
  assessments: AssessmentShare[] | null;
  thought_records: ThoughtRecordShare[] | null;
  exercise_helpfulness: ExerciseHelpfulness | null;
  note: string | null;
  questions: string[] | null;
  counts: Record<string, number>;
}

/**
 * The five thought-record sections, in the order the app walks them, with the
 * i18n key that names each one. The keys are the CBT screen's own, so a person
 * reading a shared record sees the same headings they wrote under.
 */
export const RECORD_SECTIONS = [
  ["situation", "cbt.steps.situation"],
  ["automatic_thought", "cbt.steps.automaticThought"],
  ["evidence_for", "cbt.steps.evidenceFor"],
  ["evidence_against", "cbt.steps.evidenceAgainst"],
  ["balanced_thought", "cbt.steps.balanced"],
] as const;
