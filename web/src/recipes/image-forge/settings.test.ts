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
      fit: "contain",
      scale_percent: "100",
      webp_quality: "80",
      webp_lossless: false,
    });
  });

  it("os valores dos controles desabilitados são os defaults do schema", () => {
    const settings = imageForgeSettings({
      ...version,
      defaults: { ...version.defaults, max_width: 1080, webp_quality: 90 },
    });

    expect(settings?.disabledValues).toEqual({
      max_width: "1600",
      max_height: "1600",
      jpeg_quality: "85",
      jpeg_background: "#FFFFFF",
      scale_percent: "100",
      webp_quality: "80",
      webp_lossless: false,
    });
  });

  it("faixas do schema e caixa da miniatura dos limites", () => {
    expect(imageForgeSettings(version)?.limits).toEqual({
      width: { min: 16, max: 4096 },
      height: { min: 16, max: 4096 },
      quality: { min: 60, max: 95 },
      scale: { min: 1, max: 100 },
      webpQuality: { min: 60, max: 95 },
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
          max_width: { minimum: 32, maximum: 2048, default: 1600 },
        },
      },
    });

    expect(settings?.defaults).toMatchObject({ max_width: "800", output_format: "png" });
    expect(settings?.limits).toMatchObject({
      width: { min: 32, max: 2048 },
      height: { min: 16, max: 4096 },
    });
  });

  it("settings devolve null quando o catálogo não traz fit", () => {
    expect(imageForgeSettings(catalogVersion("image-forge", 1))).toBeNull();
  });

  it.each([
    ["formato desconhecido", { defaults: { ...version.defaults, output_format: "avif" } }],
    ["modo de ajuste desconhecido", { defaults: { ...version.defaults, fit: "zoom" } }],
    [
      "schema sem default do lado",
      {
        parameters_schema: {
          properties: {
            ...version.parameters_schema.properties,
            max_width: { minimum: 16, maximum: 4096 },
          },
        },
      },
    ],
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
