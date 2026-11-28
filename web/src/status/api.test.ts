import { describe, expect, expectTypeOf, it } from "vitest";

import type { components } from "../api/schema";
import { ApiError } from "../api/response";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { getServiceStatus, type ServiceStatus } from "./api";

describe("tipos do estado do serviço", () => {
  it("são os da spec", () => {
    expectTypeOf<ServiceStatus>().toEqualTypeOf<components["schemas"]["ServiceStatusResponse"]>();
  });
});

describe("getServiceStatus", () => {
  it("lê GET /status sem credencial e devolve os níveis validados", async () => {
    const body = {
      api: "up",
      processing: "slow",
      queue: "busy",
      checked_at: "2026-11-28T12:30:00Z",
    };
    const api = fakeApi({ "GET /api/v1/status": () => jsonResponse(body) }, { csrfToken: null });

    expect(await getServiceStatus(api.client)).toEqual(body);
    expect(api.requests[0]?.headers.get("Authorization")).toBeNull();
  });

  it("recusa corpo com nível fora da spec", async () => {
    const api = fakeApi({
      "GET /api/v1/status": () =>
        jsonResponse({ api: "up", processing: "ok", queue: "free", checked_at: "x" }),
    });

    await expect(getServiceStatus(api.client)).rejects.toBeInstanceOf(ApiError);
  });
});
