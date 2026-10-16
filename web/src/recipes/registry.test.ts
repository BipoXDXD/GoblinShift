import { describe, expect, it } from "vitest";

import { catalogVersion } from "../test/catalog";
import {
  findRecipe,
  RECIPE_LIST,
  withoutUploadBoundParameters,
  type RecipeModule,
} from "./registry";

describe("findRecipe", () => {
  it.each([
    ["csv-janitor", "CSV Janitor"],
    ["image-forge", "Image Forge"],
    ["downloads-detox", "Downloads Detox"],
    ["pdf-press", "PDF Press"],
  ] as const)("encontra %s", (id, title) => {
    expect(findRecipe(id)?.title).toBe(title);
  });

  it.each(["media-mill", "", "__proto__", "toString", "constructor"])("não encontra %j", (id) => {
    expect(findRecipe(id)).toBeNull();
  });

  it("traduz o caminho do servidor para o campo da receita", () => {
    expect(findRecipe("csv-janitor")?.fieldIdFor(["filters", "0", "value"])).toBe(
      "csv-janitor-filters-0-value",
    );
  });
});

describe("formFor", () => {
  it.each(RECIPE_LIST.map((recipe) => [recipe.id, recipe] as const))(
    "%s: os defaults do catálogo já formam um comando válido",
    (id, recipe) => {
      const form = recipe.formFor(catalogVersion(id));

      expect(form?.initialDraft).toMatchObject({
        ok: true,
        command: { recipe_id: id, recipe_version: recipe.version },
      });
    },
  );

  it("traz os limites de entrada da versão", () => {
    const form = findRecipe("downloads-detox")?.formFor(catalogVersion("downloads-detox"));

    expect(form?.inputLimits).toEqual({ maxInputs: 100, maxTotalBytes: 503316480 });
  });

  it.each([
    ["sem max_inputs", { limits: { columns: 100 } }],
    ["defaults de outra receita", { defaults: catalogVersion("image-forge").defaults }],
  ])("catálogo incompatível (%s) → sem formulário", (_, override) => {
    const recipe = findRecipe("csv-janitor");

    expect(recipe?.formFor({ ...catalogVersion("csv-janitor"), ...override })).toBeNull();
  });
});

function recipeOf(id: string): RecipeModule {
  const recipe = findRecipe(id);
  if (recipe === null) {
    throw new Error(`receita ${id} ausente do registro`);
  }
  return recipe;
}

describe("withoutUploadBoundParameters", () => {
  const page = { upload_id: "0b9f6c1e-3a2d-4e5f-8a7b-6c5d4e3f2a1b", page_number: 1 };

  it("tira o select do PDF Press e avisa que descartou", () => {
    expect(
      withoutUploadBoundParameters(recipeOf("pdf-press"), { operation: "select", select: [page] }),
    ).toEqual({ parameters: { operation: "select" }, dropped: true });
  });

  it.each([
    ["select nulo", { operation: "merge", select: null }, { operation: "merge" }],
    ["sem select", { operation: "split" }, { operation: "split" }],
  ])("PDF Press com %s não avisa", (_case, parameters, expected) => {
    expect(withoutUploadBoundParameters(recipeOf("pdf-press"), parameters)).toEqual({
      parameters: expected,
      dropped: false,
    });
  });

  it("receita sem parâmetros ligados a uploads fica igual, até com um campo chamado select", () => {
    const parameters = { output_delimiter: ";", select: [page] };

    expect(withoutUploadBoundParameters(recipeOf("csv-janitor"), parameters)).toEqual({
      parameters,
      dropped: false,
    });
  });
});
