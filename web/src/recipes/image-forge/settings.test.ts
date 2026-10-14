import { describe, expect, it } from "vitest";

import { catalogVersion } from "../../test/catalog";
import { imageForgeSettings } from "./settings";

const version = catalogVersion("image-forge");

describe("imageForgeSettings", () => {
  it("defaults do catálogo viram os valores iniciais do formulário", () => {
    expect(imageForgeSettings(version)?.defaults).toEqual({
      max_width: "1600",
      max_height: "1600",
      output_format: "preserve",
      jpeg_quality: "85",
      jpeg_background: "#FFFFFF",
      make_thumbnails: false,
    });
  });

  it("faixas do schema e caixa da miniatura dos limites", () => {
    expect(imageForgeSettings(version)?.limits).toEqual({
      width: { min: 16, max: 4096 },
      height: { min: 16, max: 4096 },
      quality: { min: 60, max: 95 },
      thumbnailBox: 320,
    });
  });

  it("os valores seguem o catálogo", () => {
    const settings = imageForgeSettings({
      ...version,
      defaults: { ...version.defaults, max_width: 800, output_format: "png" },
      parameters_schema: {
        properties: {
          ...version.parameters_schema.properties,
          max_width: { minimum: 32, maximum: 2048 },
        },
      },
    });

    expect(settings?.defaults).toMatchObject({ max_width: "800", output_format: "png" });
    expect(settings?.limits).toMatchObject({
      width: { min: 32, max: 2048 },
      height: { min: 16, max: 4096 },
    });
  });

  it.each([
    ["formato desconhecido", { defaults: { ...version.defaults, output_format: "webp" } }],
    ["default ausente", { defaults: { max_width: 1600 } }],
    ["sem caixa da miniatura", { limits: { max_inputs: 100 } }],
    ["schema sem faixa", { parameters_schema: { properties: {} } }],
    [
      "faixa invertida",
      {
        parameters_schema: {
          properties: {
            ...version.parameters_schema.properties,
            jpeg_quality: { minimum: 95, maximum: 60 },
          },
        },
      },
    ],
  ])("catálogo incompatível (%s) → null", (_, override) => {
    expect(imageForgeSettings({ ...version, ...override })).toBeNull();
  });
});
