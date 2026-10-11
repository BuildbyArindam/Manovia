import { describe, expect, it, vi } from "vitest";

import { ApiClient, MemoryTokenStore } from "@/lib/api";
import { breathingExercise, completion } from "@/test/exerciseFakes";

import {
  completeExercise,
  fetchExercise,
  fetchExerciseHistory,
  fetchExercises,
  fetchRecommendations,
} from "./endpoints";

function transport(response: unknown) {
  const fetchImpl = vi.fn(
    async () =>
      new Response(JSON.stringify(response), { headers: { "Content-Type": "application/json" } }),
  );
  const client = new ApiClient({ tokens: new MemoryTokenStore(), fetchImpl });
  client.applySession({
    access_token: "fixture-access",
    refresh_token: "fixture-refresh",
    expires_in: 900,
  });
  return { client, fetchImpl };
}

describe("exercise wire contract", () => {
  it("fetches the whole public catalogue without an auth header", async () => {
    const { client, fetchImpl } = transport({ exercises: [breathingExercise()], count: 1 });
    const result = await fetchExercises(client);
    expect(result.count).toBe(1);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/exercises",
      expect.objectContaining({ method: "GET" }),
    );
    const init = (fetchImpl.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it("encodes a detail id and makes a public request", async () => {
    const { client, fetchImpl } = transport(breathingExercise());
    await fetchExercise("a/b", client);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/exercises/a%2Fb",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("sends the selected emotion and intensity to the rule endpoint", async () => {
    const { client, fetchImpl } = transport({
      emotion: "anxiety",
      intensity: "high",
      recommendations: [],
    });
    await fetchRecommendations("anxiety", "high", client);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/exercises/recommendations?emotion=anxiety&intensity=high",
      expect.anything(),
    );
  });

  it("posts only numeric ratings and the client UUID with authentication", async () => {
    const { client, fetchImpl } = transport(completion());
    const payload = { completion_id: "fixture-uuid", pre_rating: 2, post_rating: 4 };
    await completeExercise("box-breathing", payload, client);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/exercises/box-breathing/complete",
      expect.objectContaining({ method: "POST", body: JSON.stringify(payload) }),
    );
    const init = (fetchImpl.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer fixture-access");
  });

  it("fetches paginated owner history, including the default first page", async () => {
    const { client, fetchImpl } = transport({ items: [], count: 0, total: 0 });
    await fetchExerciseHistory(20, client);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/api/v1/exercises/history?limit=20&offset=20",
      expect.anything(),
    );
    await fetchExerciseHistory(undefined, client);
    expect(fetchImpl).toHaveBeenLastCalledWith(
      "/api/v1/exercises/history?limit=20&offset=0",
      expect.anything(),
    );
  });
});
