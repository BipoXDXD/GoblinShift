import { describe, expectTypeOf, it } from "vitest";

import type { RunCommand } from "../api/types";
import type { RecipeCommand, RecipeCommandFor, RecipeId, RecipeParameters } from "./types";

describe("tipos de receita derivados de POST /runs", () => {
  it("RecipeId é a união dos recipe_id da spec", () => {
    expectTypeOf<RecipeId>().toEqualTypeOf<RunCommand["recipe_id"]>();
  });

  it("RecipeCommand tira as entradas e mantém a variante", () => {
    expectTypeOf<RecipeCommand>().not.toHaveProperty("input_upload_ids");
    expectTypeOf<RecipeCommandFor<"csv-janitor">["recipe_version"]>().toEqualTypeOf<1>();
  });

  it("RecipeParameters é o objeto de parâmetros da variante", () => {
    expectTypeOf<RecipeParameters<"csv-janitor">>().toEqualTypeOf<
      Extract<RunCommand, { recipe_id: "csv-janitor" }>["parameters"]
    >();
  });

  it("um comando de receita mais as entradas é um RunCommand", () => {
    expectTypeOf<RecipeCommand & { input_upload_ids: string[] }>().toExtend<RunCommand>();
  });
});
