import { describe, expect, it } from "vitest";

import type { ApiProblem } from "./problem";
import { ApiError, type ApiFailure } from "./response";
import { shouldRetryQuery } from "./queryRetry";

function problem(status: number, code: ApiProblem["code"]): ApiFailure {
  return {
    kind: "problem",
    status,
    retryAfterMs: null,
    problem: {
      type: "x",
      title: "x",
      status,
      detail: "x",
      instance: "/x",
      code,
      request_id: "req-1",
    },
  };
}

describe("shouldRetryQuery", () => {
  it.each<[string, ApiFailure]>([
    ["rede", { kind: "network" }],
    ["timeout", { kind: "timeout" }],
    ["503 problem+json", problem(503, "SERVICE_UNAVAILABLE")],
    [
      "502 sem problem+json (proxy)",
      { kind: "unexpected-response", status: 502, retryAfterMs: null },
    ],
  ])("falha transitória (%s) tenta de novo", (_, failure) => {
    expect(shouldRetryQuery(0, new ApiError(failure))).toBe(true);
  });

  it.each<[string, ApiFailure]>([
    ["401 sem login", problem(401, "AUTHENTICATION_REQUIRED")],
    ["404", problem(404, "NOT_FOUND")],
    ["429", problem(429, "RATE_LIMITED")],
    ["499 sem problem+json", { kind: "unexpected-response", status: 499, retryAfterMs: null }],
    [
      "200 com corpo fora do schema",
      { kind: "unexpected-response", status: 200, retryAfterMs: null },
    ],
  ])("resposta definitiva (%s) não tenta de novo", (_, failure) => {
    expect(shouldRetryQuery(0, new ApiError(failure))).toBe(false);
  });

  it("500 é o primeiro status que tenta de novo", () => {
    expect(shouldRetryQuery(0, new ApiError(problem(500, "INTERNAL_ERROR")))).toBe(true);
  });

  it("para depois de 3 tentativas a mais", () => {
    const failure = new ApiError({ kind: "network" });
    expect(shouldRetryQuery(2, failure)).toBe(true);
    expect(shouldRetryQuery(3, failure)).toBe(false);
  });

  it("defeito da interface (erro que não veio da API) não tenta de novo", () => {
    expect(shouldRetryQuery(0, new TypeError("x is undefined"))).toBe(false);
  });
});
