import { describe, expect, it } from "vitest";

import { catalogVersion } from "../../test/catalog";
import { downloadsDetoxSettings } from "./settings";

const version = catalogVersion("downloads-detox");

describe("downloadsDetoxSettings", () => {
  it("defaults e máximo do prefixo vêm do catálogo", () => {
    expect(downloadsDetoxSettings(version)).toEqual({
      defaults: { deduplicate: true, group_by: "kind", name_mode: "preserve", name_prefix: "file" },
      limits: { maxPrefixChars: 40 },
    });
  });

  it("os valores seguem o catálogo", () => {
    const settings = downloadsDetoxSettings({
      ...version,
      defaults: { ...version.defaults, group_by: "none", name_prefix: "doc" },
      parameters_schema: { properties: { name_prefix: { maxLength: 12 } } },
    });

    expect(settings?.defaults).toMatchObject({ group_by: "none", name_prefix: "doc" });
    expect(settings?.limits).toEqual({ maxPrefixChars: 12 });
  });

  it.each([
    ["agrupamento desconhecido", { defaults: { ...version.defaults, group_by: "date" } }],
    ["default ausente", { defaults: { deduplicate: true } }],
    ["schema sem maxLength", { parameters_schema: { properties: { name_prefix: {} } } }],
    ["maxLength zero", { parameters_schema: { properties: { name_prefix: { maxLength: 0 } } } }],
  ])("catálogo incompatível (%s) → null", (_, override) => {
    expect(downloadsDetoxSettings({ ...version, ...override })).toBeNull();
  });
});
