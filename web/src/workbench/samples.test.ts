/// <reference types="node" />
// O tamanho de cada amostra vem do disco: o Vitest roda no Node, e o `?url` só devolve o caminho.
import { statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { inputLimitsOf } from "../catalog/inputLimits";
import { RECIPE_LIST, recipeOf } from "../recipes/registry";
import type { RecipeId } from "../recipes/types";
import { CATALOG_BODY } from "../test/catalogBody";
import { loadSample, SAMPLES } from "./samples";

// web/, a raiz do Vite: o `?url` devolve o caminho a partir dela (`/src/...?no-inline`).
const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function sizeOf(url: string): number {
  return statSync(join(WEB_ROOT, url.split("?")[0] ?? url)).size;
}

function urlOf(input: RequestInfo | URL): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

function catalogLimits(recipeId: RecipeId) {
  const version = CATALOG_BODY.recipes.find((recipe) => recipe.recipe_id === recipeId)?.versions[0];
  if (version === undefined) {
    throw new Error(`Sem versão no catálogo para ${recipeId}`);
  }
  const limits = inputLimitsOf(version);
  if (limits === null) {
    throw new Error(`Limites fora do formato para ${recipeId}`);
  }
  // O CSV Janitor recusa tabela acima de `table_bytes` no executor (D8): a amostra cabe nele também.
  const table = "table_bytes" in version.limits ? version.limits.table_bytes : null;
  return { ...limits, tableBytes: table };
}

/** O nome passa no `accept` do input (extensão ou tipo MIME), como a janela de arquivos filtra. */
function accepted(accept: string | null, name: string, type: string): boolean {
  if (accept === null) {
    return true;
  }
  const lower = name.toLowerCase();
  return accept
    .split(",")
    .some((token) => (token.startsWith(".") ? lower.endsWith(token) : token === type));
}

describe("amostras da Bancada", () => {
  it("toda receita do registro tem amostra", () => {
    expect(Object.keys(SAMPLES).sort()).toEqual(RECIPE_LIST.map((recipe) => recipe.id).sort());
    for (const files of Object.values(SAMPLES)) {
      expect(files.length).toBeGreaterThan(0);
    }
  });

  it.each(RECIPE_LIST.map((recipe) => recipe.id))(
    "a amostra de %s cabe nos limites da receita",
    (recipeId) => {
      const files = SAMPLES[recipeId];
      const limits = catalogLimits(recipeId);
      const sizes = files.map((file) => sizeOf(file.url));

      expect(files.length).toBeLessThanOrEqual(limits.maxInputs);
      for (const size of sizes) {
        expect(size).toBeGreaterThan(0);
        expect(size).toBeLessThanOrEqual(200 * 1024);
        expect(size).toBeLessThanOrEqual(limits.maxFileBytes ?? Infinity);
        expect(size).toBeLessThanOrEqual(limits.tableBytes ?? Infinity);
      }
      expect(sizes.reduce((total, size) => total + size, 0)).toBeLessThanOrEqual(
        limits.maxTotalBytes ?? Infinity,
      );
      for (const file of files) {
        expect(accepted(recipeOf(recipeId).accept, file.name, file.type)).toBe(true);
      }
    },
  );

  it("loadSample devolve File com nome e tipo", async () => {
    const requested: string[] = [];
    const fetcher: typeof fetch = (input) => {
      requested.push(urlOf(input));
      return Promise.resolve(new Response("a,b\n1,2\n"));
    };

    const files = await loadSample("csv-janitor", fetcher);

    expect(requested).toEqual(SAMPLES["csv-janitor"].map((file) => file.url));
    expect(files.map((file) => [file.name, file.type])).toEqual([
      ["exemplo-clientes.csv", "text/csv"],
    ]);
    expect(files[0]).toBeInstanceOf(File);
    expect(await files[0]?.text()).toBe("a,b\n1,2\n");
  });

  it("loadSample falha quando a amostra não vem", async () => {
    const fetcher: typeof fetch = () => Promise.resolve(new Response("", { status: 404 }));

    await expect(loadSample("image-forge", fetcher)).rejects.toThrow();
  });
});
