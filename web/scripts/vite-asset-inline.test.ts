import { describe, expect, it } from "vitest";

import viteConfig from "../vite.config";
import { assetsInlineLimit } from "./vite-asset-inline";

describe("assetsInlineLimit", () => {
  it.each([
    "/repo/web/src/assets/art/marca/rubia-escudo-48h.avif",
    "/repo/web/src/assets/art/goblins/jade-256w.webp",
    "C:\\repo\\web\\src\\assets\\art\\marca\\rubia-escudo-48h.webp",
  ])("nunca embute a arte como data: URL: %s", (path) => {
    expect(assetsInlineLimit(path)).toBe(false);
  });

  it("deixa o resto com o limite padrão do Vite", () => {
    expect(assetsInlineLimit("/repo/web/src/assets/icons/seta.svg")).toBeUndefined();
  });

  it("o build usa a regra: a vitrine acha a arte pelo nome do arquivo", () => {
    expect(viteConfig.build?.assetsInlineLimit).toBe(assetsInlineLimit);
  });
});
