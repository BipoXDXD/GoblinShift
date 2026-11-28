import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import { ApiError } from "../api/response";
import { fakeApi, problemResponse } from "./fakeApi";

describe("fakeApi", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("o cliente da API falsa lê o Retry-After em HTTP-date pelo relógio fixo dos testes", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    // O relógio real fica longe do fixo: a data do header já passou para ele.
    vi.setSystemTime(new Date("2030-01-01T00:00:00Z"));
    const { client } = fakeApi({
      "GET /api/v1/runs": () =>
        problemResponse(503, "SERVICE_UNAVAILABLE", {
          headers: { "Retry-After": "Wed, 14 Oct 2026 12:00:30 GMT" },
        }),
    });

    const error = await client
      .request({ method: "GET", path: "/api/v1/runs", schema: z.unknown() })
      .catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).failure).toMatchObject({ retryAfterMs: 30_000 });
  });
});
