import { describe, expect, it } from "vitest";

import { catalogVersion } from "../test/catalog";
import { inputLimitsOf } from "./inputLimits";

describe("inputLimitsOf", () => {
  it("máximo de entradas e teto por arquivo, sem teto do lote", () => {
    expect(inputLimitsOf(catalogVersion("csv-janitor"))).toEqual({
      maxInputs: 5,
      maxTotalBytes: null,
      maxFileBytes: 33554432,
    });
  });

  it("sem teto por arquivo no catálogo (versão anterior da API), fica sem teto no cliente", () => {
    expect(inputLimitsOf({ limits: { max_inputs: 5 } })).toEqual({
      maxInputs: 5,
      maxTotalBytes: null,
      maxFileBytes: null,
    });
  });

  it("teto de bytes do lote quando a versão tem", () => {
    expect(inputLimitsOf(catalogVersion("downloads-detox"))).toEqual({
      maxInputs: 100,
      maxTotalBytes: 503316480,
      maxFileBytes: 33554432,
    });
  });

  it("teto por arquivo quando a versão tem (vídeo do Media Mill)", () => {
    expect(inputLimitsOf(catalogVersion("media-mill"))).toEqual({
      maxInputs: 5,
      maxTotalBytes: 536870912,
      maxFileBytes: 268435456,
    });
  });

  it.each([
    ["sem max_inputs", {}],
    ["max_inputs zero", { max_inputs: 0 }],
    ["teto de bytes negativo", { max_inputs: 1, max_total_input_bytes: -1 }],
    ["teto por arquivo zero", { max_inputs: 1, max_input_bytes: 0 }],
  ])("%s → null", (_, limits) => {
    expect(inputLimitsOf({ ...catalogVersion("csv-janitor"), limits })).toBeNull();
  });
});
