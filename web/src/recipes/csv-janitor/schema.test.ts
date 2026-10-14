import { describe, expect, expectTypeOf, it } from "vitest";

import type { RecipeParameters } from "../types";
import type { CASE_MODES, DELIMITERS, FILTER_OPS, FORMULA_GUARDS } from "./schema";
import {
  csvJanitorFieldId,
  parseCsvJanitor as parseWithLimits,
  type CsvJanitorFormValues,
  type CsvJanitorLimits,
} from "./schema";

type Parameters = RecipeParameters<"csv-janitor">;

const LIMITS: CsvJanitorLimits = { maxColumns: 100, maxFilters: 5 };
const MAX_LIST_COLUMNS = LIMITS.maxColumns;
const MAX_FILTERS = LIMITS.maxFilters;

const DEFAULT_VALUES: CsvJanitorFormValues = {
  input_delimiter: ",",
  output_delimiter: ",",
  trim_columns: "",
  case_rules: [],
  filters: [],
  deduplicate_by: "",
  output_columns: "",
  formula_guard: "none",
};

function values(overrides: Partial<CsvJanitorFormValues> = {}): CsvJanitorFormValues {
  return { ...DEFAULT_VALUES, ...overrides };
}

function parseCsvJanitor(formValues: CsvJanitorFormValues) {
  return parseWithLimits(formValues, LIMITS);
}

function lines(count: number): string {
  return Array.from({ length: count }, (_, index) => `coluna${String(index)}`).join("\n");
}

function parametersOf(draft: ReturnType<typeof parseCsvJanitor>) {
  if (!draft.ok) {
    throw new Error(`rascunho inválido: ${JSON.stringify(draft.issues)}`);
  }
  return draft.command.parameters;
}

function issuesOf(draft: ReturnType<typeof parseCsvJanitor>) {
  if (draft.ok) {
    throw new Error("rascunho deveria ser inválido");
  }
  return draft.issues;
}

describe("constantes espelham os tipos gerados", () => {
  it("cobrem cada união da spec", () => {
    expectTypeOf<(typeof DELIMITERS)[number]>().toEqualTypeOf<
      NonNullable<Parameters["input_delimiter"]>
    >();
    expectTypeOf<(typeof FORMULA_GUARDS)[number]>().toEqualTypeOf<
      NonNullable<Parameters["formula_guard"]>
    >();
    expectTypeOf<(typeof CASE_MODES)[number]>().toEqualTypeOf<
      NonNullable<Parameters["case_rules"]>[number]["mode"]
    >();
    expectTypeOf<(typeof FILTER_OPS)[number]>().toEqualTypeOf<
      NonNullable<Parameters["filters"]>[number]["op"]
    >();
  });
});

