import { describe, expect, it } from "vitest";

import { catalogVersion } from "../test/catalog";
import { findRecipe, RECIPE_LIST } from "./registry";

describe("findRecipe", () => {
  it.each([
    ["csv-janitor", "CSV Janitor"],
    ["image-forge", "Image Forge"],
    ["downloads-detox", "Downloads Detox"],
    ["pdf-press", "PDF Press"],
    ["media-mill", "Media Mill"],
  ] as const)("encontra %s", (id, title) => {
    expect(findRecipe(id)?.title).toBe(title);
  });

  it.each(["receita-inexistente", "", "__proto__", "toString", "constructor"])("não encontra %j", (id) => {
    expect(findRecipe(id)).toBeNull();
  });

  it("Media Mill aceita só os contêineres de vídeo da allowlist", () => {
    expect(findRecipe("media-mill")?.accept).toBe(
      ".mp4,.mov,.webm,video/mp4,video/quicktime,video/webm",
    );
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

    expect(form?.inputLimits).toEqual({
      maxInputs: 100,
      maxTotalBytes: 503316480,
      maxFileBytes: 33554432,
    });
  });

  it.each([
    ["sem max_inputs", { limits: { columns: 100 } }],
    ["defaults de outra receita", { defaults: catalogVersion("image-forge").defaults }],
  ])("catálogo incompatível (%s) → sem formulário", (_, override) => {
    const recipe = findRecipe("csv-janitor");

    expect(recipe?.formFor({ ...catalogVersion("csv-janitor"), ...override })).toBeNull();
  });
});
