import { describe, expect, expectTypeOf, it } from "vitest";
import { z } from "zod";

import spec from "../../../openapi/openapi.json";
import type { RecipeParameters } from "../types";
import {
  CASE_MODES,
  CSV_JANITOR_DEFAULTS,
  csvJanitorFieldId,
  DELIMITERS,
  FILTER_OPS,
  FORMULA_GUARDS,
  MAX_FILTERS,
  MAX_LIST_COLUMNS,
  parseCsvJanitor,
  type CsvJanitorFormValues,
} from "./schema";

type Parameters = RecipeParameters<"csv-janitor">;

function values(overrides: Partial<CsvJanitorFormValues> = {}): CsvJanitorFormValues {
  return { ...CSV_JANITOR_DEFAULTS, ...overrides };
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

// O tipo não carrega defaults nem limites; o openapi.json versionado, sim. Se a API mudar um deles
// e o tipo continuar igual, este teste falha.
const specSchema = z.object({
  paths: z.object({
    "/api/v1/runs": z.object({
      post: z.object({
        requestBody: z.object({
          content: z.object({
            "application/json": z.object({
              schema: z.object({
                allOf: z.tuple([
                  z.object({}),
                  z.object({
                    oneOf: z.array(
                      z.object({
                        properties: z.object({
                          recipe_id: z.object({ const: z.string() }),
                          parameters: z.object({ properties: z.record(z.string(), z.unknown()) }),
                        }),
                      }),
                    ),
                  }),
                ]),
              }),
            }),
          }),
        }),
      }),
    }),
  }),
});

const enumProperty = z.object({ enum: z.array(z.string()), default: z.string() });
const listProperty = z.object({
  maxItems: z.number(),
  items: z.object({
    properties: z.record(z.string(), z.object({ enum: z.array(z.string()) }).partial()).optional(),
  }),
});
const nullableListProperty = z.object({
  anyOf: z.tuple([z.object({ maxItems: z.number(), minItems: z.number() }), z.object({})]),
});

function specParameters(): Record<string, unknown> {
  const variants =
    specSchema.parse(spec).paths["/api/v1/runs"].post.requestBody.content["application/json"].schema
      .allOf[1].oneOf;
  const variant = variants.find(
    (candidate) => candidate.properties.recipe_id.const === "csv-janitor",
  );
  if (variant === undefined) {
    throw new Error("csv-janitor ausente da spec");
  }
  return variant.properties.parameters.properties;
}

describe("defaults e limites conferidos contra openapi.json", () => {
  const properties = specParameters();

  it.each(["input_delimiter", "output_delimiter"] as const)("%s", (name) => {
    const property = enumProperty.parse(properties[name]);

    expect(property.enum).toEqual([...DELIMITERS]);
    expect(CSV_JANITOR_DEFAULTS[name]).toBe(property.default);
  });

  it("formula_guard", () => {
    const property = enumProperty.parse(properties["formula_guard"]);

    expect(property.enum).toEqual([...FORMULA_GUARDS]);
    expect(CSV_JANITOR_DEFAULTS.formula_guard).toBe(property.default);
  });

  it.each(["trim_columns", "case_rules", "deduplicate_by"] as const)("máximo de %s", (name) => {
    expect(listProperty.parse(properties[name]).maxItems).toBe(MAX_LIST_COLUMNS);
  });

  it("máximo de filters e operadores", () => {
    const filters = listProperty.parse(properties["filters"]);

    expect(filters.maxItems).toBe(MAX_FILTERS);
    expect(filters.items.properties?.["op"]?.enum).toEqual([...FILTER_OPS]);
  });

  it("modos de caixa", () => {
    const rules = listProperty.parse(properties["case_rules"]);

    expect(rules.items.properties?.["mode"]?.enum).toEqual([...CASE_MODES]);
  });

  it("output_columns: de 1 ao máximo, ou null", () => {
    const [list] = nullableListProperty.parse(properties["output_columns"]).anyOf;

    expect(list).toEqual({ maxItems: MAX_LIST_COLUMNS, minItems: 1 });
  });
});

describe("parseCsvJanitor", () => {
  it("os defaults viram um comando válido e explícito", () => {
    const draft = parseCsvJanitor(CSV_JANITOR_DEFAULTS);

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
