import { describe, expectTypeOf, it } from "vitest";

import type { RunCommand } from "./types";

// Trava o formato que o gerador produz para o corpo de POST /runs: `allOf` (campos comuns) +
// `oneOf` das versões do catálogo, discriminado por `recipe_id`. Uma receita nova na API
// muda este teste de propósito, para o formulário dela ser escrito à mão (2.2).
describe("contrato gerado de POST /runs", () => {
  it("discrimina as variantes pelo recipe_id literal", () => {
    expectTypeOf<RunCommand["recipe_id"]>().toEqualTypeOf<"csv-janitor">();
  });

  it("fixa a versão de cada variante como literal", () => {
    type CsvJanitor = Extract<RunCommand, { recipe_id: "csv-janitor" }>;

    expectTypeOf<CsvJanitor["recipe_version"]>().toEqualTypeOf<1>();
  });

  it("dá a cada variante os próprios parâmetros, sem index signature", () => {
    type CsvJanitorParameters = Extract<RunCommand, { recipe_id: "csv-janitor" }>["parameters"];

    expectTypeOf<CsvJanitorParameters["input_delimiter"]>().toEqualTypeOf<
      "," | ";" | "\t" | undefined
    >();
    expectTypeOf<keyof CsvJanitorParameters>().toEqualTypeOf<
      | "input_delimiter"
      | "output_delimiter"
      | "trim_columns"
      | "case_rules"
      | "filters"
      | "deduplicate_by"
      | "output_columns"
      | "formula_guard"
    >();
  });

  it("mantém os campos comuns fora das variantes", () => {
    expectTypeOf<RunCommand["input_upload_ids"]>().toEqualTypeOf<string[] | undefined>();
  });
});
