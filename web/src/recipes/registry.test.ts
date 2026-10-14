import { describe, expect, it } from "vitest";

import { findRecipe } from "./registry";

describe("findRecipe", () => {
  it("encontra a CSV Janitor", () => {
    const recipe = findRecipe("csv-janitor");

    expect(recipe?.title).toBe("CSV Janitor");
    expect(recipe?.initialDraft.ok).toBe(true);
  });

  it.each(["image-forge", "", "__proto__", "toString", "constructor"])("não encontra %j", (id) => {
    expect(findRecipe(id)).toBeNull();
  });

  it("traduz o caminho do servidor para o campo da receita", () => {
    expect(findRecipe("csv-janitor")?.fieldIdFor(["filters", "0", "value"])).toBe(
      "csv-janitor-filters-0-value",
    );
  });
});
