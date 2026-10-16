import { describe, expectTypeOf, it } from "vitest";

import type { components } from "../api/schema";
import type {
  Coded,
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
  });

  it("RunItem: os campos da spec, com os códigos tolerantes a um valor novo", () => {
    expectTypeOf<RunItem>().toEqualTypeOf<{
      position: SpecItem["position"];
      upload_id: SpecItem["upload_id"];
      status: Coded<SpecItem["status"]>;
      error_code: Coded<NonNullable<SpecItem["error_code"]>> | null;
      warnings: Coded<SpecItem["warnings"][number]>[];
      counts: SpecItem["counts"];
      duplicate_of: SpecItem["duplicate_of"];
    }>();
  });
});
