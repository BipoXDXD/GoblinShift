import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type { ApiProblem } from "../api/problem";
import { ApiError } from "../api/response";
import { meBody } from "../test/account";
import { fakeApi, jsonResponse, problemResponse } from "../test/fakeApi";
import { ME_QUERY_KEY } from "./api";
import { isSignedOut, readAccount, refreshAccountOn } from "./useMe";

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

describe("readAccount", () => {
  it("sem o cookie de CSRF, é sem sessão e não chama o /me", async () => {
    const api = fakeApi({}, { csrfToken: null });

    await expect(readAccount(api.client)).resolves.toBeNull();
    expect(api.requests).toHaveLength(0);
  });

  it("com o cookie, devolve a conta do /me", async () => {
    const api = fakeApi({ "GET /api/v1/me": () => jsonResponse(meBody()) });

    await expect(readAccount(api.client)).resolves.toMatchObject({ via: "session" });
  });

  it("com o cookie de uma sessão que acabou, o 401 é sem sessão", async () => {
    const api = fakeApi({
      "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED"),
    });

    await expect(readAccount(api.client)).resolves.toBeNull();
  });

  it.each([
    ["REAUTH_REQUIRED", 401],
    ["SERVICE_UNAVAILABLE", 503],
  ] as const)("%s continua sendo falha", async (code, status) => {
    const api = fakeApi({ "GET /api/v1/me": () => problemResponse(status, code) });

    await expect(readAccount(api.client)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("isSignedOut", () => {
  it("só a conta nula é estar sem sessão; carregando não é", () => {
    expect(isSignedOut(null)).toBe(true);
    expect(isSignedOut(undefined)).toBe(false);
    expect(isSignedOut(meBody())).toBe(false);
  });
});
