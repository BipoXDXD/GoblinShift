import { describe, expect, expectTypeOf, it } from "vitest";

import type { RecipeDraft, RecipeParameters } from "../types";
import type { OUTPUT_FORMATS } from "./schema";
import {
  imageForgeFieldId,
  parseImageForge,
  type ImageForgeFormValues,
  type ImageForgeSettings,
} from "./schema";

const SETTINGS: ImageForgeSettings = {
  defaults: {
    max_width: "1600",
    max_height: "1600",
    output_format: "preserve",
    jpeg_quality: "85",
    jpeg_background: "#FFFFFF",
    make_thumbnails: false,
  },
  limits: {
    width: { min: 16, max: 4096 },
    height: { min: 16, max: 4096 },
    quality: { min: 60, max: 95 },
    thumbnailBox: 320,
  },
};

function values(overrides: Partial<ImageForgeFormValues> = {}): ImageForgeFormValues {
  return { ...SETTINGS.defaults, ...overrides };
}

function parametersOf(draft: RecipeDraft<"image-forge">) {
  if (!draft.ok) {
    throw new Error(`rascunho inválido: ${JSON.stringify(draft.issues)}`);
  }
  return draft.command.parameters;
}

function issuesOf(draft: RecipeDraft<"image-forge">) {
  if (draft.ok) {
    throw new Error("rascunho deveria ser inválido");
  }
  return draft.issues;
}

describe("constantes espelham os tipos gerados", () => {
  it("formatos de saída", () => {
    expectTypeOf<(typeof OUTPUT_FORMATS)[number]>().toEqualTypeOf<
      NonNullable<RecipeParameters<"image-forge">["output_format"]>
    >();
  });
});

describe("parseImageForge", () => {
  it("os defaults viram um comando válido e explícito", () => {
    expect(parseImageForge(values(), SETTINGS)).toEqual({
      ok: true,
      command: {
        recipe_id: "image-forge",
        recipe_version: 1,
        parameters: {
          max_width: 1600,
          max_height: 1600,
          output_format: "preserve",
          jpeg_quality: 85,
          jpeg_background: "#FFFFFF",
          make_thumbnails: false,
        },
      },
    });
  });

  it.each([
    ["mínimo", "16", true],
    ["abaixo do mínimo", "15", false],
    ["máximo", "4096", true],
    ["acima do máximo", "4097", false],
    ["vazio", "", false],
    ["fração", "100.5", false],
    ["texto", "abc", false],
    ["negativo", "-20", false],
  ])("largura %s (%s)", (_, maxWidth, valid) => {
    expect(parseImageForge(values({ max_width: maxWidth }), SETTINGS).ok).toBe(valid);
  });

  it("largura e altura seguem cada uma a própria faixa", () => {
    const settings = { ...SETTINGS, limits: { ...SETTINGS.limits, height: { min: 1, max: 10 } } };

    expect(parseImageForge(values({ max_width: "20", max_height: "10" }), settings).ok).toBe(true);
    expect(parseImageForge(values({ max_width: "20", max_height: "11" }), settings).ok).toBe(false);
  });

  it("número com espaços nas pontas vale", () => {
    expect(
      parametersOf(parseImageForge(values({ max_height: " 800 " }), SETTINGS)).max_height,
    ).toBe(800);
  });

  it("lado fora da faixa aponta o campo com a faixa do catálogo", () => {
    expect(issuesOf(parseImageForge(values({ max_height: "9000" }), SETTINGS))).toEqual([
      {
        fieldId: "image-forge-max_height",
        message: "Informe um número inteiro de 16 a 4096.",
      },
    ]);
  });

  it.each([
    ["59", false],
    ["60", true],
    ["95", true],
    ["96", false],
  ])("qualidade JPEG %s", (quality, valid) => {
    expect(
      parseImageForge(values({ output_format: "jpeg", jpeg_quality: quality }), SETTINGS).ok,
    ).toBe(valid);
  });

  it.each(["#ffffff", "#00AA11"])("cor de fundo %s vale, em maiúsculas", (color) => {
    expect(
      parametersOf(parseImageForge(values({ jpeg_background: color }), SETTINGS)).jpeg_background,
    ).toBe(color.toUpperCase());
  });

  it.each(["", "fff", "#fff", "#GGGGGG", "#0000000", " #000000"])(
    "cor de fundo %j é recusada no campo",
    (color) => {
      expect(issuesOf(parseImageForge(values({ jpeg_background: color }), SETTINGS))).toEqual([
        { fieldId: "image-forge-jpeg_background", message: "Use o formato #RRGGBB, como #FFFFFF." },
      ]);
    },
  );

  it("com saída PNG, as opções de JPEG vão com os defaults do catálogo, sem erro", () => {
    const parameters = parametersOf(
      parseImageForge(
        values({ output_format: "png", jpeg_quality: "abc", jpeg_background: "#000000" }),
        SETTINGS,
      ),
    );

    expect(parameters).toMatchObject({
      output_format: "png",
      jpeg_quality: 85,
      jpeg_background: "#FFFFFF",
    });
  });

  it("miniaturas ligadas", () => {
    expect(
      parametersOf(parseImageForge(values({ make_thumbnails: true }), SETTINGS)).make_thumbnails,
    ).toBe(true);
  });

  it("vários erros de uma vez, um por campo", () => {
    const issues = issuesOf(
      parseImageForge(values({ max_width: "0", max_height: "x", jpeg_quality: "1" }), SETTINGS),
    );

    expect(issues.map((issue) => issue.fieldId)).toEqual([
      "image-forge-max_width",
      "image-forge-max_height",
      "image-forge-jpeg_quality",
    ]);
  });
});

describe("imageForgeFieldId", () => {
  it.each([
    [["max_width"], "image-forge-max_width"],
    [["jpeg_background"], "image-forge-jpeg_background"],
    [["make_thumbnails"], "image-forge-make_thumbnails"],
  ])("%j → %s", (path, expected) => {
    expect(imageForgeFieldId(path)).toBe(expected);
  });

  it.each([[[]], [["recipe_version"]], [["__proto__"]], [["toString"]]])(
    "caminho sem campo %j → null",
    (path) => {
      expect(imageForgeFieldId(path)).toBeNull();
    },
  );
});
