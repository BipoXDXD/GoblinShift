import { describe, expect, expectTypeOf, it } from "vitest";

import type { components } from "../api/schema";
import { fakeApi, jsonResponse, RUN_ID, runBody } from "../test/fakeApi";
import { getRun } from "./api";
import type {
  Coded,
  ITEM_NOTICES,
  ITEM_REJECTIONS,
  ITEM_STATUSES,
  ITEM_WARNINGS,
  Run,
  RunDetail,
  RunItem,
} from "./api";

type Schemas = components["schemas"];
type SpecItem = Schemas["RunItemResponse"];

describe("tipos de execução espelham a spec", () => {
  it("Run", () => {
    expectTypeOf<Run>().toEqualTypeOf<Schemas["RunResponse"]>();
  });

  it("RunDetail, fora os itens", () => {
    expectTypeOf<Omit<RunDetail, "items">>().toEqualTypeOf<
      Omit<Schemas["RunDetailResponse"], "items">
    >();
  });

  it("listas fechadas dos itens", () => {
    expectTypeOf<(typeof ITEM_STATUSES)[number]>().toEqualTypeOf<Schemas["ItemStatus"]>();
    expectTypeOf<(typeof ITEM_REJECTIONS)[number]>().toEqualTypeOf<Schemas["ItemRejectionCode"]>();
    expectTypeOf<(typeof ITEM_WARNINGS)[number]>().toEqualTypeOf<Schemas["ItemWarningCode"]>();
    expectTypeOf<(typeof ITEM_NOTICES)[number]>().toEqualTypeOf<Schemas["ItemNoticeCode"]>();
  });

  it("RunItem: os campos da spec, com os códigos tolerantes a um valor novo", () => {
    expectTypeOf<RunItem>().toEqualTypeOf<{
      position: SpecItem["position"];
      upload_id: SpecItem["upload_id"];
      original_name: SpecItem["original_name"];
      status: Coded<SpecItem["status"]>;
      error_code: Coded<NonNullable<SpecItem["error_code"]>> | null;
      warnings: Coded<SpecItem["warnings"][number]>[];
      notices: Coded<SpecItem["notices"][number]>[];
      counts: SpecItem["counts"];
      duplicate_of: SpecItem["duplicate_of"];
      formula_cells: SpecItem["formula_cells"];
      comparison: SpecItem["comparison"];
    }>();
  });
});

describe("GET /runs/{id}", () => {
  const route = `GET /api/v1/runs/${RUN_ID}`;

  it("aceita o corpo de uma execução válida", async () => {
    const api = fakeApi({ [route]: () => jsonResponse(runBody()) });

    await expect(getRun(api.client, RUN_ID)).resolves.toMatchObject({
      status: 200,
      data: { id: RUN_ID },
    });
  });

  it.each([
    ["UUID inválido", { id: "id-invalido" }],
    ["data sem fuso", { created_at: "2026-10-14T12:00:00" }],
    ["tentativa fracionária", { attempt: 1.5 }],
  ] as const)("recusa %s como resposta inesperada", async (_description, override) => {
    const api = fakeApi({ [route]: () => jsonResponse(runBody(override)) });

    await expect(getRun(api.client, RUN_ID)).rejects.toMatchObject({
      failure: { kind: "unexpected-response", status: 200 },
    });
  });
});
