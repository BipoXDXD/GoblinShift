import { describe, expect, it, onTestFinished, vi } from "vitest";

import type { ApiProblem } from "./problem";
import { ApiError, type ApiFailure } from "./response";
import { queryRetryDelay, shouldRetryQuery } from "./queryRetry";

function problem(
  status: number,
  code: ApiProblem["code"],
): Extract<ApiFailure, { kind: "problem" }> {
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

  it("permite só uma tentativa a mais em caso de timeout", () => {
    const failure = new ApiError({ kind: "timeout" });
    expect(shouldRetryQuery(0, failure)).toBe(true);
    expect(shouldRetryQuery(1, failure)).toBe(false);
  });

  it("defeito da interface (erro que não veio da API) não tenta de novo", () => {
    expect(shouldRetryQuery(0, new TypeError("x is undefined"))).toBe(false);
  });
});

describe("queryRetryDelay", () => {
  it.each<ApiFailure>([
    { ...problem(503, "SERVICE_UNAVAILABLE"), retryAfterMs: 30_000 },
    { kind: "unexpected-response", status: 503, retryAfterMs: 30_000 },
  ])("respeita Retry-After de 30 s com resposta $kind", (failure) => {
    expect(queryRetryDelay(0, new ApiError(failure))).toBe(30_000);
  });

  it("aumenta o atraso exponencial e sorteia uma folga mesmo sem Retry-After", () => {
    const random = vi.spyOn(Math, "random").mockReturnValue(0);
    onTestFinished(() => {
      random.mockRestore();
    });
    const failure = new ApiError(problem(503, "SERVICE_UNAVAILABLE"));
    expect([0, 1, 2].map((attempt) => queryRetryDelay(attempt, failure))).toEqual([
      1_000, 2_000, 4_000,
    ]);

    random.mockReturnValue(0.5);
    expect(queryRetryDelay(1, failure)).toBe(2_500);
  });

  it("mantém o backoff quando Retry-After fica abaixo da espera local", () => {
    const random = vi.spyOn(Math, "random").mockReturnValue(0);
    onTestFinished(() => {
      random.mockRestore();
    });
    const failure = new ApiError({ kind: "unexpected-response", status: 503, retryAfterMs: 500 });
    expect(queryRetryDelay(0, failure)).toBe(1_000);
  });
});
