import { describe, expect, expectTypeOf, it } from "vitest";

import type { components } from "../api/schema";
import { ApiError } from "../api/response";
import { catalogHandler } from "../test/catalog";
import { fakeApi, jsonResponse } from "../test/fakeApi";
import { findCatalogVersion, getCatalog, type Catalog } from "./api";

describe("tipos espelham a spec", () => {
  it("Catalog", () => {
    expectTypeOf<Catalog>().toEqualTypeOf<components["schemas"]["CatalogResponse"]>();
  });
});

describe("getCatalog", () => {
  it("lê o catálogo da API", async () => {
    const { client, requests } = fakeApi({ "GET /api/v1/catalog": catalogHandler() });

    const catalog = await getCatalog(client);

    expect(catalog.recipes.map((recipe) => recipe.recipe_id)).toEqual([
      "csv-janitor",
      "image-forge",
      "downloads-detox",
      "pdf-press",
    ]);
    expect(requests[0]?.method).toBe("GET");
  });

  it("recusa catálogo fora do formato", async () => {
    const { client } = fakeApi({
      "GET /api/v1/catalog": () => jsonResponse({ recipes: [{ recipe_id: "csv-janitor" }] }),
    });

    await expect(getCatalog(client)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("findCatalogVersion", () => {
  const loadCatalog = () => getCatalog(fakeApi({ "GET /api/v1/catalog": catalogHandler() }).client);

  it("encontra a versão da receita", async () => {
    const catalog = await loadCatalog();

    expect(findCatalogVersion(catalog, "image-forge", 1)?.limits["max_inputs"]).toBe(100);
  });

  it.each([
    ["receita ausente", "media-mill", 1],
    ["versão ausente", "image-forge", 2],
  ])("%s → null", async (_, recipeId, version) => {
    const catalog = await loadCatalog();
    expect(findCatalogVersion(catalog, recipeId, version)).toBeNull();
  });
});
