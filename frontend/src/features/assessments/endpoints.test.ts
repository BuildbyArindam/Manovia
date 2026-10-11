/**
 * The wire calls: what actually goes to the server.
 *
 * Two of these matter beyond shape. The definition is fetched with
 * `authenticated: false` because it is published material and must load even if
 * the token has expired; and the submit body carries only the answers, so a
 * client cannot smuggle a total or a band that the server would have to trust.
 */

import { ApiClient, MemoryTokenStore, type FetchImpl } from "@/lib/api";
import { beforeEach, describe, expect, it } from "vitest";

import { assessmentDefinition, historyResponse, resultView } from "@/test/assessmentFakes";

import {
  ASSESSMENT_ENDPOINTS,
  fetchAssessmentDefinition,
  fetchAssessmentHistory,
  fetchInstruments,
  fetchAssessmentResult,
  submitAssessment,
} from "./endpoints";

function clientWith(fetchImpl: FetchImpl): ApiClient {
  const client = new ApiClient({ fetchImpl, tokens: new MemoryTokenStore() });
  client.applySession({ access_token: "access-1", refresh_token: "refresh-1", expires_in: 900 });
  return client;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let calls: Array<{ url: string; method: string; body: unknown; authorization: string | null }> = [];

beforeEach(() => {
  calls = [];
});

function recordingFetch(payload: unknown, status = 200): FetchImpl {
  return async (url, init) => {
    const headers = new Headers(init?.headers ?? {});
    calls.push({
      url,
      method: init?.method ?? "GET",
      body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      authorization: headers.get("Authorization"),
    });
    return jsonResponse(payload, status);
  };
}

describe("assessment endpoints", () => {
  it("lists the instruments without sending a token", async () => {
    const client = clientWith(recordingFetch([]));
    await fetchInstruments(client);
    expect(calls[0]?.url).toBe("/api/v1/assessments");
    expect(calls[0]?.authorization).toBeNull();
  });

  it("fetches a definition without sending a token", async () => {
    const client = clientWith(recordingFetch(assessmentDefinition()));
    const definition = await fetchAssessmentDefinition("phq9", client);
    expect(definition.short_name).toBe("PHQ-9");
    expect(calls[0]?.url).toBe("/api/v1/assessments/phq9");
    expect(calls[0]?.authorization).toBeNull();
  });

  it("submits only the answers — no total, no band", async () => {
    const client = clientWith(recordingFetch({ result: resultView(), crisis: null }));
    await submitAssessment("phq9", { phq9_1: 0, phq9_9: 1 }, { client });
    expect(calls[0]?.url).toBe("/api/v1/assessments/phq9/submit");
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.body).toEqual({ answers: { phq9_1: 0, phq9_9: 1 } });
    // Authenticated: results belong to somebody.
    expect(calls[0]?.authorization).toBe("Bearer access-1");
  });

  it("passes a locale only when one is given", async () => {
    const client = clientWith(recordingFetch({}));
    await submitAssessment("gad7", { gad7_1: 0 }, { locale: "hi", client });
    expect(calls[0]?.body).toEqual({ answers: { gad7_1: 0 }, locale: "hi" });
    await submitAssessment("gad7", { gad7_1: 0 }, { client });
    expect(calls[1]?.body).toEqual({ answers: { gad7_1: 0 } });
  });

  it("reads history and one stored result", async () => {
    const client = clientWith(recordingFetch(historyResponse([])));
    await fetchAssessmentHistory("gad7", { limit: 5, client });
    expect(calls[0]?.url).toBe("/api/v1/assessments/gad7/history?limit=5");

    const second = clientWith(recordingFetch(resultView()));
    const result = await fetchAssessmentResult("phq9", "result-9", second);
    expect(result.id).toBe("result-1");
  });

  it("names every route in one place", () => {
    expect(ASSESSMENT_ENDPOINTS.list).toBe("/assessments");
    expect(ASSESSMENT_ENDPOINTS.submit("gad7")).toBe("/assessments/gad7/submit");
    expect(ASSESSMENT_ENDPOINTS.result("gad7", "abc")).toBe("/assessments/gad7/history/abc");
  });
});
