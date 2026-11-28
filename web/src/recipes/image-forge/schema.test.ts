import { describe, expect, expectTypeOf, it } from "vitest";

import type { RecipeDraft, RecipeParameters } from "../types";
import type { FIT_MODES, OUTPUT_FORMATS } from "./schema";
import {
  imageForgeField,
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
    fit: "contain",
    scale_percent: "100",
    webp_quality: "80",
    webp_lossless: false,
  },
  disabledValues: {
    max_width: "1600",
    max_height: "1600",
    jpeg_quality: "85",
    jpeg_background: "#FFFFFF",
    scale_percent: "100",
    webp_quality: "80",
    webp_lossless: false,
  },
  limits: {
    width: { min: 16, max: 4096 },
    height: { min: 16, max: 4096 },
    quality: { min: 60, max: 95 },
    scale: { min: 1, max: 100 },
    webpQuality: { min: 60, max: 95 },
    thumbnailBox: 320,
  },
};

const V2_DEFAULTS = {
  max_width: 1600,
  max_height: 1600,
  output_format: "preserve",
  jpeg_quality: 85,
  jpeg_background: "#FFFFFF",
  make_thumbnails: false,
  fit: "contain",
  scale_percent: 100,
  webp_quality: 80,
  webp_lossless: false,
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

  it("modos de ajuste", () => {
    expectTypeOf<(typeof FIT_MODES)[number]>().toEqualTypeOf<
      NonNullable<RecipeParameters<"image-forge">["fit"]>
    >();
  });
});

describe("parseImageForge", () => {
  it("produz o comando da versão 2 com os defaults do catálogo", () => {
    expect(parseImageForge(SETTINGS.defaults, SETTINGS)).toEqual({
      ok: true,
      command: { recipe_id: "image-forge", recipe_version: 2, parameters: V2_DEFAULTS },
    });
  });

  it("em escala, largura e altura saem no default do catálogo, sem erro", () => {
    const parameters = parametersOf(
      parseImageForge(
        values({ fit: "scale", scale_percent: "50", max_width: "abc", max_height: "800" }),
        SETTINGS,
      ),
    );

    expect(parameters).toMatchObject({
      fit: "scale",
      scale_percent: 50,
      max_width: 1600,
      max_height: 1600,
    });
  });

  it("fora de escala, a escala sai no default do catálogo, sem erro", () => {
    const parameters = parametersOf(
      parseImageForge(values({ fit: "cover", scale_percent: "0" }), SETTINGS),
    );

    expect(parameters).toMatchObject({ fit: "cover", scale_percent: 100 });
  });

  it("o default enviado é o do schema, não o valor que abriu o formulário", () => {
    const settings = { ...SETTINGS, defaults: values({ max_width: "1080", jpeg_quality: "70" }) };

    expect(
      parametersOf(parseImageForge(values({ fit: "scale", output_format: "png" }), settings)),
    ).toMatchObject({ max_width: 1600, jpeg_quality: 85 });
  });

  it.each([
    ["1", true],
    ["100", true],
    ["0", false],
    ["101", false],
  ])("escala %s", (scale, valid) => {
    expect(parseImageForge(values({ fit: "scale", scale_percent: scale }), SETTINGS).ok).toBe(
      valid,
    );
  });

  it("escala fora de 1 a 100 aponta o campo", () => {
    expect(
      issuesOf(parseImageForge(values({ fit: "scale", scale_percent: "150" }), SETTINGS)),
    ).toEqual([
      {
        fieldId: "image-forge-scale_percent",
        label: "Escala, em %",
        message: "Informe um número inteiro de 1 a 100.",
      },
    ]);
  });

  it.each(["jpeg", "png"] as const)(
    "com saída %s, as opções de WebP saem nos defaults do catálogo",
    (format) => {
      const parameters = parametersOf(
        parseImageForge(
          values({ output_format: format, webp_quality: "x", webp_lossless: true }),
          SETTINGS,
        ),
      );

      expect(parameters).toMatchObject({ webp_quality: 80, webp_lossless: false });
    },
  );

  it.each(["webp", "preserve"] as const)("com saída %s, as opções de WebP valem", (format) => {
    const parameters = parametersOf(
      parseImageForge(values({ output_format: format, webp_quality: "90" }), SETTINGS),
    );

    expect(parameters).toMatchObject({ webp_quality: 90, webp_lossless: false });
  });

  it("WebP sem perdas manda a qualidade do WebP no default", () => {
    const parameters = parametersOf(
      parseImageForge(
        values({ output_format: "webp", webp_lossless: true, webp_quality: "61" }),
        SETTINGS,
      ),
    );

    expect(parameters).toMatchObject({ webp_lossless: true, webp_quality: 80 });
  });

  it("qualidade do WebP fora da faixa aponta o campo", () => {
    expect(issuesOf(parseImageForge(values({ webp_quality: "96" }), SETTINGS))).toEqual([
      {
        fieldId: "image-forge-webp_quality",
        label: "Qualidade do WebP",
        message: "Informe um número inteiro de 60 a 95.",
      },
    ]);
  });

  it("com saída WebP, as opções de JPEG saem nos defaults do catálogo", () => {
    const parameters = parametersOf(
      parseImageForge(values({ output_format: "webp", jpeg_quality: "abc" }), SETTINGS),
    );

    expect(parameters).toMatchObject({ jpeg_quality: 85, jpeg_background: "#FFFFFF" });
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
        label: "Altura máxima, em pixels",
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
        {
          fieldId: "image-forge-jpeg_background",
          label: "Cor de fundo da transparência no JPEG",
          message: "Use o formato #RRGGBB, como #FFFFFF.",
        },
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

describe("imageForgeField", () => {
  it.each([
    [["max_width"], "image-forge-max_width", "Largura máxima, em pixels"],
    [["jpeg_background"], "image-forge-jpeg_background", "Cor de fundo da transparência no JPEG"],
    [["make_thumbnails"], "image-forge-make_thumbnails", "Gerar miniaturas"],
    [["fit"], "image-forge-fit", "Ajuste"],
    [["scale_percent"], "image-forge-scale_percent", "Escala, em %"],
    [["webp_quality"], "image-forge-webp_quality", "Qualidade do WebP"],
    [["webp_lossless"], "image-forge-webp_lossless", "WebP sem perdas"],
  ])("%j → %s (%s)", (path, fieldId, label) => {
    expect(imageForgeField(path)).toEqual({ fieldId, label });
  });

  it.each([[[]], [["recipe_version"]], [["__proto__"]], [["toString"]]])(
    "caminho sem campo %j → null",
    (path) => {
      expect(imageForgeField(path)).toBeNull();
    },
  );
});
