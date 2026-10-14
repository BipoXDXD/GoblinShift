import { describe, expect, it } from "vitest";

import { catalogVersion } from "../test/catalog";
import { inputLimitsOf } from "./inputLimits";

describe("inputLimitsOf", () => {
  it("máximo de entradas, sem teto de bytes", () => {
    expect(inputLimitsOf(catalogVersion("csv-janitor"))).toEqual({
      maxInputs: 5,
      maxTotalBytes: null,
    });
  });

  it("teto de bytes do lote quando a versão tem", () => {
    expect(inputLimitsOf(catalogVersion("downloads-detox"))).toEqual({
      maxInputs: 100,
      maxTotalBytes: 503316480,
    });
  });

  it.each([
    ["sem max_inputs", {}],
    ["max_inputs zero", { max_inputs: 0 }],
    ["teto de bytes negativo", { max_inputs: 1, max_total_input_bytes: -1 }],
  ])("%s → null", (_, limits) => {
    expect(inputLimitsOf({ ...catalogVersion("csv-janitor"), limits })).toBeNull();
  });
});
