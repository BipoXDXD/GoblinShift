import { describe, expect, it } from "vitest";

import { catalogVersion } from "../../test/catalog";
import { csvJanitorSettings } from "./settings";

const version = catalogVersion("csv-janitor");

describe("csvJanitorSettings", () => {
  it("defaults do catálogo viram os valores iniciais do formulário", () => {
    expect(csvJanitorSettings(version)?.defaults).toEqual({
      input_delimiter: ",",
      output_delimiter: ",",
      trim_columns: "",
      case_rules: [],
      filters: [],
      deduplicate_by: "",
      output_columns: "",
      formula_guard: "none",
    });
  });

  it("listas e linhas dos defaults viram texto e linhas do formulário", () => {
    const settings = csvJanitorSettings({
      ...version,
      defaults: {
        ...version.defaults,
        input_delimiter: ";",
        trim_columns: ["a", "b"],
        output_columns: ["c"],
        case_rules: [{ column: "nome", mode: "upper" }],
        filters: [{ column: "status", op: "is_empty", value: null }],
      },
    });

    expect(settings?.defaults).toMatchObject({
      input_delimiter: ";",
      trim_columns: "a\nb",
      output_columns: "c",
      case_rules: [{ column: "nome", mode: "upper" }],
      filters: [{ column: "status", op: "is_empty", value: "" }],
    });
  });

  it("limites: colunas da tabela e máximo de filtros do schema", () => {
    expect(csvJanitorSettings(version)?.limits).toEqual({ maxColumns: 100, maxFilters: 5 });
  });

  it("os limites seguem o catálogo", () => {
    const settings = csvJanitorSettings({
      ...version,
      limits: { ...version.limits, columns: 7 },
      parameters_schema: { properties: { filters: { maxItems: 2 } } },
    });

    expect(settings?.limits).toEqual({ maxColumns: 7, maxFilters: 2 });
  });

  it.each([
    ["delimitador desconhecido", { defaults: { ...version.defaults, input_delimiter: "|" } }],
    ["default ausente", { defaults: { input_delimiter: "," } }],
    ["sem limite de colunas", { limits: { max_inputs: 5 } }],
    ["schema sem filters", { parameters_schema: { properties: {} } }],
    ["máximo de filtros zero", { parameters_schema: { properties: { filters: { maxItems: 0 } } } }],
  ])("catálogo incompatível (%s) → null", (_, override) => {
    expect(csvJanitorSettings({ ...version, ...override })).toBeNull();
  });
});
