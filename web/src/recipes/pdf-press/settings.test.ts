import { describe, expect, it } from "vitest";

import { catalogVersion } from "../../test/catalog";
import { pdfPressSettings } from "./settings";

const version = catalogVersion("pdf-press");

describe("pdfPressSettings", () => {
  it("defaults e tetos vêm do catálogo", () => {
    expect(pdfPressSettings(version)).toEqual({
      defaults: { operation: "merge", pages_per_document: "1", cards: [] },
      limits: { maxPages: 200, maxSelectedPages: 400 },
    });
  });

  it.each([
    ["operação desconhecida", { defaults: { ...version.defaults, operation: "rotate" } }],
    ["seleção já preenchida", { defaults: { ...version.defaults, select: [] } }],
    ["schema sem máximo do grupo", { parameters_schema: { properties: {} } }],
  ])("catálogo incompatível (%s) → null", (_, override) => {
    expect(pdfPressSettings({ ...version, ...override })).toBeNull();
  });
});
