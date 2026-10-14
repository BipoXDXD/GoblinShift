import { describe, expect, expectTypeOf, it } from "vitest";

import type { components } from "../api/schema";
import { fakeApi, jsonResponse, runBody } from "../test/fakeApi";
import { listRuns, runListPath, type RunList } from "./api";

describe("GET /runs", () => {
  it("o tipo da lista espelha a spec", () => {
    expectTypeOf<RunList>().toEqualTypeOf<components["schemas"]["RunListResponse"]>();
  });

  it.each([
    [{ limit: 5 }, "/api/v1/runs?limit=5"],
    [
      {
        limit: 25,
        recipeId: "csv-janitor",
        status: "failed",
        createdFrom: "2026-10-07T12:00:00.000Z",
        cursor: "c/1+2=",
      },
      "/api/v1/runs?limit=25&recipe_id=csv-janitor&status=failed&created_from=2026-10-07T12%3A00%3A00.000Z&cursor=c%2F1%2B2%3D",
    ],
  ] as const)("monta a query string de %o", (query, path) => {
    expect(runListPath(query)).toBe(path);
  });

  it("devolve a página já validada, com o cursor da próxima", async () => {
    const api = fakeApi({
      "GET /api/v1/runs": () => jsonResponse({ data: [runBody()], next_cursor: "seguinte" }),
    });

    const page = await listRuns(api.client, { limit: 5 });

    expect(page.next_cursor).toBe("seguinte");
    expect(page.data.map((run) => run.status)).toEqual(["queued"]);
  });
});
