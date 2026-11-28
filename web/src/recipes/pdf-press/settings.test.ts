import { describe, expect, it } from "vitest";

import { catalogVersion } from "../../test/catalog";
import { pdfPressSettings } from "./settings";

const version = catalogVersion("pdf-press");

describe("pdfPressSettings", () => {
  it("defaults e tetos da v2 vêm do catálogo", () => {
    expect(version.version).toBe(2);
    expect(pdfPressSettings(version)).toEqual({
      defaults: {
        operation: "merge",
        pages_per_document: "1",
        rotation: 0,
        compression: "none",
        strip_metadata: true,
        cards: [],
      },
      limits: { maxPages: 200, maxRanges: 400, maxOutputPages: 400 },
    });
  });

  it("preset v1 sobre os defaults da v2 abre com as opções novas no default", () => {
    const v1Preset = { operation: "split", pages_per_document: 5 };

    expect(
      pdfPressSettings({ ...version, defaults: { ...version.defaults, ...v1Preset } })?.defaults,
    ).toEqual({
      operation: "split",
      pages_per_document: "5",
      rotation: 0,
      compression: "none",
      strip_metadata: true,
      cards: [],
    });
  });

  it.each([
    ["operação desconhecida", { defaults: { ...version.defaults, operation: "rotate" } }],
    ["seleção já preenchida", { defaults: { ...version.defaults, select: [] } }],
    ["rotação fora dos ângulos", { defaults: { ...version.defaults, rotation: 45 } }],
    ["compressão desconhecida", { defaults: { ...version.defaults, compression: "lossy" } }],
    [
      "catálogo da v1, sem as opções da v2",
      { defaults: { operation: "merge", pages_per_document: 1, select: null } },
    ],
    ["schema sem máximo do grupo", { parameters_schema: { properties: {} } }],
    ["limites sem o teto de saída", { limits: {} }],
  ])("catálogo incompatível (%s) → null", (_, override) => {
    expect(pdfPressSettings({ ...version, ...override })).toBeNull();
  });
});
