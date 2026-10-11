/**
 * Typed endpoint wrappers for `/api/v1/journal`.
 */

import type { MoodCrisis } from "@/features/mood/types";
import type { ApiClient } from "@/lib/api";
import { api as defaultClient } from "@/lib/api";
import type { CrisisResource } from "@/lib/endpoints";

export type JournalTemplateId = "free" | "gratitude" | "worry_dump";

export interface JournalWriteRequest {
  title?: string;
  body: string;
  template?: JournalTemplateId | null;
  prompt_id?: string | null;
  store_emotions?: boolean;
}

export interface JournalEntryMetadata {
  word_count: number;
  store_emotions: boolean;
  primary_emotion: string | null;
  risk_level: string;
  stored_risk_level: number;
  resources: CrisisResource[];
  crisis: MoodCrisis | null;
}

export interface JournalEntry {
  id: string;
  title: string;
  body: string;
  key_id: string;
  sentiment: number | null;
  emotion_scores: Record<string, number> | null;
  template: JournalTemplateId | string | null;
  prompt_id: string | null;
  word_count: number;
  store_emotions: boolean;
  created_at: string;
  updated_at: string;
  metadata: JournalEntryMetadata;
  crisis: MoodCrisis | null;
}

export interface JournalListResponse {
  items: JournalEntry[];
  count: number;
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

export interface JournalPrompt {
  id: string;
  category: string;
  template: JournalTemplateId;
  text: string;
  hint: string;
}

export interface JournalTemplate {
  id: JournalTemplateId;
  label: string;
  description: string;
  scaffold_title: string;
  scaffold_body: string;
}

export interface JournalPromptsResponse {
  locale: string;
  for_date: string;
  featured: JournalPrompt;
  prompts: JournalPrompt[];
  templates: JournalTemplate[];
}

export interface JournalListParams {
  limit?: number;
  offset?: number;
  since?: string;
  until?: string;
  template?: JournalTemplateId;
}

export interface JournalPromptsParams {
  lang?: string;
  template?: JournalTemplateId;
  for_date?: string;
  count?: number;
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const entries = Object.entries(params).filter(
    (pair): pair is [string, string | number] => pair[1] !== undefined && pair[1] !== "",
  );
  if (entries.length === 0) {
    return "";
  }
  const search = new URLSearchParams(entries.map(([k, v]) => [k, String(v)]));
  return `?${search.toString()}`;
}

export async function fetchJournalPrompts(
  params: JournalPromptsParams = {},
  client: ApiClient = defaultClient,
): Promise<JournalPromptsResponse> {
  const query = buildQuery({
    lang: params.lang,
    template: params.template,
    for_date: params.for_date,
    count: params.count,
  });
  return client.get<JournalPromptsResponse>(`/journal/prompts${query}`);
}

export async function listJournalEntries(
  params: JournalListParams = {},
  client: ApiClient = defaultClient,
): Promise<JournalListResponse> {
  const query = buildQuery({
    limit: params.limit,
    offset: params.offset,
    since: params.since,
    until: params.until,
    template: params.template,
  });
  return client.get<JournalListResponse>(`/journal${query}`);
}

export async function getJournalEntry(
  id: string,
  client: ApiClient = defaultClient,
): Promise<JournalEntry> {
  return client.get<JournalEntry>(`/journal/${encodeURIComponent(id)}`);
}

export async function createJournalEntry(
  payload: JournalWriteRequest,
  client: ApiClient = defaultClient,
): Promise<JournalEntry> {
  return client.post<JournalEntry>("/journal", payload);
}

export async function updateJournalEntry(
  id: string,
  payload: JournalWriteRequest,
  client: ApiClient = defaultClient,
): Promise<JournalEntry> {
  return client.request<JournalEntry>(`/journal/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: payload,
  });
}

export async function deleteJournalEntry(
  id: string,
  client: ApiClient = defaultClient,
): Promise<void> {
  await client.request<undefined>(`/journal/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
