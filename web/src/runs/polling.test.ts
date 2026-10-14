import { describe, expect, it } from "vitest";

import { ApiError, type ApiFailure } from "../api/response";
import { nextPollDelayMs, type PollSnapshot } from "./polling";
import type { RunStatus } from "./api";

const noJitter = () => 0;

function success(status: RunStatus, retryAfterMs: number | null): PollSnapshot {
  return { kind: "success", status, retryAfterMs };
}

function failure(apiFailure: ApiFailure, consecutiveFailures = 1): PollSnapshot {
  return { kind: "failure", error: new ApiError(apiFailure), consecutiveFailures };
}

function problemFailure(status: number, retryAfterMs: number | null = null): ApiFailure {
  return {
    kind: "problem",
    status,
    retryAfterMs,
    problem: {
      type: "https://goblinshift.dev/problems/x",
      title: "t",
      status,
      detail: "d",
      instance: "/api/v1/runs/x",
      code: status === 404 ? "NOT_FOUND" : "SERVICE_UNAVAILABLE",
      request_id: "r",
    },
  };
}

describe("nextPollDelayMs: execução em andamento", () => {
  it.each([
    ["queued", 2_000],
    ["running", 2_000],
    ["stopping", 5_000],
  ] as const)("espera o Retry-After (%s, %d ms)", (status, retryAfterMs) => {
    expect(nextPollDelayMs(success(status, retryAfterMs), noJitter)).toBe(retryAfterMs);
  });

  it("usa 2 s quando a resposta não traz Retry-After", () => {
    expect(nextPollDelayMs(success("running", null), noJitter)).toBe(2_000);
  });

  it("segue um Retry-After menor que o padrão", () => {
    expect(nextPollDelayMs(success("running", 1_000), noJitter)).toBe(1_000);
  });
});

describe("nextPollDelayMs: estado terminal", () => {
  it.each(["succeeded", "succeeded_with_warnings", "canceled", "failed"] as const)(
    "para de consultar em %s",
    (status) => {
      expect(nextPollDelayMs(success(status, 2_000), noJitter)).toBe(false);
    },
  );
});

describe("nextPollDelayMs: falha", () => {
  it.each([
    [1, 2_000],
    [2, 4_000],
    [3, 8_000],
    [5, 30_000],
    [50, 30_000],
  ])("recua exponencialmente até 30 s (%d falhas → %d ms)", (failures, expected) => {
    expect(nextPollDelayMs(failure({ kind: "network" }, failures), noJitter)).toBe(expected);
  });

  it("nunca volta antes do Retry-After da falha", () => {
    expect(nextPollDelayMs(failure(problemFailure(503, 60_000)), noJitter)).toBe(60_000);
  });

  it("acrescenta jitter só para cima no recuo", () => {
    expect(nextPollDelayMs(failure({ kind: "timeout" }, 1), () => 0.5)).toBe(2_100);
  });

  it.each([
    ["timeout", { kind: "timeout" } as const],
    ["503", problemFailure(503)],
    [
      "502 sem problem+json",
      { kind: "unexpected-response", status: 502, retryAfterMs: null } as const,
    ],
    ["429", problemFailure(429)],
    ["408", problemFailure(408)],
  ])("continua depois de falha transitória (%s)", (_label, apiFailure) => {
    expect(nextPollDelayMs(failure(apiFailure), noJitter)).toBe(2_000);
  });

  it.each([401, 403, 404, 410])("para em falha que repetir não resolve (%d)", (status) => {
    expect(nextPollDelayMs(failure(problemFailure(status)), noJitter)).toBe(false);
  });

  it("para em erro que não veio da API (defeito da interface)", () => {
    const snapshot: PollSnapshot = {
      kind: "failure",
      error: new Error("bug"),
      consecutiveFailures: 1,
    };

    expect(nextPollDelayMs(snapshot, noJitter)).toBe(false);
  });
});
