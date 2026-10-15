import { describe, expect, expectTypeOf, it } from "vitest";

import type { components } from "../api/schema";
import { ApiError } from "../api/response";
import { fakeApi, jsonResponse, problemResponse } from "../test/fakeApi";
import {
  acceptTerms,
  createApiKey,
  deleteAccount,
  getMe,
  listApiKeys,
  logout,
  logoutAll,
  revokeApiKey,
  type AcceptedTerms,
  type ApiKey,
  type CreatedApiKey,
  type Me,
} from "./api";
import { apiKeyBody, KEY_ID, meBody } from "../test/account";
import type { SCOPES } from "./api";

type Schemas = components["schemas"];

describe("tipos da conta", () => {
  it("são os da spec", () => {
    expectTypeOf<Me>().toEqualTypeOf<Schemas["MeResponse"]>();
    expectTypeOf<ApiKey>().toEqualTypeOf<Schemas["ApiKeyResponse"]>();
    expectTypeOf<CreatedApiKey>().toEqualTypeOf<Schemas["CreatedApiKey"]>();
    expectTypeOf<AcceptedTerms>().toEqualTypeOf<Schemas["AcceptedTermsResponse"]>();
    expectTypeOf<(typeof SCOPES)[number]>().toEqualTypeOf<Schemas["Scope"]>();
  });
});

describe("getMe", () => {
  it("lê GET /me e devolve a conta validada", async () => {
    const api = fakeApi({ "GET /api/v1/me": () => jsonResponse(meBody()) });

    const me = await getMe(api.client);

    expect(me.display_name).toBe("Ana");
    expect(me.terms.acceptance_required).toBe(false);
  });

  it("recusa corpo fora do schema como resposta inesperada", async () => {
    const api = fakeApi({ "GET /api/v1/me": () => jsonResponse({ ...meBody(), via: "cookie" }) });

    await expect(getMe(api.client)).rejects.toMatchObject({
      failure: { kind: "unexpected-response", status: 200 },
    });
  });

  it("sem sessão, falha com AUTHENTICATION_REQUIRED", async () => {
    const api = fakeApi({
      "GET /api/v1/me": () => problemResponse(401, "AUTHENTICATION_REQUIRED"),
    });

    const error: unknown = await getMe(api.client).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      failure: { kind: "problem", problem: { code: "AUTHENTICATION_REQUIRED" } },
    });
  });
});

describe("mutações da conta", () => {
  it("aceita os termos mandando só a versão lida", async () => {
    const api = fakeApi({
      "POST /api/v1/me/terms:accept": () =>
        jsonResponse({ version: "v1", accepted_at: "2026-10-14T12:00:00Z" }),
    });

    const accepted = await acceptTerms(api.client, "v1");

    expect(accepted).toEqual({ version: "v1", accepted_at: "2026-10-14T12:00:00Z" });
    expect(await api.requests[0]?.json()).toEqual({ version: "v1" });
  });

  it.each([
    ["logout", "POST /auth/logout", logout],
    ["logoutAll", "POST /auth/logout-all", logoutAll],
  ] as const)("%s chama %s e aceita o 204 sem corpo", async (_name, route, call) => {
    const api = fakeApi({ [route]: () => new Response(null, { status: 204 }) });

    await expect(call(api.client)).resolves.toBeUndefined();
    expect(
      api.requests.map((request) => `${request.method} ${new URL(request.url).pathname}`),
    ).toEqual([route]);
  });

  it("exclui a conta com DELETE /me e aceita o 202", async () => {
    const api = fakeApi({ "DELETE /api/v1/me": () => new Response(null, { status: 202 }) });

    await expect(deleteAccount(api.client)).resolves.toBeUndefined();
  });

  it("exclusão sem login recente falha com REAUTH_REQUIRED", async () => {
    const api = fakeApi({ "DELETE /api/v1/me": () => problemResponse(401, "REAUTH_REQUIRED") });

    await expect(deleteAccount(api.client)).rejects.toMatchObject({
      failure: { kind: "problem", problem: { code: "REAUTH_REQUIRED" } },
    });
  });
});

describe("chaves de API", () => {
  it("lista as chaves do envelope data", async () => {
    const api = fakeApi({
      "GET /api/v1/api-keys": () => jsonResponse({ data: [apiKeyBody()] }),
    });

    expect(await listApiKeys(api.client)).toEqual([apiKeyBody()]);
  });

  it("lista vazia é uma lista, não um erro", async () => {
    const api = fakeApi({ "GET /api/v1/api-keys": () => jsonResponse({ data: [] }) });

    expect(await listApiKeys(api.client)).toEqual([]);
  });

  it("cria com escopos e validade e devolve o segredo da resposta", async () => {
    const created = {
      id: KEY_ID,
      public_id: "gsk_abc",
      token: "gsk_abc.segredo",
      scopes: ["runs:read"],
      expires_at: "2026-11-13T12:00:00Z",
    };
    const api = fakeApi({ "POST /api/v1/api-keys": () => jsonResponse(created, 201) });

    const key = await createApiKey(api.client, { scopes: ["runs:read"], days: 30 });

    expect(key.token).toBe("gsk_abc.segredo");
    expect(await api.requests[0]?.json()).toEqual({ scopes: ["runs:read"], days: 30 });
  });

  it("revoga pelo id no caminho", async () => {
    const api = fakeApi({
      [`DELETE /api/v1/api-keys/${KEY_ID}`]: () => new Response(null, { status: 204 }),
    });

    await expect(revokeApiKey(api.client, KEY_ID)).resolves.toBeUndefined();
  });

  it("chave alheia ou inexistente falha com NOT_FOUND", async () => {
    const api = fakeApi({
      [`DELETE /api/v1/api-keys/${KEY_ID}`]: () => problemResponse(404, "NOT_FOUND"),
    });

    await expect(revokeApiKey(api.client, KEY_ID)).rejects.toMatchObject({
      failure: { kind: "problem", status: 404 },
    });
  });
});
