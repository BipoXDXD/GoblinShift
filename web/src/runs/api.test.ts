import { describe, expectTypeOf, it } from "vitest";

import type { components } from "../api/schema";
import type { Run, RunDetail } from "./api";

describe("tipos de execução espelham a spec", () => {
  it("Run", () => {
    expectTypeOf<Run>().toEqualTypeOf<components["schemas"]["RunResponse"]>();
  });

  it("RunDetail", () => {
    expectTypeOf<RunDetail>().toEqualTypeOf<components["schemas"]["RunDetailResponse"]>();
  });
});
