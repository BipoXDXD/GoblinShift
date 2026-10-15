import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type { ApiProblem } from "../api/problem";
import { ApiError } from "../api/response";
import { ME_QUERY_KEY } from "./api";
import { isSignedOut, refreshAccountOn } from "./useMe";

function problemError(code: ApiProblem["code"], status: number): ApiError {
  return new ApiError({
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
      request_id: "r",
    },
  });
}

function spyClient() {
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
  return { client, invalidate };
}

describe("refreshAccountOn", () => {
  it.each([
    ["AUTHENTICATION_REQUIRED", 401],
    ["TERMS_NOT_ACCEPTED", 403],
  ] as const)("%s de outra leitura relê o /me", (code, status) => {
    const { client, invalidate } = spyClient();

    refreshAccountOn(client)(problemError(code, status), ["runs", "recent"]);

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ME_QUERY_KEY, refetchType: "active" });
  });

  it("falha de uma ação (sem queryKey) também relê o /me", () => {
    const { client, invalidate } = spyClient();

    refreshAccountOn(client)(problemError("TERMS_NOT_ACCEPTED", 403));

    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it("o 401 do próprio /me não relê o /me (sem laço)", () => {
    const { client, invalidate } = spyClient();

    refreshAccountOn(client)(problemError("AUTHENTICATION_REQUIRED", 401), ME_QUERY_KEY);

    expect(invalidate).not.toHaveBeenCalled();
  });

  it.each([
    ["FORBIDDEN", 403],
    ["REAUTH_REQUIRED", 401],
    ["CSRF_FAILED", 403],
  ] as const)("%s não mexe no /me", (code, status) => {
    const { client, invalidate } = spyClient();

    refreshAccountOn(client)(problemError(code, status), ["catalog"]);

    expect(invalidate).not.toHaveBeenCalled();
  });

  it("erro que não veio da API não mexe no /me", () => {
    const { client, invalidate } = spyClient();

    refreshAccountOn(client)(new Error("defeito"), ["catalog"]);

    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("isSignedOut", () => {
  it("só o AUTHENTICATION_REQUIRED é estar sem sessão", () => {
    expect(isSignedOut(problemError("AUTHENTICATION_REQUIRED", 401))).toBe(true);
    expect(isSignedOut(problemError("REAUTH_REQUIRED", 401))).toBe(false);
    expect(isSignedOut(new ApiError({ kind: "network" }))).toBe(false);
    expect(isSignedOut(null)).toBe(false);
  });
});
