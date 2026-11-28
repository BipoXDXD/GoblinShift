import { describe, expectTypeOf, it } from "vitest";

import type { RunCommand } from "../api/types";
import type {
  RecipeCommand,
  RecipeCommandFor,
  RecipeId,
  RecipeParameters,
  RecipeParametersAt,
} from "./types";

describe("tipos de receita derivados de POST /runs", () => {
  it("RecipeId é a união dos recipe_id da spec", () => {
    expectTypeOf<RecipeId>().toEqualTypeOf<RunCommand["recipe_id"]>();
  });

  it("RecipeCommand tira as entradas e mantém a variante", () => {
    expectTypeOf<RecipeCommand>().not.toHaveProperty("input_upload_ids");
    expectTypeOf<RecipeCommandFor<"csv-janitor">["recipe_version"]>().toEqualTypeOf<1 | 2>();
  });

  it("RecipeParameters é o objeto de parâmetros da variante", () => {
    expectTypeOf<RecipeParameters<"csv-janitor">>().toEqualTypeOf<
      Extract<RunCommand, { recipe_id: "csv-janitor" }>["parameters"]
    >();
  });

  it("RecipeParametersAt separa os parâmetros de cada versão", () => {
    expectTypeOf<RecipeParametersAt<"csv-janitor", 2>>().toHaveProperty("input_encoding");
    expectTypeOf<RecipeParametersAt<"csv-janitor", 1>>().not.toHaveProperty("input_encoding");
  });

  it("um comando de receita mais as entradas é um RunCommand", () => {
    expectTypeOf<RecipeCommand & { input_upload_ids: string[] }>().toExtend<RunCommand>();
  });
});