describe("parseCsvJanitor", () => {
  it("os defaults viram um comando válido e explícito", () => {
    const draft = parseCsvJanitor(DEFAULT_VALUES);

    expect(draft).toEqual({
      ok: true,
      command: {
        recipe_id: "csv-janitor",
        recipe_version: 1,
        parameters: {
          input_delimiter: ",",
          output_delimiter: ",",
          trim_columns: [],
          case_rules: [],
          filters: [],
          deduplicate_by: [],
          output_columns: null,
          formula_guard: "none",
        },
      },
    });
  });

  it("listas de colunas: uma por linha, sem linhas em branco, texto preservado", () => {
    const parameters = parametersOf(
      parseCsvJanitor(values({ trim_columns: " nome \r\n\n  \nemail", deduplicate_by: "id" })),
    );

    expect(parameters.trim_columns).toEqual([" nome ", "email"]);
    expect(parameters.deduplicate_by).toEqual(["id"]);
  });

  it("colunas finais vazias viram null (todas, na ordem original)", () => {
    expect(
      parametersOf(parseCsvJanitor(values({ output_columns: "\n" }))).output_columns,
    ).toBeNull();
  });

  it("colunas finais preenchidas viram a lista", () => {
    expect(
      parametersOf(parseCsvJanitor(values({ output_columns: "b\na" }))).output_columns,
    ).toEqual(["b", "a"]);
  });

  it.each([
    ["no máximo", MAX_LIST_COLUMNS, true],
    ["acima do máximo", MAX_LIST_COLUMNS + 1, false],
  ])("lista de colunas %s", (_label, count, valid) => {
    expect(parseCsvJanitor(values({ trim_columns: lines(count) })).ok).toBe(valid);
  });

  it("acima do máximo aponta o campo", () => {
    const issues = issuesOf(
      parseCsvJanitor(values({ output_columns: lines(MAX_LIST_COLUMNS + 1) })),
    );

    expect(issues.map((issue) => issue.fieldId)).toEqual(["csv-janitor-output_columns"]);
    expect(issues[0]?.message).toBe("Informe no máximo 100 colunas.");
  });

  it("filtro is_empty vai sem valor; os outros levam o texto, mesmo vazio", () => {
    const parameters = parametersOf(
      parseCsvJanitor(
        values({
          filters: [
            { key: "a", column: "status", op: "is_empty", value: "ignorado" },
            { key: "b", column: "status", op: "eq", value: "" },
          ],
        }),
      ),
    );

    expect(parameters.filters).toEqual([
      { column: "status", op: "is_empty", value: null },
      { column: "status", op: "eq", value: "" },
    ]);
  });

  it("regras de caixa saem sem a chave da linha", () => {
    const parameters = parametersOf(
      parseCsvJanitor(values({ case_rules: [{ key: "k", column: "nome", mode: "upper" }] })),
    );

    expect(parameters.case_rules).toEqual([{ column: "nome", mode: "upper" }]);
  });

  it("linha sem coluna aponta o campo da linha", () => {
    const issues = issuesOf(
      parseCsvJanitor(
        values({
          case_rules: [
            { key: "a", column: "nome", mode: "lower" },
            { key: "b", column: "", mode: "upper" },
          ],
        }),
      ),
    );

    expect(issues).toEqual([
      { fieldId: "csv-janitor-case_rules-1-column", message: "Informe a coluna." },
    ]);
  });

  it.each([
    [MAX_FILTERS, true],
    [MAX_FILTERS + 1, false],
  ])("%i filtros", (count, valid) => {
    const filters = Array.from({ length: count }, (_, index) => ({
      key: String(index),
      column: "c",
      op: "ne" as const,
      value: "x",
    }));

    expect(parseCsvJanitor(values({ filters })).ok).toBe(valid);
  });
});

describe("parseCsvJanitor com outros limites do catálogo", () => {
  it("usa os máximos recebidos", () => {
    const limits: CsvJanitorLimits = { maxColumns: 2, maxFilters: 1 };
    const filter = { key: "a", column: "c", op: "eq" as const, value: "x" };

    expect(parseWithLimits(values({ trim_columns: lines(2) }), limits).ok).toBe(true);
    expect(parseWithLimits(values({ trim_columns: lines(3) }), limits).ok).toBe(false);
    expect(parseWithLimits(values({ filters: [filter, { ...filter, key: "b" }] }), limits).ok).toBe(
      false,
    );
  });
});

describe("csvJanitorFieldId", () => {
  it.each([
    [["input_delimiter"], "csv-janitor-input_delimiter"],
    [["trim_columns", "3"], "csv-janitor-trim_columns"],
    [["output_columns"], "csv-janitor-output_columns"],
    [["case_rules"], "csv-janitor-case_rules-adicionar"],
    [["case_rules", "2", "mode"], "csv-janitor-case_rules-2-mode"],
    [["filters", 0, "value"], "csv-janitor-filters-0-value"],
    [["filters", "0"], "csv-janitor-filters-0-op"],
    [["filters", "0", "desconhecido"], "csv-janitor-filters-0-op"],
    [["filters", "x"], "csv-janitor-filters-adicionar"],
  ])("%j → %s", (path, expected) => {
    expect(csvJanitorFieldId(path)).toBe(expected);
  });

  it.each([[[]], [["recipe_version"]], [["__proto__"]]])("caminho sem campo %j → null", (path) => {
    expect(csvJanitorFieldId(path)).toBeNull();
  });
});
